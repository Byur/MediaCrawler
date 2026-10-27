# -*- coding: utf-8 -*-
"""反检测改动的回归测试（小红书）。

覆盖四类“改参数就能实验”的优化：

1. 身份一致性：签名里的 x2 平台、HTTP UA、UA-CH、真实 b1 必须相互自洽；
2. 请求真实性：补全签名头、按接口设置 Referer、页面导航请求不携带 API 签名头；
3. 重试语义：每次重试重新签名，429 退避重试、401/403 不重试、验证码冷却；
4. 行为节奏：sleep 抖动区间、has_more=False 时仍消费本页 items。

全部离线运行，不访问小红书。
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest

import config
from media_platform.xhs.browser_profile import (
    build_browser_profile,
    capture_browser_profile,
    identity_conflicts,
    profile_from_user_agent,
)
from media_platform.xhs.client import XiaoHongShuClient
from media_platform.xhs.exception import CaptchaError, DataFetchError, PlatformAccessError
from media_platform.xhs.playwright_sign import SignProfile, XhsSigner
from tools import humanize

# x-s / x-s-common 使用的自定义 base64 表
_STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_CUSTOM_ALPHABET = "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5"
_TRANS = str.maketrans(_CUSTOM_ALPHABET, _STD_ALPHABET)


def decode_custom_b64(value: str) -> bytes:
    translated = value.translate(_TRANS)
    translated += "=" * (-len(translated) % 4)
    return base64.b64decode(translated)


def decode_x_s(value: str) -> dict:
    payload = value[4:] if value.startswith("XYS_") else value
    return json.loads(decode_custom_b64(payload))


def decode_x_s_common(value: str) -> dict:
    return json.loads(decode_custom_b64(value))


class FakeAsyncClient:
    def __init__(self, request_impl):
        self.request_impl = request_impl

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, *args, **kwargs):
        return await self.request_impl(*args, **kwargs)


def make_client() -> XiaoHongShuClient:
    client = XiaoHongShuClient(
        headers={"Cookie": "a1=AAAA; web_session=BBBB"},
        playwright_page=object(),
        cookie_dict={},
    )
    client._refresh_proxy_if_expired = AsyncMock()
    return client


# --------------------------------------------------------------------- 签名
def test_sign_headers_carry_full_browser_header_set():
    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1="B1REAL"))
    headers = signer.sign_headers(
        "POST", "/api/sns/web/v1/search/notes", "a1=AAAA; web_session=BBBB", {"keyword": "k"}
    )

    assert {"x-s", "x-t", "x-s-common", "x-b3-traceid"} <= set(headers)
    # 旧实现丢弃了这三个库已经算好的头
    assert {"x-xray-traceid", "x-mns", "xy-direction"} <= set(headers)


def test_legacy_sign_with_xhshow_is_still_exported():
    """旧版公共接口保留，避免外部调用方 import 失败。"""
    from media_platform.xhs.playwright_sign import sign_with_xhshow

    headers = sign_with_xhshow("/api/sns/web/v1/feed", {"source_note_id": "n1"}, "a1=AAAA")

    assert {"x-s", "x-t", "x-s-common", "x-b3-traceid"} <= set(headers)


def test_signature_platform_matches_configured_identity():
    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1="B1REAL"))
    headers = signer.sign_headers("POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": "n1"})

    assert decode_x_s(headers["x-s"])["x2"] == "Mac OS"
    assert decode_x_s_common(headers["x-s-common"])["x2"] == "Mac OS"


def test_real_b1_overrides_synthetic_x_s_common():
    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1="B1REAL"))
    headers = signer.sign_headers("POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": "n1"})

    common = decode_x_s_common(headers["x-s-common"])
    assert common["x8"] == "B1REAL"
    assert common["x5"] == "AAAA"


def test_synthetic_b1_used_only_when_real_b1_missing():
    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1=""))
    headers = signer.sign_headers("POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": "n1"})

    common = decode_x_s_common(headers["x-s-common"])
    assert common["x8"]  # 库会现场合成一份，至少有值
    assert common["x8"] != "B1REAL"


def test_signer_without_profile_keeps_legacy_default_platform():
    signer = XhsSigner()
    headers = signer.sign_headers("POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": "n1"})
    # 未提供真实身份时保持旧行为（xhshow 默认 x2=Windows），便于对比实验
    assert decode_x_s(headers["x-s"])["x2"] == "Windows"


def test_session_state_evolves_within_one_page_load():
    """同一会话内 page_load_timestamp 固定、sequence/window_props 递增。

    旧实现每个请求都 new 一个 Xhshow()，等于每个请求都自称"刚打开页面"。
    """
    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1="B1REAL"))
    session = signer._session
    page_load_ts = session.page_load_timestamp
    props_before = session.window_props_length
    seq_before = session.sequence_value

    for index in range(5):
        signer.sign_headers(
            "POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": f"n{index}"}
        )

    assert session.page_load_timestamp == page_load_ts  # 同一会话，不重新"加载页面"
    assert session.window_props_length > props_before  # 每步 1~10，必然递增
    assert session.sequence_value >= seq_before  # 步长 0~1，非递减


def test_reset_session_simulates_page_reload():
    import time as _time

    signer = XhsSigner(SignProfile(user_agent="UA-TEST", platform="Mac OS", b1="B1REAL"))
    old_ts = signer._session.page_load_timestamp
    _time.sleep(0.01)
    signer.reset_session()

    assert signer._session is not None
    assert signer._session.page_load_timestamp > old_ts


# --------------------------------------------------------------------- 身份
def test_profile_from_windows_ua_is_coherent():
    profile = profile_from_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    )

    assert profile.platform == "Windows"
    assert profile.ua_platform == '"Windows"'
    # 没有 navigator.userAgentData 时不凭空合成 sec-ch-ua，宁可不发这个头
    assert profile.ua_brands == ""
    assert profile.ua_mobile == "?0"


def test_profile_from_macos_ua_uses_mac_os_platform():
    profile = profile_from_user_agent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    )

    assert profile.platform == "Mac OS"
    assert profile.ua_platform == '"macOS"'
    assert profile.ua_brands == ""


def test_browser_values_win_over_ua_derivation():
    profile = build_browser_profile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0 Safari/537.36",
        brands=[["Chromium", "141"], ["Google Chrome", "141"]],
        ua_platform="macOS",  # UA 说是 Windows，UA-CH 说是 macOS：以浏览器实际值为准
        languages=["zh-CN", "zh", "en"],
        b1="REAL_B1",
        source="browser",
    )

    assert profile.platform == "Mac OS"
    assert profile.ua_platform == '"macOS"'
    assert profile.ua_brands == '"Chromium";v="141", "Google Chrome";v="141"'
    assert profile.accept_language == "zh-CN,zh,en"
    assert profile.has_real_b1


@pytest.mark.asyncio
async def test_capture_profile_without_page_falls_back_to_ua():
    profile = await capture_browser_profile(
        None, fallback_user_agent="Mozilla/5.0 (Macintosh) Chrome/141.0.0.0 Safari/537.36"
    )
    assert profile.platform == "Mac OS"
    assert profile.source == "fallback"


def test_identity_conflicts_flags_headless_and_mismatch():
    headless = build_browser_profile(
        user_agent="Mozilla/5.0 (Macintosh) AppleWebKit/537.36 HeadlessChrome/141.0.0.0 Safari/537.36",
        brands=[["Chromium", "141"]],
        ua_platform="macOS",
    )
    assert any("HeadlessChrome" in c for c in identity_conflicts(headless))

    # UA 说 Windows 126，UA-CH 却是 macOS 141：两个方向都要报
    mismatched = build_browser_profile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36",
        brands=[["Chromium", "141"], ["Google Chrome", "141"]],
        ua_platform="macOS",
    )
    conflicts = identity_conflicts(mismatched)
    assert any("大版本" in c for c in conflicts)
    assert any("平台" in c for c in conflicts)


def test_identity_conflicts_accepts_coherent_identity():
    coherent = build_browser_profile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        brands=[["Chromium", "141"], ["Google Chrome", "141"], ["Not_A Brand", "24"]],
        ua_platform="macOS",
        languages=["zh-CN", "zh"],
    )
    assert identity_conflicts(coherent) == []


# --------------------------------------------------------------------- 请求头
def test_navigation_headers_strip_api_signature():
    client = make_client()
    client.headers.update(
        {
            "x-s": "sig",
            "x-t": "123",
            "x-s-common": "common",
            "X-B3-Traceid": "trace",
            "x-rap-param": "rap",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.xiaohongshu.com",
        }
    )

    headers = client._navigation_headers(include_cookie=True)
    lowered = {key.lower() for key in headers}

    assert not lowered & client.SIGN_HEADER_KEYS
    assert "cookie" in lowered
    # 带 Referer 的导航不可能是 sec-fetch-site: none，两者只能留一个
    assert "referer" not in lowered
    # 文档导航的 fetch metadata 与 XHR 不同
    assert lowered & {"sec-fetch-dest", "sec-fetch-mode", "sec-fetch-user"}
    assert headers["sec-fetch-dest"] == "document"
    assert headers["sec-fetch-mode"] == "navigate"
    assert headers["accept"].startswith("text/html")
    assert "content-type" not in lowered
    assert "origin" not in lowered

    without_cookie = client._navigation_headers(include_cookie=False)
    assert not {key.lower() for key in without_cookie} & {"cookie"}


@pytest.mark.parametrize(
    ("uri", "params", "payload", "expected"),
    [
        (
            "/api/sns/web/v1/search/notes",
            None,
            {"keyword": "副业 编程"},
            "https://www.xiaohongshu.com/search_result?keyword=%E5%89%AF%E4%B8%9A%20%E7%BC%96%E7%A8%8B"
            "&source=web_search_result_notes",
        ),
        (
            "/api/sns/web/v1/feed",
            None,
            {"source_note_id": "note1"},
            "https://www.xiaohongshu.com/explore/note1",
        ),
        (
            "/api/sns/web/v2/comment/page",
            {"note_id": "note1"},
            None,
            "https://www.xiaohongshu.com/explore/note1",
        ),
        (
            "/api/sns/web/v1/user_posted",
            {"user_id": "user1"},
            None,
            "https://www.xiaohongshu.com/user/profile/user1",
        ),
    ],
)
def test_endpoint_referer_follows_real_browsing_path(uri, params, payload, expected):
    client = make_client()
    assert client._endpoint_referer(uri, params=params, payload=payload) == expected


@pytest.mark.asyncio
async def test_pre_headers_does_not_mutate_shared_headers():
    client = make_client()
    await client._pre_headers("/api/sns/web/v1/feed", payload={"source_note_id": "n1"})

    # 签名只存在于本次请求的副本里；原 headers 不再被污染（旧实现会 update 进来）
    assert "x-s" not in {key.lower() for key in client.headers}


# --------------------------------------------------------------------- 重试
@pytest.mark.asyncio
async def test_fetch_re_signs_on_every_attempt(monkeypatch):
    client = make_client()
    monkeypatch.setattr("media_platform.xhs.client.RETRY_BASE_DELAY_SEC", 0.0)

    sign_calls = 0
    original_sign = client._signer.sign_headers

    def spy_sign(*args, **kwargs):
        nonlocal sign_calls
        sign_calls += 1
        return original_sign(*args, **kwargs)

    monkeypatch.setattr(client._signer, "sign_headers", spy_sign)

    attempts = 0

    async def fake_request(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise DataFetchError("temporary failure")
        return {"ok": True}

    monkeypatch.setattr(client, "request", fake_request)

    result = await client._fetch("POST", "/api/sns/web/v1/feed", payload={"source_note_id": "n1"})

    assert result == {"ok": True}
    assert attempts == 3
    assert sign_calls == 3  # 每次重试都重新签名，而不是重放旧 x-s/x-t


@pytest.mark.asyncio
async def test_fetch_retries_429_with_backoff(monkeypatch):
    client = make_client()
    monkeypatch.setattr("media_platform.xhs.client.RETRY_BASE_DELAY_SEC", 0.0)

    attempts = 0

    async def fake_request(**kwargs):
        nonlocal attempts
        attempts += 1
        raise PlatformAccessError("rate limited", status_code=429)

    monkeypatch.setattr(client, "request", fake_request)

    with pytest.raises(PlatformAccessError):
        await client._fetch("POST", "/api/sns/web/v1/feed", payload={"source_note_id": "n1"})

    assert attempts == 3


@pytest.mark.asyncio
async def test_fetch_does_not_retry_403(monkeypatch):
    client = make_client()

    attempts = 0

    async def fake_request(**kwargs):
        nonlocal attempts
        attempts += 1
        raise PlatformAccessError("forbidden", status_code=403)

    monkeypatch.setattr(client, "request", fake_request)

    with pytest.raises(PlatformAccessError):
        await client._fetch("POST", "/api/sns/web/v1/feed", payload={"source_note_id": "n1"})

    assert attempts == 1


@pytest.mark.asyncio
async def test_fetch_does_not_retry_captcha(monkeypatch):
    """验证码不是网络抖动：立即抛给上层跳过，不做内联冷却重试。"""
    client = make_client()

    attempts = 0

    async def fake_request(**kwargs):
        nonlocal attempts
        attempts += 1
        raise CaptchaError("captcha", verify_type="slide")

    monkeypatch.setattr(client, "request", fake_request)

    with pytest.raises(CaptchaError):
        await client._fetch("POST", "/api/sns/web/v1/feed", payload={"source_note_id": "n1"})

    assert attempts == 1


@pytest.mark.asyncio
async def test_captcha_response_raises_dedicated_exception(monkeypatch):
    async def request_impl(method, url, **kwargs):
        return httpx.Response(
            461,
            headers={"Verifytype": "captcha", "Verifyuuid": "uuid-1"},
            text="captcha",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )

    with pytest.raises(CaptchaError) as exc_info:
        await make_client().request("POST", "https://edith.xiaohongshu.com/api/test")

    assert exc_info.value.verify_type == "captcha"
    assert exc_info.value.verify_uuid == "uuid-1"


# --------------------------------------------------------------------- 行为节奏
def test_next_delay_stays_within_jitter_bounds(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 10, raising=False)

    delays = [humanize.next_delay() for _ in range(200)]

    # ±50%：base=10 时落在 5~15s
    assert all(5.0 <= delay <= 15.0 for delay in delays)
    assert min(delays) < 10.0 < max(delays)


@pytest.mark.asyncio
async def test_polite_sleep_awaits_jittered_interval(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 2, raising=False)

    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(humanize.asyncio, "sleep", fake_sleep)

    delay = await humanize.polite_sleep(2)

    assert recorded == [delay]
    assert 1.0 <= delay <= 3.0


def test_default_interval_covers_one_to_three_seconds():
    """默认 base=2s、±50%：每次请求间隔是 1~3s 的均匀随机数。"""
    assert config.CRAWLER_MAX_SLEEP_SEC * humanize.JITTER_MIN_RATIO == 1
    assert config.CRAWLER_MAX_SLEEP_SEC * humanize.JITTER_MAX_RATIO == 3

    delays = [humanize.next_delay() for _ in range(200)]

    assert all(1.0 <= delay <= 3.0 for delay in delays)
    # 不是退化成固定值：样本要覆盖区间两侧
    assert min(delays) < 2.0 < max(delays)


# --------------------------------------------------------------------- 数据完整性
@pytest.mark.asyncio
async def test_search_processes_items_even_when_has_more_is_false(monkeypatch):
    from media_platform.xhs.core import XiaoHongShuCrawler

    monkeypatch.setattr(config, "KEYWORDS", "test", raising=False)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 20, raising=False)
    monkeypatch.setattr(config, "START_PAGE", 1, raising=False)
    monkeypatch.setattr(config, "SORT_TYPE", "", raising=False)
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 1, raising=False)
    monkeypatch.setattr(
        "media_platform.xhs.core.polite_sleep", AsyncMock(return_value=0.0)
    )
    update_note = AsyncMock()
    monkeypatch.setattr("media_platform.xhs.core.xhs_store.update_xhs_note", update_note)

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_by_keyword = AsyncMock(
        return_value={
            "has_more": False,
            "items": [{"id": "n1", "xsec_token": "t", "xsec_source": "pc_search"}],
        }
    )
    crawler.get_note_detail_async_task = AsyncMock(
        return_value={"note_id": "n1", "xsec_token": "t"}
    )
    crawler.download_media = AsyncMock()
    crawler.batch_get_note_comments = AsyncMock()

    await crawler.search()

    crawler.get_note_detail_async_task.assert_awaited_once()
    update_note.assert_awaited_once()
    crawler.batch_get_note_comments.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_breaks_gracefully_when_access_restricted(monkeypatch):
    from media_platform.xhs.core import XiaoHongShuCrawler

    monkeypatch.setattr(config, "KEYWORDS", "test", raising=False)
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 20, raising=False)
    monkeypatch.setattr(config, "START_PAGE", 1, raising=False)
    monkeypatch.setattr(config, "SORT_TYPE", "", raising=False)

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_by_keyword = AsyncMock(
        side_effect=PlatformAccessError("HTTP 429", status_code=429)
    )

    # 旧实现会让 PlatformAccessError 直接击穿 search()，整个任务中断
    await crawler.search()


# --------------------------------------------------------------------- 集成
class _FakeBrowserContext:
    async def cookies(self, urls=None):
        return [
            {
                "name": "a1",
                "value": "A1_FROM_BROWSER",
                "domain": ".xiaohongshu.com",
                "path": "/",
            },
            {
                "name": "web_session",
                "value": "SESSION",
                "domain": ".xiaohongshu.com",
                "path": "/",
            },
        ]


class _FakePage:
    def __init__(self, payload):
        self.payload = payload

    async def evaluate(self, script):
        return self.payload


@pytest.mark.asyncio
async def test_crawler_builds_headers_from_real_browser_identity(monkeypatch):
    """core.start() 的真实路径：采集身份 → 建 client → 头与签名同源。"""
    from media_platform.xhs.core import XiaoHongShuCrawler

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.index_url = "https://www.xiaohongshu.com"
    crawler.cookie_urls = [crawler.index_url]
    crawler.browser_context = _FakeBrowserContext()
    crawler.context_page = _FakePage(
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            "langs": ["zh-CN", "zh"],
            "brands": [["Chromium", "141"], ["Google Chrome", "141"]],
            "platform": "macOS",
            "mobile": False,
            "b1": "REAL_B1_FROM_BROWSER",
        }
    )
    crawler.user_agent = ""
    crawler.browser_profile = None
    crawler.ip_proxy_pool = None
    crawler.cdp_manager = None
    crawler._media_downloader = None

    await crawler._capture_browser_profile()
    client = await crawler.create_xhs_client(None)

    # HTTP 头：UA、UA-CH、平台互相一致，且来自浏览器
    assert client.headers["user-agent"].endswith("Chrome/141.0.0.0 Safari/537.36")
    assert client.headers["sec-ch-ua-platform"] == '"macOS"'
    assert "141" in client.headers["sec-ch-ua"]
    assert "pragma" not in {key.lower() for key in client.headers}

    # 签名：x2 与 HTTP 平台一致，x-s-common 使用浏览器真实 b1
    headers = await client._pre_headers("/api/sns/web/v1/feed", payload={"source_note_id": "n1"})
    assert decode_x_s(headers["x-s"])["x2"] == "Mac OS"
    common = decode_x_s_common(headers["x-s-common"])
    assert common["x8"] == "REAL_B1_FROM_BROWSER"
    assert common["x5"] == "A1_FROM_BROWSER"
    # Referer 按接口推断
    assert headers["referer"] == "https://www.xiaohongshu.com/explore/n1"


@pytest.mark.asyncio
async def test_stable_identity_is_internally_coherent():
    """浏览器采集失败/headless 时使用的稳定身份也必须自洽：UA、UA-CH、x2 描述同一台设备。"""
    from media_platform.xhs.core import XiaoHongShuCrawler

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.index_url = "https://www.xiaohongshu.com"
    crawler.cookie_urls = [crawler.index_url]
    crawler.browser_context = _FakeBrowserContext()
    crawler.context_page = _FakePage({})
    crawler.user_agent = ""
    crawler.browser_profile = None
    crawler.ip_proxy_pool = None
    crawler.cdp_manager = None
    crawler._media_downloader = None

    client = await crawler.create_xhs_client(None)

    assert "Chrome/155.0.0.0" in client.headers["user-agent"]
    assert 'v="155"' in client.headers["sec-ch-ua"]
    assert client.headers["sec-ch-ua-platform"] == '"macOS"'

    headers = await client._pre_headers("/api/sns/web/v1/feed", payload={"source_note_id": "n1"})
    assert decode_x_s(headers["x-s"])["x2"] == "Mac OS"
    assert decode_x_s_common(headers["x-s-common"])["x2"] == "Mac OS"


class _RaisingPage:
    """模拟登录跳转期间 page.evaluate 失败（Execution context was destroyed）。"""

    async def evaluate(self, script):
        raise RuntimeError("Execution context was destroyed")


@pytest.mark.asyncio
async def test_headless_capture_uses_stable_identity_with_real_b1():
    """headless 浏览器自报 HeadlessChrome 时：HTTP/签名身份用稳定样张，真实 b1 保留。"""
    from media_platform.xhs.core import XiaoHongShuCrawler, _STABLE_USER_AGENT

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.index_url = "https://www.xiaohongshu.com"
    crawler.cookie_urls = [crawler.index_url]
    crawler.browser_context = _FakeBrowserContext()
    crawler.context_page = _FakePage(
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36",
            "langs": ["zh-CN"],
            "brands": None,  # chrome-headless-shell 没有 navigator.userAgentData
            "platform": "",
            "mobile": False,
            "b1": "REAL_B1_HEADLESS",
        }
    )
    crawler.user_agent = ""
    crawler.browser_profile = None
    crawler.ip_proxy_pool = None
    crawler.cdp_manager = None
    crawler._media_downloader = None

    profile = await crawler._capture_browser_profile()

    assert profile.source == "stable"
    assert profile.user_agent == _STABLE_USER_AGENT
    assert "HeadlessChrome" not in profile.user_agent
    assert 'v="155"' in profile.ua_brands
    assert profile.platform == "Mac OS"
    assert profile.b1 == "REAL_B1_HEADLESS"

    client = await crawler.create_xhs_client(None)
    assert "HeadlessChrome" not in client.headers["user-agent"]
    assert 'v="155"' in client.headers["sec-ch-ua"]

    headers = await client._pre_headers("/api/sns/web/v1/feed", payload={"source_note_id": "n1"})
    assert decode_x_s(headers["x-s"])["x2"] == "Mac OS"
    assert decode_x_s_common(headers["x-s-common"])["x8"] == "REAL_B1_HEADLESS"


@pytest.mark.asyncio
async def test_capture_failure_uses_stable_identity():
    """启动时采集失败也不能退回 UA-only 身份：使用完整自洽的稳定样张。"""
    from media_platform.xhs.core import XiaoHongShuCrawler, _STABLE_USER_AGENT

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.context_page = _RaisingPage()
    crawler.user_agent = ""
    crawler.browser_profile = None

    profile = await crawler._capture_browser_profile()

    assert profile.source == "stable"
    assert profile.user_agent == _STABLE_USER_AGENT
    assert 'v="155"' in profile.ua_brands
    assert profile.platform == "Mac OS"
    assert not profile.b1


@pytest.mark.asyncio
async def test_refresh_does_not_downgrade_real_b1():
    """登录后 refresh 采集失败时，不能把启动时拿到的真实 b1 冲成空。"""
    profile = build_browser_profile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        brands=[["Chromium", "141"], ["Google Chrome", "141"]],
        ua_platform="macOS",
        languages=["zh-CN", "zh"],
        b1="REAL_B1",
        source="browser",
    )
    client = XiaoHongShuClient(
        headers={"Cookie": "a1=AAAA; web_session=S", "user-agent": profile.user_agent},
        playwright_page=_RaisingPage(),
        cookie_dict={},
        browser_profile=profile,
    )

    refreshed = await client.refresh_browser_profile()

    assert refreshed is not None and refreshed.b1 == "REAL_B1"
    assert client.browser_profile is not None and client.browser_profile.b1 == "REAL_B1"
    headers = await client._pre_headers("/api/sns/web/v1/feed", payload={"source_note_id": "n1"})
    assert decode_x_s_common(headers["x-s-common"])["x8"] == "REAL_B1"


@pytest.mark.asyncio
async def test_refresh_stable_identity_only_adopts_b1():
    """稳定样张身份 + headless 浏览器：refresh 只补真实 b1，不采用 headless 的 UA/UA-CH。"""
    from media_platform.xhs.core import _STABLE_PROFILE, _STABLE_USER_AGENT

    client = XiaoHongShuClient(
        headers={"Cookie": "a1=AAAA; web_session=S", "user-agent": _STABLE_USER_AGENT},
        playwright_page=_FakePage(
            {
                "ua": "Mozilla/5.0 (Macintosh) HeadlessChrome/151.0.0.0 Safari/537.36",
                "langs": ["zh-CN"],
                "brands": [["Chromium", "151"]],
                "platform": "macOS",
                "mobile": False,
                "b1": "REAL_B1_AFTER_LOGIN",
            }
        ),
        cookie_dict={},
        browser_profile=_STABLE_PROFILE,
    )

    refreshed = await client.refresh_browser_profile()

    assert refreshed is not None
    assert refreshed.b1 == "REAL_B1_AFTER_LOGIN"
    assert refreshed.user_agent == _STABLE_USER_AGENT
    assert 'v="155"' in refreshed.ua_brands
    assert refreshed.source == "stable"


@pytest.mark.asyncio
async def test_non_json_response_raises_data_fetch_error(monkeypatch):
    """WAF/验证码 HTML 页（200 + 非 JSON）不能被 JSONDecodeError 击穿。"""

    async def request_impl(method, url, **kwargs):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>challenge page</html>",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "media_platform.xhs.client.make_async_client",
        lambda **kwargs: FakeAsyncClient(request_impl),
    )
    monkeypatch.setattr("media_platform.xhs.client.RETRY_BASE_DELAY_SEC", 0.0)

    with pytest.raises(DataFetchError):
        await make_client().get_note_by_id("n1", "pc_search", "token")


@pytest.mark.asyncio
async def test_transport_error_after_retries_is_wrapped_as_data_fetch_error(monkeypatch):
    """重试耗尽的传输层错误要包装成 DataFetchError，不能裸穿 asyncio.gather。"""
    client = make_client()
    monkeypatch.setattr("media_platform.xhs.client.RETRY_BASE_DELAY_SEC", 0.0)
    attempts = 0

    async def fake_request(**kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(client, "request", fake_request)

    with pytest.raises(DataFetchError):
        await client.get_note_by_id("n1", "pc_search", "token")
    assert attempts == 3


@pytest.mark.asyncio
async def test_detail_task_falls_back_to_html_on_data_fetch_error(monkeypatch):
    """API 业务失败（DataFetchError）后仍要尝试 HTML 兜底，不能静默丢笔记。"""
    from media_platform.xhs.core import XiaoHongShuCrawler

    monkeypatch.setattr("media_platform.xhs.core.polite_sleep", AsyncMock(return_value=0.0))

    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_by_id = AsyncMock(side_effect=DataFetchError("server biz error"))
    crawler.xhs_client.get_note_by_id_from_html = AsyncMock(return_value={"note_id": "n1"})

    result = await crawler.get_note_detail_async_task(
        note_id="n1",
        xsec_source="pc_search",
        xsec_token="token",
        semaphore=asyncio.Semaphore(1),
    )

    assert result is not None and result["note_id"] == "n1"
    crawler.xhs_client.get_note_by_id_from_html.assert_awaited_once()


def test_ios_user_agent_is_not_treated_as_macos():
    from media_platform.xhs.browser_profile import _platform_from_user_agent

    ios_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    )
    assert _platform_from_user_agent(ios_ua) == "iOS"


def test_malformed_brand_entries_do_not_raise():
    """capture 的契约是永不抛：异常结构只跳过该条，不影响其余身份。"""
    profile = build_browser_profile(
        user_agent="UA", brands=[["Chromium"], ["Google Chrome", "155"]], ua_platform="macOS"
    )
    assert profile.ua_brands == '"Google Chrome";v="155"'


def test_signer_fails_fast_when_xhshow_unavailable(monkeypatch):
    """签名库不可用时必须报错，不能静默发送无签名请求。"""
    import media_platform.xhs.playwright_sign as sign_module

    monkeypatch.setattr(sign_module, "_XHSHOW_AVAILABLE", False)
    signer = XhsSigner(SignProfile(user_agent="UA", platform="Mac OS", b1="B1"))

    with pytest.raises(RuntimeError):
        signer.sign_headers("POST", "/api/sns/web/v1/feed", "a1=AAAA", {"source_note_id": "n1"})
