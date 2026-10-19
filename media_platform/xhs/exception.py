# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/exception.py
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


from httpx import RequestError


class DataFetchError(RequestError):
    """something error when fetch"""


class IPBlockError(RequestError):
    """fetch so fast that the server block us ip"""


class PlatformAccessError(RequestError):
    """authentication, rate-limit, or account-security restriction"""

    def __init__(self, *args, status_code: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # 429 与 401/403 的处理策略不同：前者值得退避重试，后者应立即停止。
        self.status_code = status_code


class CaptchaError(RequestError):
    """CAPTCHA challenge (HTTP 461/471) returned instead of the expected payload.

    单独建模的原因：验证码不是"网络抖动"，用相同签名在 1 秒后重放只会加重风控。
    调用方应显著放慢、冷却，必要时人工介入。
    """

    def __init__(self, *args, verify_type: str = "", verify_uuid: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.verify_type = verify_type
        self.verify_uuid = verify_uuid


class NoteNotFoundError(RequestError):
    """Note does not exist or is abnormal"""
