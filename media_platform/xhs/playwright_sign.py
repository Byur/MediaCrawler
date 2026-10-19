# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/playwright_sign.py
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

# Xiaohongshu signature generation using xhshow pure-algorithm library
#
# 致谢：本签名实现依赖 xhshow 开源库, 由 Cloxl 提供
# 仓库地址: https://github.com/Cloxl/xhshow
# 许可协议: MIT License
#
# 依赖 xhshow>=0.2.0: 该版本原生修复了 GET 请求 a3_hash 计算的 bug
# (https://github.com/Cloxl/xhshow/issues/104), 不再需要在本地打 monkey-patch。
#
# 与“直接调用 xhshow 默认值”的区别（反检测评估里最便宜的一批修复）：
#
# 1. ``x2`` 平台字段与真实运行环境对齐：默认值写死 "Windows"，如果实际是 macOS，
#    会和 HTTP ``User-Agent`` 直接冲突。
# 2. ``x-s-common`` 里的 ``b1`` 使用浏览器 ``localStorage['b1']`` 的真实指纹，
#    而不是每次现场合成的假指纹（合成指纹会让同一 a1 的设备指纹逐请求抖动）。
# 3. 使用 ``SessionManager`` 维护会话状态：同一会话内 ``page_load_timestamp``
#    固定、``sequence`` 递增，而不是每个请求都自称“刚刚新开了一个页面”。
# 4. 返回完整头集合（含 ``x-xray-traceid`` / ``x-mns`` / ``xy-direction``），
#    不再丢弃库已经算好的字段。

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from tools import utils

from .xhs_sign import get_trace_id

try:  # pragma: no cover - 依赖版本差异时降级为旧行为
    from xhshow import Xhshow
    from xhshow.config import CryptoConfig
    from xhshow.core.crc32_encrypt import CRC32
    from xhshow.session import SessionManager
    from xhshow.utils.encoder import Base64Encoder

    _XHSHOW_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    Xhshow = None  # type: ignore[assignment]
    CryptoConfig = None  # type: ignore[assignment]
    CRC32 = None  # type: ignore[assignment]
    SessionManager = None  # type: ignore[assignment]
    Base64Encoder = None  # type: ignore[assignment]
    _XHSHOW_AVAILABLE = False
    utils.logger.error(f"[XhsSigner] xhshow 不可用，签名功能降级: {_exc}")


__all__ = ["SignProfile", "XhsSigner", "sign_with_xhshow"]


@dataclass(frozen=True)
class SignProfile:
    """签名需要对齐的“设备身份”子集。"""

    user_agent: str = ""
    platform: str = ""  # x-s / x-s-common 的 x2："Mac OS" / "Windows" / "Linux"
    b1: str = ""  # 浏览器 localStorage['b1'] 的真实值


def _parse_cookie_dict(cookie_str: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


class XhsSigner:
    """有状态的签名器：同一会话内复用，不要每个请求 new 一个。"""

    #: 模拟"页面重载"：长跑时定期重置 page_load_timestamp / sequence
    SESSION_ROTATE_SECONDS = 1800

    def __init__(self, profile: Optional[SignProfile] = None) -> None:
        self._profile = profile or SignProfile()
        self._build()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        self._config = self._build_config(self._profile)
        self._client = Xhshow(self._config) if _XHSHOW_AVAILABLE else None
        self._session: Optional[Any] = None
        if _XHSHOW_AVAILABLE and SessionManager is not None:
            self._session = SessionManager(self._config)
        self._session_started_at = time.monotonic()

    @staticmethod
    def _build_config(profile: SignProfile) -> Any:
        """在 xhshow 的默认配置上覆盖与设备相关的字段。"""
        if not _XHSHOW_AVAILABLE or CryptoConfig is None:  # pragma: no cover
            return None
        base = CryptoConfig()
        if not profile or not (profile.user_agent or profile.platform):
            return base

        overrides: Dict[str, Any] = {}

        if profile.platform:
            signature_template = dict(base.SIGNATURE_DATA_TEMPLATE)
            signature_template["x2"] = profile.platform
            common_template = dict(base.SIGNATURE_XSCOMMON_TEMPLATE)
            common_template["x2"] = profile.platform
            overrides["SIGNATURE_DATA_TEMPLATE"] = signature_template
            overrides["SIGNATURE_XSCOMMON_TEMPLATE"] = common_template

        if profile.user_agent:
            overrides["PUBLIC_USERAGENT"] = profile.user_agent

        return base.with_overrides(**overrides) if overrides else base

    # ------------------------------------------------------------------ 状态
    def set_profile(self, profile: SignProfile) -> None:
        """登录/刷新后更新身份；同时重置会话状态（等价于页面发生了一次重载）。"""
        self._profile = profile or SignProfile()
        self._build()

    def reset_session(self) -> None:
        """模拟页面重载：重置 page_load_timestamp / sequence。"""
        if _XHSHOW_AVAILABLE and SessionManager is not None:
            self._session = SessionManager(self._config)
            self._session_started_at = time.monotonic()

    def _maybe_rotate_session(self) -> None:
        """长跑时定期“重载页面”，避免一个 page_load_timestamp 用上几个小时。"""
        if time.monotonic() - self._session_started_at >= self.SESSION_ROTATE_SECONDS:
            utils.logger.info(
                "[XhsSigner] 签名会话已超过 %.0fs，模拟一次页面重载以刷新会话状态",
                self.SESSION_ROTATE_SECONDS,
            )
            self.reset_session()

    # ------------------------------------------------------------------ 签名
    def sign_headers(
        self,
        method: str,
        uri: str,
        cookie_str: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """生成完整签名头（小写 key，与浏览器在 HTTP/2 上的形态一致）。"""
        if not _XHSHOW_AVAILABLE or self._client is None:
            return {}

        self._maybe_rotate_session()

        method_upper = method.upper()
        payload = data if isinstance(data, dict) else {}

        if method_upper == "GET":
            headers = self._client.sign_headers_get(
                uri=uri,
                cookies=cookie_str,
                params=payload,
                session=self._session,
            )
        else:
            headers = self._client.sign_headers_post(
                uri=uri,
                cookies=cookie_str,
                payload=payload,
                session=self._session,
            )

        headers = dict(headers)

        # 用浏览器里的真实 b1 覆盖 x-s-common（库默认会现场合成一份假指纹）。
        real_x_s_common = self._build_x_s_common(cookie_str)
        if real_x_s_common:
            headers["x-s-common"] = real_x_s_common

        if not headers.get("x-b3-traceid"):
            headers["x-b3-traceid"] = get_trace_id()

        return headers

    def _build_x_s_common(self, cookie_str: str) -> Optional[str]:
        """用真实 b1 构造 x-s-common。

        结构与 xhshow 的 ``XsCommonSigner`` 一致：
        ``{s0, s1, x0..x11}`` 外加 ``x5=a1``、``x8=b1``、``x9=crc32(b1)``。
        只有同时拿到 a1 与 b1 时才覆盖，否则返回 None 让库走默认逻辑。
        """
        if not _XHSHOW_AVAILABLE or CRC32 is None or Base64Encoder is None:
            return None
        if not self._profile.b1:
            return None

        a1 = _parse_cookie_dict(cookie_str).get("a1", "")
        if not a1:
            return None

        try:
            template = dict(self._config.SIGNATURE_XSCOMMON_TEMPLATE)
            template["x5"] = a1
            template["x8"] = self._profile.b1
            template["x9"] = CRC32.crc32_js_int(self._profile.b1)
            payload = json.dumps(template, separators=(",", ":"), ensure_ascii=False)
            return Base64Encoder(self._config).encode(payload)
        except Exception as exc:  # noqa: BLE001 - 失败时退回库默认实现
            utils.logger.warning(f"[XhsSigner] 使用真实 b1 生成 x-s-common 失败: {exc}")
            return None


def sign_with_xhshow(
    uri: str,
    data: Optional[Union[Dict, str]] = None,
    cookie_str: str = "",
    method: str = "POST",
) -> Dict[str, Any]:
    """
    使用 xhshow 纯算法生成完整签名请求头（保留的旧版公共接口）。

    新代码请用 ``XhsSigner``（复用会话状态与浏览器身份）；这个函数保持原有签名，
    仅供外部调用方兼容，行为与改动前一致。

    Args:
        uri: API path
        data: Request data (GET params dict or POST payload dict)
        cookie_str: Cookie string
        method: Request method (GET or POST)

    Returns:
        Dictionary containing x-s, x-t, x-s-common, x-b3-traceid
    """
    from xhshow import Xhshow

    xhshow_client = Xhshow()

    if method.upper() == "POST":
        headers = xhshow_client.sign_headers_post(
            uri=uri,
            cookies=cookie_str,
            payload=data if isinstance(data, dict) else {},
        )
    else:
        headers = xhshow_client.sign_headers_get(
            uri=uri,
            cookies=cookie_str,
            params=data if isinstance(data, dict) else {},
        )

    return {
        "x-s": headers.get("x-s", ""),
        "x-t": headers.get("x-t", ""),
        "x-s-common": headers.get("x-s-common", ""),
        "x-b3-traceid": headers.get("x-b3-traceid", get_trace_id()),
    }
