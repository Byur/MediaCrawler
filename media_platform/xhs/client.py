# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/client.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import json
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union
from urllib.parse import quote

import httpx
from playwright.async_api import BrowserContext, Page
from tenacity import (
    RetryError,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_fixed,
)
from tools.httpx_util import make_async_client

import config
from base.base_crawler import AbstractApiClient
from proxy.proxy_mixin import ProxyRefreshMixin
from tools import utils
from tools.humanize import polite_sleep

if TYPE_CHECKING:
    from proxy.proxy_ip_pool import ProxyIpPool

from .browser_profile import BrowserProfile, capture_browser_profile, identity_conflicts
from .exception import (
    CaptchaError,
    DataFetchError,
    IPBlockError,
    NoteNotFoundError,
    PlatformAccessError,
)
from .extractor import XiaoHongShuExtractor
from .field import SearchNoteType, SearchSortType
from .help import get_search_id
from .playwright_sign import SignProfile, XhsSigner

# 重试策略：429 与网络抖动重试，验证码/403/401 不重试。
MAX_REQUEST_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 2.0
RETRY_MAX_DELAY_SEC = 60.0


class XiaoHongShuClient(AbstractApiClient, ProxyRefreshMixin):
    """小红书 API 客户端。

    反检测相关的三条约定：

    1. 每次请求都重新签名：重试不会重放同一份 ``x-s/x-t``；
    2. 身份头（UA / UA-CH / Referer）来自真实浏览器，而不是硬编码常量；
    3. 页面类请求（detail HTML / creator HTML）不携带 API 签名头，
       避免"一个文档请求带着 x-s"这种只有爬虫才会出现的组合。
    """

    #: 只在 API 请求里出现的签名头；页面导航请求需要剥离它们
    SIGN_HEADER_KEYS = frozenset(
        {
            "x-s",
            "x-t",
            "x-s-common",
            "x-b3-traceid",
            "x-xray-traceid",
            "x-mns",
            "xy-direction",
            "x-rap-param",
        }
    )

    def __init__(
        self,
        timeout=60,  # If media crawling is enabled, Xiaohongshu long videos need longer timeout
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
        proxy_ip_pool: Optional["ProxyIpPool"] = None,
        browser_profile: Optional[BrowserProfile] = None,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        if config.XHS_INTERNATIONAL:
            self._host = "https://webapi.rednote.com"
            self._domain = "https://www.rednote.com"
        else:
            self._host = "https://edith.xiaohongshu.com"
            self._domain = "https://www.xiaohongshu.com"
        self.cookie_urls = [self._domain]
        self.IP_ERROR_STR = "Network connection error, please check network settings or restart"
        self.IP_ERROR_CODE = 300012
        self.SECURITY_LIMIT_CODE = 300011
        self.NOTE_NOT_FOUND_CODE = -510000
        self.NOTE_ABNORMAL_STR = "Note status abnormal, please check later"
        self.NOTE_ABNORMAL_CODE = -510001
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        self.browser_profile: Optional[BrowserProfile] = browser_profile
        self._extractor = XiaoHongShuExtractor()
        # 签名器有状态（会话序列号），必须在整个客户端生命周期内复用
        self._signer = XhsSigner(self._build_sign_profile(browser_profile))
        # Initialize proxy pool (from ProxyRefreshMixin)
        self.init_proxy_pool(proxy_ip_pool)
        if browser_profile is not None:
            self.apply_browser_profile(browser_profile)

    # ------------------------------------------------------------------ 身份
    @staticmethod
    def _build_sign_profile(profile: Optional[BrowserProfile]) -> SignProfile:
        if profile is None:
            return SignProfile()
        return SignProfile(
            user_agent=profile.user_agent,
            platform=profile.platform,
            b1=profile.b1,
        )

    def apply_browser_profile(self, profile: Optional[BrowserProfile]) -> None:
        """把浏览器采集到的身份写入请求头与签名器。"""
        if profile is None:
            return
        self.browser_profile = profile
        if profile.user_agent:
            self.headers["user-agent"] = profile.user_agent
        # UA-CH 三件套要么一起发，要么都不发：只发 platform 不发 brands 更像脚本。
        if profile.ua_brands:
            self.headers["sec-ch-ua"] = profile.ua_brands
            if profile.ua_platform:
                self.headers["sec-ch-ua-platform"] = profile.ua_platform
            if profile.ua_mobile:
                self.headers["sec-ch-ua-mobile"] = profile.ua_mobile
        if profile.accept_language:
            self.headers["accept-language"] = profile.accept_language
        self._signer.set_profile(self._build_sign_profile(profile))
        utils.logger.info(
            "[XiaoHongShuClient.apply_browser_profile] 已应用浏览器身份: "
            f"platform={profile.platform or 'unknown'}, "
            f"ua={profile.user_agent[:48] or 'unknown'}..., "
            f"real_b1={profile.has_real_b1}, source={profile.source}"
        )
        for conflict in identity_conflicts(profile):
            utils.logger.warning(
                f"[XiaoHongShuClient.apply_browser_profile] UA/UA-CH 一致性风险: {conflict}"
            )
        if not profile.has_real_b1:
            utils.logger.info(
                "[XiaoHongShuClient.apply_browser_profile] 未取到 localStorage['b1']，"
                "x-s-common 将使用合成指纹（同一 a1 的 b1 会随请求变化）"
            )

    async def refresh_browser_profile(self, page: Optional[Page] = None) -> Optional[BrowserProfile]:
        """重新采集浏览器身份（登录后 b1 / UA 可能才真正就绪）。"""
        target = page or self.playwright_page
        if target is None or not hasattr(target, "evaluate"):
            return self.browser_profile
        profile = await capture_browser_profile(
            target,
            fallback_user_agent=self.headers.get("user-agent", ""),
            wait_for_b1_seconds=3.0,
        )
        self.apply_browser_profile(profile)
        return profile

    # ------------------------------------------------------------------ 请求头
    def _endpoint_referer(
        self,
        uri: str,
        params: Optional[Dict] = None,
        payload: Optional[Dict] = None,
    ) -> Optional[str]:
        """按接口返回更接近真实浏览路径的 Referer。"""
        domain = self._domain
        if uri.startswith("/api/sns/web/v1/search/notes"):
            keyword = str((payload or {}).get("keyword") or "")
            if keyword:
                return (
                    f"{domain}/search_result?keyword={quote(keyword, safe='')}"
                    "&source=web_search_result_notes"
                )
            return f"{domain}/search_result"
        if uri.startswith("/api/sns/web/v1/feed"):
            note_id = (payload or {}).get("source_note_id")
            return f"{domain}/explore/{note_id}" if note_id else f"{domain}/"
        if uri.startswith("/api/sns/web/v2/comment"):
            note_id = (params or {}).get("note_id")
            return f"{domain}/explore/{note_id}" if note_id else None
        if uri.startswith("/api/sns/web/v1/user_posted"):
            user_id = (params or {}).get("user_id")
            return f"{domain}/user/profile/{user_id}" if user_id else None
        if uri.startswith("/api/sns/web/v1/user/selfinfo"):
            return f"{domain}/"
        return None

    def _navigation_headers(self, include_cookie: bool = True) -> Dict[str, str]:
        """页面导航请求的请求头：不携带 API 签名头，并改成 document 的 fetch metadata。

        这些请求会返回 HTML 页面（详情页 / 创作者主页），所以 ``sec-fetch-dest``
        应该是 ``document`` 而不是 XHR 的 ``empty``。带着 XHR 的 fetch metadata
        去请求一个 HTML 文档，是服务端很容易识别的不一致。

        同时去掉 ``referer``：这里等价于"地址栏直接打开"，而带 Referer 的导航
        不可能是 ``sec-fetch-site: none``——保留 referer 会和 fetch metadata 打架。
        """
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in self.SIGN_HEADER_KEYS
            # 文档导航不会带 content-type / origin / referer
            and key.lower() not in ("content-type", "origin", "referer")
        }
        headers.update(
            {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
            }
        )
        if not include_cookie:
            headers = {
                key: value for key, value in headers.items() if key.lower() != "cookie"
            }
        return headers

    async def _pre_headers(
        self,
        url: str,
        params: Optional[Dict] = None,
        payload: Optional[Dict] = None,
        referer: Optional[str] = None,
    ) -> Dict:
        """请求头参数签名 (使用 xhshow 纯算法)

        Args:
            url: 请求 URI path
            params: GET 请求参数
            payload: POST 请求参数
            referer: 显式指定 Referer；None 时按接口自动推断

        Returns:
            Dict: 签名后的请求头（新 dict，不修改 self.headers）
        """
        if params is not None:
            data = params
            method = "GET"
        elif payload is not None:
            data = payload
            method = "POST"
        else:
            raise ValueError("params or payload is required")

        signs = self._signer.sign_headers(
            method=method,
            uri=url,
            cookie_str=self.headers.get("Cookie", ""),
            data=data,
        )

        headers = dict(self.headers)
        headers.update(signs)

        resolved_referer = referer or self._endpoint_referer(
            url, params=params, payload=payload
        )
        if resolved_referer:
            headers["referer"] = resolved_referer
        return headers

    # ------------------------------------------------------------------ 请求
    @staticmethod
    def _parse_response_json(response: httpx.Response) -> Optional[Dict]:
        try:
            candidate = response.json()
        except (TypeError, ValueError):
            return None
        return candidate if isinstance(candidate, dict) else None

    async def request(self, method, url, **kwargs) -> Union[str, Any]:
        """
        Wrapper for httpx common request method, processes request response

        单次尝试，不做重试：重试与退避在 ``_fetch`` 中完成，保证每次重试都重新签名。

        Args:
            method: Request method
            url: Request URL
            **kwargs: Other request parameters, such as headers, body, etc.

        Returns:

        """
        # Check if proxy is expired before each request
        await self._refresh_proxy_if_expired()

        return_response = kwargs.pop("return_response", False)

        async with make_async_client(proxy=self.proxy) as client:
            response = await client.request(method, url, timeout=self.timeout, **kwargs)

        if response.status_code in {401, 403, 429}:
            raise PlatformAccessError(
                f"XHS request blocked with HTTP {response.status_code}",
                status_code=response.status_code,
            )

        if response.status_code in (461, 471):
            # 验证码：单独建模，交给上层跳过，不原样重放
            verify_type = response.headers.get("Verifytype", "")
            verify_uuid = response.headers.get("Verifyuuid", "")
            msg = (
                "CAPTCHA appeared, request failed, "
                f"Verifytype: {verify_type}, Verifyuuid: {verify_uuid}, "
                f"Response: {response}"
            )
            utils.logger.error(msg)
            raise CaptchaError(msg, verify_type=verify_type, verify_uuid=verify_uuid)

        response_data = self._parse_response_json(response)

        response_code = (
            str(response_data.get("code"))
            if response_data is not None and response_data.get("code") is not None
            else ""
        )
        if response_code == str(self.IP_ERROR_CODE):
            raise IPBlockError(self.IP_ERROR_STR)
        if response_code == str(self.SECURITY_LIMIT_CODE):
            raise PlatformAccessError(
                f"XHS account security restriction, code: {self.SECURITY_LIMIT_CODE}"
            )

        if return_response:
            return response.text

        data: Dict = response_data if response_data is not None else response.json()
        if data["success"]:
            return data.get("data", data.get("success", {}))
        # IP_ERROR_CODE / SECURITY_LIMIT_CODE are already handled above, before return_response.
        elif data["code"] in (self.NOTE_NOT_FOUND_CODE, self.NOTE_ABNORMAL_CODE):
            raise NoteNotFoundError(f"Note not found or abnormal, code: {data['code']}")
        else:
            err_msg = data.get("msg", None) or f"{response.text}"
            raise DataFetchError(err_msg)

    async def _sleep_backoff(self, attempt: int, reason: str) -> float:
        delay = min(RETRY_MAX_DELAY_SEC, RETRY_BASE_DELAY_SEC * (2 ** max(0, attempt - 1)))
        delay = max(0.0, delay * random.uniform(0.8, 1.2))
        utils.logger.warning(
            f"[XiaoHongShuClient._fetch] 第 {attempt} 次请求失败（{reason}），"
            f"{delay:.1f}s 后重新签名重试"
        )
        if delay > 0:
            await asyncio.sleep(delay)
        return delay

    async def _fetch(
        self,
        method: str,
        uri: str,
        *,
        params: Optional[Dict] = None,
        payload: Optional[Dict] = None,
        referer: Optional[str] = None,
        return_response: bool = False,
    ) -> Union[str, Any]:
        """签名 + 请求 + 退避重试。

        每次尝试都会重新生成签名，避免"同一条 x-s/x-t 被重放 3 次"这种
        真实客户端不会出现的行为。

        验证码（461/471）不在这里重试：它通常意味着当前出口已被标记，
        冷却后原样重放只会加重风控。抛给调用方，由上层跳过该条数据即可。
        """
        method = method.upper()
        attempts = MAX_REQUEST_ATTEMPTS
        last_exc: Optional[BaseException] = None

        for attempt in range(1, attempts + 1):
            headers = await self._pre_headers(
                uri,
                params=params,
                payload=payload,
                referer=referer,
            )

            if method == "GET":
                full_url = (
                    f"{self._host}{uri}?{self._build_query_string(params)}"
                    if params
                    else f"{self._host}{uri}"
                )
                request_kwargs: Dict[str, Any] = {
                    "method": "GET",
                    "url": full_url,
                    "headers": headers,
                }
            else:
                request_kwargs = {
                    "method": "POST",
                    "url": f"{self._host}{uri}",
                    "data": json.dumps(
                        payload or {}, separators=(",", ":"), ensure_ascii=False
                    ),
                    "headers": headers,
                }

            if return_response:
                request_kwargs["return_response"] = True

            try:
                return await self.request(**request_kwargs)
            except (NoteNotFoundError, IPBlockError, CaptchaError):
                # 验证码与"笔记不存在"一样，重试没有意义，直接交给上层决定是否跳过。
                raise
            except PlatformAccessError as exc:
                last_exc = exc
                status_code = getattr(exc, "status_code", None)
                # 只有 429（限流）值得退避重试；401/403 重试只会加重风控
                if status_code == 429 and attempt < attempts:
                    await self._sleep_backoff(attempt, "HTTP 429 rate limited")
                    continue
                raise
            except (DataFetchError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < attempts:
                    await self._sleep_backoff(attempt, type(exc).__name__)
                    continue
                raise

        # 理论上不会走到这里
        if last_exc is not None:
            raise last_exc
        raise DataFetchError(f"{uri} request failed without exception")

    @staticmethod
    def _build_query_string(params: Dict) -> str:
        """Build URL query string with encoding matching browser behavior (commas not encoded)"""
        parts = []
        for key, value in params.items():
            value_str = str(value) if value is not None else ""
            parts.append(f"{key}={quote(value_str, safe=',')}")
        return "&".join(parts)

    async def get(
        self,
        uri: str,
        params: Optional[Dict] = None,
        referer: Optional[str] = None,
    ) -> Dict:
        """
        GET request, signs request headers
        Args:
            uri: Request route
            params: Request parameters
            referer: 显式 Referer（默认按接口推断）

        Returns:

        """
        return await self._fetch(
            "GET",
            uri,
            params=params,
            referer=referer,
        )

    async def post(self, uri: str, data: dict, **kwargs) -> Dict:
        """
        POST request, signs request headers
        Args:
            uri: Request route
            data: Request body parameters

        Returns:

        """
        return await self._fetch(
            "POST",
            uri,
            payload=data,
            referer=kwargs.get("referer"),
            return_response=bool(kwargs.get("return_response", False)),
        )

    async def query_self(self) -> Optional[Dict]:
        """
        Query self user info to check login state

        Returns:
            接口 ``data`` 字段（形如 ``{"result": {...}}``）；请求失败或异常时返回 None。
        """
        uri = "/api/sns/web/v1/user/selfinfo"
        try:
            # 走 _fetch：带签名、429 退避和审计日志。
            # 启动时的一次网络抖动不应被误判成"未登录"而触发登录流程。
            return await self._fetch("GET", uri, params={})
        except httpx.RequestError as exc:
            utils.logger.warning(
                f"[XiaoHongShuClient.query_self] Query self info failed: {exc}"
            )
            return None

    async def pong(self) -> bool:
        """
        Check if login state is still valid by querying self user info
        Returns:
            bool: True if logged in, False otherwise
        """
        utils.logger.info("[XiaoHongShuClient.pong] Begin to check login state...")
        ping_flag = False
        try:
            self_info: Dict = await self.query_self()
            if self_info and self_info.get("result", {}).get("success"):
                ping_flag = True
        except Exception as e:
            utils.logger.error(
                f"[XiaoHongShuClient.pong] Check login state failed: {e}, and try to login again..."
            )
            ping_flag = False
        utils.logger.info(f"[XiaoHongShuClient.pong] Login state result: {ping_flag}")
        return ping_flag

    async def update_cookies(self, browser_context: BrowserContext, urls: Optional[list[str]] = None):
        """
        Update cookies method provided by API client, usually called after successful login
        Args:
            browser_context: Browser context object

        Returns:

        """
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=urls or self.cookie_urls,
        )
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict

    async def get_note_by_keyword(
        self,
        keyword: str,
        search_id: str = get_search_id(),
        page: int = 1,
        page_size: int = 20,
        sort: SearchSortType = SearchSortType.GENERAL,
        note_type: SearchNoteType = SearchNoteType.ALL,
    ) -> Dict:
        """
        Search notes by keyword
        Args:
            keyword: Keyword parameter
            page: Page number
            page_size: Page data length
            sort: Search result sorting specification
            note_type: Type of note to search

        Returns:

        """
        uri = "/api/sns/web/v1/search/notes"
        data = {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "search_id": search_id,
            "sort": sort.value,
            "note_type": note_type.value,
        }
        return await self.post(uri, data)

    async def get_note_by_id(
        self,
        note_id: str,
        xsec_source: str,
        xsec_token: str,
    ) -> Dict:
        """
        Get note detail API
        Args:
            note_id: Note ID
            xsec_source: Channel source
            xsec_token: Token returned from search keyword result list

        Returns:

        """
        if xsec_source == "":
            xsec_source = "pc_search"

        data = {
            "source_note_id": note_id,
            "image_formats": ["jpg", "webp", "avif"],
            "extra": {"need_body_topic": 1},
            "xsec_source": xsec_source,
            "xsec_token": xsec_token,
        }
        uri = "/api/sns/web/v1/feed"
        res = await self.post(uri, data)
        if res and res.get("items"):
            res_dict: Dict = res["items"][0]["note_card"]
            return res_dict
        # When crawling frequently, some notes may have results while others don't
        utils.logger.error(
            f"[XiaoHongShuClient.get_note_by_id] get note id:{note_id} empty and res:{res}"
        )
        return dict()

    async def get_note_comments(
        self,
        note_id: str,
        xsec_token: str,
        cursor: str = "",
    ) -> Dict:
        """
        Get first-level comments API
        Args:
            note_id: Note ID
            xsec_token: Verification token
            cursor: Pagination cursor

        Returns:

        """
        uri = "/api/sns/web/v2/comment/page"
        params = {
            "note_id": note_id,
            "cursor": cursor,
            "top_comment_id": "",
            "image_formats": "jpg,webp,avif",
            "xsec_token": xsec_token,
        }
        return await self.get(uri, params)

    async def get_note_sub_comments(
        self,
        note_id: str,
        root_comment_id: str,
        xsec_token: str,
        num: int = 10,
        cursor: str = "",
    ):
        """
        Get sub-comments under specified parent comment API
        Args:
            note_id: Post ID of sub-comments
            root_comment_id: Root comment ID
            xsec_token: Verification token
            num: Pagination quantity
            cursor: Pagination cursor

        Returns:

        """
        uri = "/api/sns/web/v2/comment/sub/page"
        params = {
            "note_id": note_id,
            "root_comment_id": root_comment_id,
            "num": str(num),
            "cursor": cursor,
            "image_formats": "jpg,webp,avif",
            "top_comment_id": "",
            "xsec_token": xsec_token,
        }
        return await self.get(uri, params)

    async def get_note_all_comments(
        self,
        note_id: str,
        xsec_token: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
        max_count: int = 10,
    ) -> List[Dict]:
        """
        Get all first-level comments under specified note, this method will continuously find all comment information under a post
        Args:
            note_id: Note ID
            xsec_token: Verification token
            crawl_interval: Crawl delay per note (seconds)
            callback: Callback after one note crawl ends
            max_count: Maximum number of comments to crawl per note
        Returns:

        """
        result = []
        comments_has_more = True
        comments_cursor = ""
        while comments_has_more and len(result) < max_count:
            comments_res = await self.get_note_comments(
                note_id=note_id, xsec_token=xsec_token, cursor=comments_cursor
            )
            comments_has_more = comments_res.get("has_more", False)
            comments_cursor = comments_res.get("cursor", "")
            if "comments" not in comments_res:
                utils.logger.info(
                    f"[XiaoHongShuClient.get_note_all_comments] No 'comments' key found in response: {comments_res}"
                )
                break
            comments = comments_res["comments"]
            if len(result) + len(comments) > max_count:
                comments = comments[: max_count - len(result)]
            if callback:
                await callback(note_id, comments)
            await polite_sleep(crawl_interval)
            result.extend(comments)
            sub_comments = await self.get_comments_all_sub_comments(
                comments=comments,
                xsec_token=xsec_token,
                crawl_interval=crawl_interval,
                callback=callback,
            )
            result.extend(sub_comments)
        return result

    async def get_comments_all_sub_comments(
        self,
        comments: List[Dict],
        xsec_token: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        Get all second-level comments under specified first-level comments, this method will continuously find all second-level comment information under first-level comments
        Args:
            comments: Comment list
            xsec_token: Verification token
            crawl_interval: Crawl delay per comment (seconds)
            callback: Callback after one comment crawl ends

        Returns:

        """
        if not config.ENABLE_GET_SUB_COMMENTS:
            utils.logger.info(
                f"[XiaoHongShuCrawler.get_comments_all_sub_comments] Crawling sub_comment mode is not enabled"
            )
            return []

        result = []
        for comment in comments:
            try:
                note_id = comment.get("note_id")
                sub_comments = comment.get("sub_comments")
                if sub_comments and callback:
                    await callback(note_id, sub_comments)

                sub_comment_has_more = comment.get("sub_comment_has_more")
                if not sub_comment_has_more:
                    continue

                root_comment_id = comment.get("id")
                sub_comment_cursor = comment.get("sub_comment_cursor")

                while sub_comment_has_more:
                    try:
                        comments_res = await self.get_note_sub_comments(
                            note_id=note_id,
                            root_comment_id=root_comment_id,
                            xsec_token=xsec_token,
                            num=10,
                            cursor=sub_comment_cursor,
                        )

                        if comments_res is None:
                            utils.logger.info(
                                f"[XiaoHongShuClient.get_comments_all_sub_comments] No response found for note_id: {note_id}"
                            )
                            break
                        sub_comment_has_more = comments_res.get("has_more", False)
                        sub_comment_cursor = comments_res.get("cursor", "")
                        if "comments" not in comments_res:
                            utils.logger.info(
                                f"[XiaoHongShuClient.get_comments_all_sub_comments] No 'comments' key found in response: {comments_res}"
                            )
                            break
                        comments = comments_res["comments"]
                        if callback:
                            await callback(note_id, comments)
                        await polite_sleep(crawl_interval)
                        result.extend(comments)
                    except DataFetchError as e:
                        utils.logger.warning(
                            f"[XiaoHongShuClient.get_comments_all_sub_comments] Failed to get sub-comments for note_id: {note_id}, root_comment_id: {root_comment_id}, error: {e}. Skipping this comment's sub-comments."
                        )
                        break  # Break out of the sub-comment acquisition loop of the current comment and continue processing the next comment
                    except Exception as e:
                        utils.logger.error(
                            f"[XiaoHongShuClient.get_comments_all_sub_comments] Unexpected error when getting sub-comments for note_id: {note_id}, root_comment_id: {root_comment_id}, error: {e}"
                        )
                        break
            except Exception as e:
                utils.logger.error(
                    f"[XiaoHongShuClient.get_comments_all_sub_comments] Error processing comment: {comment.get('id', 'unknown')}, error: {e}. Continuing with next comment."
                )
                continue  # Continue to next comment
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_not_exception_type(
            (RetryError, IPBlockError, PlatformAccessError, CaptchaError)
        ),
    )
    async def get_creator_info(
        self, user_id: str, xsec_token: str = "", xsec_source: str = ""
    ) -> Dict:
        """
        Get user profile brief information by parsing user homepage HTML
        The PC user homepage has window.__INITIAL_STATE__ variable, just parse it

        Args:
            user_id: User ID
            xsec_token: Verification token (optional, pass if included in URL)
            xsec_source: Channel source (optional, pass if included in URL)

        Returns:
            Dict: Creator information
        """
        # Build URI, add xsec parameters to URL if available
        uri = f"/user/profile/{user_id}"
        if xsec_token and xsec_source:
            uri = f"{uri}?xsec_token={xsec_token}&xsec_source={xsec_source}"

        # 页面导航请求：不带 API 签名头，但需要 Cookie
        html_content = await self.request(
            "GET",
            self._domain + uri,
            return_response=True,
            headers=self._navigation_headers(include_cookie=True),
        )
        return self._extractor.extract_creator_info_from_html(html_content)

    async def get_notes_by_creator(
        self,
        creator: str,
        cursor: str,
        page_size: int = 30,
        xsec_token: str = "",
        xsec_source: str = "pc_feed",
    ) -> Dict:
        """
        Get creator's notes
        Args:
            creator: Creator ID
            cursor: Last note ID from previous page
            page_size: Page data length
            xsec_token: Verification token
            xsec_source: Channel source

        Returns:

        """
        uri = f"/api/sns/web/v1/user_posted"
        params = {
            "num": page_size,
            "cursor": cursor,
            "user_id": creator,
            "image_formats": "jpg,webp,avif",
            "xsec_token": xsec_token,
            "xsec_source": xsec_source,
        }
        return await self.get(uri, params)

    async def get_all_notes_by_creator(
        self,
        user_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
        xsec_token: str = "",
        xsec_source: str = "pc_feed",
    ) -> List[Dict]:
        """
        Get all posts published by specified user, this method will continuously find all post information under a user
        Args:
            user_id: User ID
            crawl_interval: Crawl delay (seconds)
            callback: Update callback function after one pagination crawl ends
            xsec_token: Verification token
            xsec_source: Channel source

        Returns:

        """
        result = []
        notes_has_more = True
        notes_cursor = ""
        while notes_has_more and len(result) < config.CRAWLER_MAX_NOTES_COUNT:
            notes_res = await self.get_notes_by_creator(
                user_id, notes_cursor, xsec_token=xsec_token, xsec_source=xsec_source
            )
            if not notes_res:
                utils.logger.error(
                    f"[XiaoHongShuClient.get_notes_by_creator] The current creator may have been banned by xhs, so they cannot access the data."
                )
                break

            notes_has_more = notes_res.get("has_more", False)
            notes_cursor = notes_res.get("cursor", "")
            if "notes" not in notes_res:
                utils.logger.info(
                    f"[XiaoHongShuClient.get_all_notes_by_creator] No 'notes' key found in response: {notes_res}"
                )
                break

            notes = notes_res["notes"]
            utils.logger.info(
                f"[XiaoHongShuClient.get_all_notes_by_creator] got user_id:{user_id} notes len : {len(notes)}"
            )

            remaining = config.CRAWLER_MAX_NOTES_COUNT - len(result)
            if remaining <= 0:
                break

            notes_to_add = notes[:remaining]
            if callback:
                await callback(notes_to_add)

            result.extend(notes_to_add)
            await polite_sleep(crawl_interval)

        utils.logger.info(
            f"[XiaoHongShuClient.get_all_notes_by_creator] Finished getting notes for user {user_id}, total: {len(result)}"
        )
        return result

    async def get_note_short_url(self, note_id: str) -> Dict:
        """
        Get note short URL
        Args:
            note_id: Note ID

        Returns:

        """
        uri = f"/api/sns/web/short_url"
        data = {"original_url": f"{self._domain}/discovery/item/{note_id}"}
        return await self.post(uri, data=data, return_response=True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_not_exception_type(
            (RetryError, IPBlockError, PlatformAccessError, CaptchaError)
        ),
    )
    async def get_note_by_id_from_html(
        self,
        note_id: str,
        xsec_source: str,
        xsec_token: str,
        enable_cookie: bool = False,
    ) -> Optional[Dict]:
        """
        Get note details by parsing note detail page HTML, this interface may fail, retry 3 times here
        copy from https://github.com/ReaJason/xhs/blob/eb1c5a0213f6fbb592f0a2897ee552847c69ea2d/xhs/core.py#L217-L259
        thanks for ReaJason
        Args:
            note_id:
            xsec_source:
            xsec_token:
            enable_cookie:

        Returns:

        """
        url = (
            f"{self._domain}/explore/"
            + note_id
            + f"?xsec_token={xsec_token}&xsec_source={xsec_source}"
        )
        copy_headers = self._navigation_headers(include_cookie=enable_cookie)

        html = await self.request(
            method="GET", url=url, return_response=True, headers=copy_headers
        )

        return self._extractor.extract_note_detail_from_html(note_id, html)
