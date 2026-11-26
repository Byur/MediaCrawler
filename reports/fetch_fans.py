#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补数：抓取「笔记 → 作者粉丝数」。

设计约束（对齐本仓库 privacy 版设定）：
- user_id 只在本进程内存里用于拼主页地址，**绝不落盘**；
- 落盘只有 note_id / fans_raw / fans_int，不含昵称、头像、主页链接、user_id；
- 串行请求 + 1.5~3.0s 随机间隔，命中验证码 / 风控立即停止并保留已得数据。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl-cache")

import re  # noqa: E402

import config  # noqa: E402
from media_platform.xhs import XiaoHongShuCrawler  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

OUT = ROOT / "reports"
STATE = OUT / "author_fans.json"
LIMIT = int(os.environ.get("FANS_LIMIT", "0"))  # 0 = 全部
SLEEP = (1.6, 3.2)


def load_notes():
    data = json.loads((OUT / "doumi_2026.json").read_text(encoding="utf-8"))
    notes = []
    for idx, it in enumerate(data["all_2026"], 1):
        m = re.search(r"xsec_token=([^&]+)", it["url"])
        notes.append(
            {
                "rank": idx,
                "note_id": it["note_id"],
                "xsec_token": m.group(1) if m else "",
                "liked": it["liked"],
                "collected": it["collected"],
                "title": it["title"],
            }
        )
    return notes


def pick_user_id(note_dict):
    """从笔记页 __INITIAL_STATE__ 解析出的 note dict 里取作者指针。"""
    if not isinstance(note_dict, dict):
        return None, None
    user = note_dict.get("user") or note_dict.get("users") or {}
    if not isinstance(user, dict):
        return None, None
    uid = user.get("user_id") or user.get("userId") or user.get("id")
    tok = user.get("xsec_token") or user.get("xsecToken")
    return (str(uid) if uid else None), tok


def pick_fans(user_page_data):
    """userPageData.interactions 里取粉丝数。"""
    if not isinstance(user_page_data, dict):
        return None
    for it in user_page_data.get("interactions") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "")
        typ = str(it.get("type") or "").lower()
        if typ == "fans" or name in ("粉丝", "粉丝数"):
            return it.get("count")
    # 兜底：basicInfo.fans / fansCount
    basic = user_page_data.get("basicInfo") or {}
    for k in ("fans", "fansCount", "fanCount"):
        if basic.get(k) not in (None, ""):
            return basic.get(k)
    return None


def parse_count(v):
    if v in (None, ""):
        return None
    s = str(v).strip()
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"^([\d.]+)\s*亿$", s)
    if m:
        return int(float(m.group(1)) * 100000000)
    m = re.match(r"^([\d.]+)$", s)
    if m:
        return int(float(m.group(1)))
    return None


async def human_sleep():
    await asyncio.sleep(random.uniform(*SLEEP))


def save(results):
    STATE.write_text(
        json.dumps({"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def main() -> int:
    notes = load_notes()
    if LIMIT:
        notes = notes[:LIMIT]

    results = {}
    if STATE.exists():
        try:
            results = json.loads(STATE.read_text(encoding="utf-8")).get("items", {})
        except Exception:  # noqa: BLE001
            results = {}

    todo = [n for n in notes if n["note_id"] not in results]
    print(f"总笔记 {len(notes)}，已有结果 {len(notes) - len(todo)}，待处理 {len(todo)}", flush=True)
    if not todo:
        return 0

    crawler = XiaoHongShuCrawler()
    fan_cache: dict[str, str | None] = {}
    consecutive_fail = 0

    async with async_playwright() as playwright:
        ctx = await crawler.launch_browser_with_cdp(
            playwright, None, None, headless=config.CDP_HEADLESS
        )
        crawler.browser_context = ctx
        crawler.context_page = await ctx.new_page()
        await crawler.context_page.goto(crawler.index_url)
        await crawler._capture_browser_profile()
        client = await crawler.create_xhs_client(None)

        if not await client.pong():
            print("!! 登录态无效，停止（请先扫码登录）", flush=True)
            return 2

        for i, note in enumerate(todo, 1):
            nid = note["note_id"]
            try:
                note_dict = await client.get_note_by_id(
                    nid, "pc_search", note["xsec_token"] or ""
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
                print(f"[{i}/{len(todo)}] {nid} 笔记页失败 -> {msg}", flush=True)
                if any(k in msg for k in ("Captcha", "IPBlock", "PlatformAccess")):
                    print("!! 命中风控/验证码，立即停止", flush=True)
                    save(results)
                    return 3
                consecutive_fail += 1
                if consecutive_fail >= 3:
                    print("!! 连续 3 次失败，停止", flush=True)
                    save(results)
                    return 4
                await human_sleep()
                continue

            uid, utok = pick_user_id(note_dict)
            if not uid:
                print(f"[{i}/{len(todo)}] {nid} 未拿到作者指针（可能验证码页）", flush=True)
                consecutive_fail += 1
                if consecutive_fail >= 3:
                    print("!! 连续失败，疑似风控，停止", flush=True)
                    save(results)
                    return 4
                await human_sleep()
                continue

            if uid in fan_cache:
                fans = fan_cache[uid]
            else:
                await human_sleep()
                fans = None
                try:
                    info = await client.get(
                        "/api/sns/web/v1/user/otherinfo",
                        params={"target_user_id": uid},
                    )
                    fans = pick_fans(info)
                except Exception as exc:  # noqa: BLE001
                    msg = f"{type(exc).__name__}: {exc}"
                    print(f"[{i}/{len(todo)}] {nid} otherinfo 失败 -> {msg}", flush=True)
                    if any(k in msg for k in ("Captcha", "IPBlock", "PlatformAccess")):
                        print("!! 命中风控/验证码，立即停止", flush=True)
                        save(results)
                        return 3
                if fans is None:
                    await human_sleep()
                    try:
                        page_data = await client.get_creator_info(
                            user_id=uid, xsec_token=utok or "", xsec_source="pc_note"
                        )
                        fans = pick_fans(page_data)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{i}/{len(todo)}] {nid} 主页兜底失败 -> {type(exc).__name__}", flush=True)
                fan_cache[uid] = fans
                await human_sleep()

            results[nid] = {
                "note_id": nid,
                "rank": note["rank"],
                "fans_raw": fans,
                "fans": parse_count(fans),
            }
            consecutive_fail = 0
            print(
                f"[{i}/{len(todo)}] #{note['rank']:>2} {nid} 粉丝={fans} "
                f"| {(note['title'] or '')[:24]}",
                flush=True,
            )

        save(results)
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass

    got = sum(1 for v in results.values() if v.get("fans_raw"))
    print(f"完成：{len(results)} 条有结果，其中 {got} 条拿到粉丝数", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
