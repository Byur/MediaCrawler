# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/browser_profile.py
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

"""浏览器指纹采集。

反爬/风控视角下最重要的一条原则：**同一个会话里，HTTP 头、签名、UA-CH、
localStorage 里的设备指纹必须描述同一台设备**。项目原先在 core.py 里硬编码了
一套 "Mac Chrome 126/137 + Windows UA-CH" 的组合，x-s/x-s-common 里又写着
Windows，服务端只要做一次交叉校验就能命中。

本模块只做两件事：在真实浏览器里把下面这些值取出来，供 HTTP 头与签名共同使用。

- ``user_agent``        —— navigator.userAgent（与浏览器实际发出的 UA 头一致）
- ``ua_brands``         —— sec-ch-ua（来自 navigator.userAgentData.brands）
- ``ua_platform``       —— sec-ch-ua-platform（带引号，如 ``"macOS"``）
- ``ua_mobile``         —— sec-ch-ua-mobile（``?0`` / ``?1``）
- ``accept_language``   —— accept-language（来自 navigator.languages）
- ``platform``          —— 小红书 x-s / x-s-common 里的 ``x2`` 字段（"Mac OS"/"Windows"/"Linux"）
- ``b1``                —— localStorage 里的设备指纹，浏览器自己算出来的那一份

**不编造浏览器值**：某个头没采到就留空，由调用方省略该头。发一个缺失的头，
比发一个和 UA 对不上的头安全。只有 ``b1`` 缺失时会退回 xhshow 自己合成的指纹。
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tools import utils

__all__ = [
    "BrowserProfile",
    "build_browser_profile",
    "capture_browser_profile",
    "identity_conflicts",
    "profile_from_user_agent",
]


# 小红书签名里的 x2 字段取值。老实现（secsign.py）在 macOS 上用的是 "Mac OS"。
_XHS_PLATFORM_BY_UA_CH = {
    "macos": "Mac OS",
    "mac os x": "Mac OS",
    "windows": "Windows",
    "linux": "Linux",
    "chrome os": "Linux",
    "android": "Android",
    "ios": "iOS",
}

@dataclass(frozen=True)
class BrowserProfile:
    """一次采集得到的、内部自洽的浏览器身份。"""

    user_agent: str = ""
    platform: str = ""  # x-s / x-s-common 的 x2
    ua_brands: str = ""  # sec-ch-ua
    ua_platform: str = ""  # sec-ch-ua-platform，带引号
    ua_mobile: str = "?0"  # sec-ch-ua-mobile
    accept_language: str = ""  # accept-language
    b1: str = ""  # localStorage['b1']
    source: str = "fallback"  # browser / fallback，便于日志排查

    @property
    def has_real_b1(self) -> bool:
        return bool(self.b1)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "user_agent": self.user_agent,
            "platform": self.platform,
            "ua_brands": self.ua_brands,
            "ua_platform": self.ua_platform,
            "ua_mobile": self.ua_mobile,
            "accept_language": self.accept_language,
            "has_real_b1": self.has_real_b1,
            "source": self.source,
        }


_CAPTURE_SCRIPT = """
() => {
  const out = {};
  try { out.ua = navigator.userAgent || ""; } catch (e) { out.ua = ""; }
  try {
    const langs = navigator.languages;
    out.langs = (langs && langs.length) ? Array.from(langs) : [navigator.language || ""];
  } catch (e) { out.langs = []; }
  try {
    const d = navigator.userAgentData;
    out.brands = (d && d.brands) ? d.brands.map((b) => [String(b.brand), String(b.version)]) : null;
    out.platform = d ? String(d.platform || "") : "";
    out.mobile = d ? !!d.mobile : false;
  } catch (e) { out.brands = null; out.platform = ""; out.mobile = false; }
  try { out.b1 = localStorage.getItem("b1") || ""; } catch (e) { out.b1 = ""; }
  return out;
}
"""


_B1_SCRIPT = """
() => {
  try { return localStorage.getItem("b1") || ""; } catch (e) { return ""; }
}
"""


def _platform_from_user_agent(user_agent: str) -> str:
    """从 UA 推断 UA-CH 平台名（macOS / Windows / Linux ...）。"""
    ua = user_agent or ""
    if "Windows NT" in ua:
        return "Windows"
    if "Macintosh" in ua or "Mac OS X" in ua or "Mac OS" in ua:
        return "macOS"
    if "Android" in ua:
        return "Android"
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "iOS"
    if "CrOS" in ua:
        return "Chrome OS"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return ""


def xhs_platform_from_ua_platform(ua_platform: str) -> str:
    """UA-CH 平台名 -> 小红书 x-s 的 x2 字段。"""
    return _XHS_PLATFORM_BY_UA_CH.get((ua_platform or "").strip().lower(), "")


def _ua_major(user_agent: str) -> str:
    """UA 里的浏览器大版本号（Chrome / Chromium / Edge / HeadlessChrome）。"""
    match = re.search(r"(?:HeadlessChrome|Chrome|Chromium|Edg)/(\d+)", user_agent or "")
    return match.group(1) if match else ""


def identity_conflicts(profile: BrowserProfile) -> List[str]:
    """检查一份身份在「服务端可见」的层面是否自相矛盾。

    只报告问题，不修改任何值。UA、UA-CH、签名平台必须描述同一台设备：
    版本号对不上、平台对不上、或者 UA 自带 HeadlessChrome，都是可被判定的信号。
    """
    conflicts: List[str] = []
    ua = profile.user_agent or ""

    if "HeadlessChrome" in ua:
        conflicts.append(
            "UA 含 HeadlessChrome（headless 浏览器自报家门），建议改用 CDP 模式或 HEADLESS=False"
        )

    if ua and not profile.platform:
        conflicts.append("无法从身份推导平台，签名 x2 会退回 xhshow 默认的 Windows")

    if not profile.ua_brands:
        return conflicts

    brand_majors = re.findall(r'v="(\d+)"', profile.ua_brands)
    ua_major = _ua_major(ua)
    if ua_major and brand_majors and ua_major not in brand_majors:
        conflicts.append(
            f"UA 大版本 {ua_major} 不在 sec-ch-ua 的版本 {brand_majors} 里"
        )

    ch_platform = (profile.ua_platform or "").strip('"')
    ua_platform = _platform_from_user_agent(ua)
    if ch_platform and ua_platform and ch_platform != ua_platform:
        conflicts.append(
            f"UA 平台 {ua_platform} 与 sec-ch-ua-platform {ch_platform} 不一致"
        )

    return conflicts


def build_browser_profile(
    *,
    user_agent: str = "",
    brands: Optional[List[List[str]]] = None,
    ua_platform: str = "",
    mobile: bool = False,
    languages: Optional[List[str]] = None,
    b1: str = "",
    source: str = "fallback",
) -> BrowserProfile:
    """把采集到的原始值组装成自洽的 BrowserProfile。

    只做「拼接 + 平台映射」，不编造浏览器值：某个头没采到就留空，
    由调用方省略该头（缺失一个头比发一个自相矛盾的头更安全）。
    """
    ua = (user_agent or "").strip()
    ua_platform = (ua_platform or "").strip() or _platform_from_user_agent(ua)

    ua_brands = (
        ", ".join(f'"{brand}";v="{version}"' for brand, version in brands if brand)
        if brands
        else ""
    )
    language_header = ",".join(
        lang.strip() for lang in (languages or []) if lang and lang.strip()
    )

    return BrowserProfile(
        user_agent=ua,
        platform=xhs_platform_from_ua_platform(ua_platform),
        ua_brands=ua_brands,
        ua_platform=f'"{ua_platform}"' if ua_platform else "",
        ua_mobile="?1" if mobile else "?0",
        accept_language=language_header,
        b1=(b1 or "").strip(),
        source=source,
    )


def profile_from_user_agent(user_agent: str, b1: str = "") -> BrowserProfile:
    """仅凭 UA 构造兜底身份（无浏览器时使用，也便于单测）。"""
    return build_browser_profile(user_agent=user_agent, b1=b1, source="fallback")


async def capture_browser_profile(
    page: Any,
    *,
    fallback_user_agent: str = "",
    wait_for_b1_seconds: float = 0.0,
    poll_interval_seconds: float = 0.3,
) -> BrowserProfile:
    """从 Playwright Page 采集浏览器身份。

    Args:
        page: Playwright ``Page``；为 None 时直接走 UA 兜底。
        fallback_user_agent: 页面不可用时使用的 UA。
        wait_for_b1_seconds: 等待 ``localStorage['b1']`` 出现的时长。首页 SDK 初始化
            是异步的，b1 可能晚于 ``goto`` 才写入；登录后重新采集时通常已经有了。

    Returns:
        BrowserProfile。任何异常都不会向上抛，最差返回 UA 兜底身份。
    """
    if page is None:
        return profile_from_user_agent(fallback_user_agent)

    try:
        raw: Dict[str, Any] = await page.evaluate(_CAPTURE_SCRIPT)
    except Exception as exc:  # noqa: BLE001 - 采集失败不应影响爬取主流程
        utils.logger.warning(
            f"[BrowserProfile] 采集浏览器指纹失败，改用 UA 兜底: {exc}"
        )
        return profile_from_user_agent(fallback_user_agent)

    if not isinstance(raw, dict):
        raw = {}

    user_agent = str(raw.get("ua") or fallback_user_agent or "")
    b1 = str(raw.get("b1") or "")

    if not b1 and wait_for_b1_seconds > 0:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_for_b1_seconds
        while not b1 and loop.time() < deadline:
            await asyncio.sleep(poll_interval_seconds)
            try:
                b1 = str(await page.evaluate(_B1_SCRIPT) or "")
            except Exception:  # noqa: BLE001
                break

    brands = raw.get("brands")
    return build_browser_profile(
        user_agent=user_agent,
        brands=brands if isinstance(brands, list) else None,
        ua_platform=str(raw.get("platform") or ""),
        mobile=bool(raw.get("mobile")),
        languages=[str(x) for x in (raw.get("langs") or [])],
        b1=b1,
        source="browser",
    )
