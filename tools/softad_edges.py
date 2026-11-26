# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# 本文件为 MediaCrawler 教学版的一部分。

"""「边表」：creator_hash ↔ user_id，用于把评论区里的人跳转到他的主页。

设计取向
--------
- **默认不写盘**。只有环境变量 ``SOFTAD_EDGE_MAP=1`` 时才记录。
- 只记录**身份指针**，不记录身份档案：一行只有 hash / user_id / 类型 / 时间。
  不存昵称、不存头像、不存任何内容。
- 它是一个**临时跳板**，不是资产。用完请 ``prune`` 或直接删除。

为什么只需要 user_id
--------------------
判断「矩阵」靠的是追边：A 的评论区里出现了 B，去看 B 的主页是不是同类账号。
所以只需要一个能跳到主页的钥匙；账号特征可以按需现抓，不必预先入库。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from tools.user_hash import anonymize_user_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FLAG = "SOFTAD_EDGE_MAP"

_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv(ENV_FLAG) == "1"


def edges_path(platform: str) -> Path:
    return PROJECT_ROOT / "data" / platform / "edges" / "user_map.jsonl"


def record_edge(
    kind: str,
    user_id: Any,
    platform: str = "xhs",
    nickname: Optional[str] = None,
) -> None:
    """记录一条 hash → user_id。未开启 flag 时什么都不做。

    kind: "note"（作者）或 "comment"（评论者），仅用于后续区分来源。
    nickname 参数保留但**不写入**，避免无意中落身份信息。
    """
    if not enabled():
        return
    user_id = str(user_id or "").strip()
    if not user_id:
        return

    row = {
        "hash": anonymize_user_id(user_id),
        "user_id": user_id,
        "kind": kind,
        "ts": int(time.time()),
    }
    path = edges_path(platform)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        # 边表绝不能影响主流程
        pass


def load_map(platform: str = "xhs") -> dict[str, str]:
    """读回 hash → user_id（同 hash 多次出现取最后一条）。"""
    path = edges_path(platform)
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("hash") and row.get("user_id"):
                    result[row["hash"]] = row["user_id"]
    except Exception:
        return result
    return result


def prune(platform: str = "xhs", days: int = 7) -> int:
    """删掉超过 days 天的记录，返回删除条数。"""
    path = edges_path(platform)
    if not path.exists():
        return 0
    cutoff = int(time.time()) - days * 86400
    kept: list[str] = []
    removed = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if int(row.get("ts", 0)) < cutoff:
                removed += 1
            else:
                kept.append(line)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + ("\n" if kept else ""))
    return removed


# ---------------------------------------------------------------- CLI

def _load_comments(platform: str = "xhs") -> list[dict[str, Any]]:
    """扫描 data/<platform>/json 与 jsonl 下的评论文件。"""
    base = PROJECT_ROOT / "data" / platform
    rows: list[dict[str, Any]] = []
    for directory in (base / "json", base / "jsonl"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if "comment" not in path.name or path.suffix not in {".json", ".jsonl"}:
                continue
            try:
                if path.suffix == ".jsonl":
                    with path.open(encoding="utf-8") as fh:
                        rows.extend(json.loads(l) for l in fh if l.strip())
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    rows.extend(data if isinstance(data, list) else [data])
            except Exception:
                continue
    return [r for r in rows if isinstance(r, dict) and r.get("note_id")]


def expand(note_id: str, platform: str = "xhs", limit: int = 5) -> list[dict[str, Any]]:
    """给一篇笔记，返回前排评论者的主页链接（按评论时间从早到晚）。"""
    mapping = load_map(platform)
    rows = [r for r in _load_comments(platform) if str(r.get("note_id")) == note_id]
    rows.sort(key=lambda r: int(r.get("create_time") or 0))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        h = str(row.get("creator_hash") or "")
        if not h or h in seen:
            continue
        seen.add(h)
        user_id = mapping.get(h)
        out.append({
            "hash": h,
            "user_id": user_id,
            "comment": (row.get("content") or "")[:40],
            "create_time": row.get("create_time"),
            "profile": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else None,
        })
        if len(out) >= limit:
            break
    return out


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="边表工具：hash ↔ user_id")
    parser.add_argument("action", choices=["expand", "stats", "prune"])
    parser.add_argument("--note", help="expand 用：笔记 ID")
    parser.add_argument("--platform", default="xhs")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    if args.action == "expand" and not load_map(args.platform):
        print(f"[提示] 边表为空。采集时请带上 {ENV_FLAG}=1。")

    if args.action == "stats":
        path = edges_path(args.platform)
        mapping = load_map(args.platform)
        size = path.stat().st_size if path.exists() else 0
        print(f"文件: {path}")
        print(f"记录: {len(mapping)} 个账号 | 体积: {size} 字节")
        return

    if args.action == "prune":
        print(f"已删除 {prune(args.platform, args.days)} 条超过 {args.days} 天的记录")
        return

    if not args.note:
        parser.error("expand 需要 --note")
    rows = expand(args.note, args.platform, args.limit)
    if not rows:
        print("没有找到该笔记的评论，或边表为空")
        return

    print(f"笔记 {args.note} 的前 {len(rows)} 个评论者：\n")
    urls = []
    for r in rows:
        print(f"  {r['hash'][:8]} | {r['comment']!r}")
        print(f"    {r['profile'] or '（边表里没有这个 hash，需重新采集）'}")
        if r["profile"]:
            urls.append(r["profile"])

    if urls:
        print("\n可直接用于二跳采集：\n")
        print("  .venv/bin/python -m tools.agent_cli start --platform xhs --type creator \\")
        print(f'    --creator-ids "{",".join(urls)}" --max-notes 5 --comments no')


if __name__ == "__main__":
    _main()
