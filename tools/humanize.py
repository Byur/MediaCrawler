# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/humanize.py
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

"""请求节奏工具：把固定间隔换成有界随机间隔。

固定间隔的请求到达方差接近 0，是最容易被规则化识别的行为特征之一。
这里在基准间隔上施加 ±50% 的均匀抖动：``CRAWLER_MAX_SLEEP_SEC = 2`` 时，
每次请求之间等待 1~3 秒的随机数。

只改变请求之间的间隔，不改变单个请求的行为。
"""

import asyncio
import random
from typing import Optional

import config

__all__ = ["polite_sleep", "next_delay"]

JITTER_MIN_RATIO = 0.5
JITTER_MAX_RATIO = 1.5


def next_delay(base_seconds: Optional[float] = None) -> float:
    """计算下一次 sleep 的时长（不实际 sleep），便于单测与日志。"""
    base = config.CRAWLER_MAX_SLEEP_SEC if base_seconds is None else base_seconds
    base = max(0.0, float(base))

    return base * random.uniform(JITTER_MIN_RATIO, JITTER_MAX_RATIO)


async def polite_sleep(base_seconds: Optional[float] = None) -> float:
    """带抖动的 sleep，返回实际等待秒数。

    Args:
        base_seconds: 基准间隔；None 表示使用 ``config.CRAWLER_MAX_SLEEP_SEC``。
    """
    delay = next_delay(base_seconds)
    if delay > 0:
        await asyncio.sleep(delay)
    return delay
