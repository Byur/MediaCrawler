# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/routers/label.py
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

"""标注工作台 API。

把 data/<platform>/json 下的 contents / comments 还原成"可阅读的帖子"，
并提供判据打标的读写。只读本地已落盘数据，不发起任何网络请求。
"""

import itertools
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/label", tags=["label"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LABEL_DIR = DATA_DIR / "label"
VERDICT_FILE = LABEL_DIR / "verdicts.jsonl"

# 判据权重：只用于排序，不代表真实性概率
SIGNAL_WEIGHTS: dict[str, int] = {
    "staging": 25,   # A 摆拍图片
    "profile": 25,   # B 主页历史
    "matrix": 25,    # D 矩阵
    "language": 15,  # C 语言风格
    "ratio": 10,     # E 互动比值
}

TZ = timezone(timedelta(hours=8))
IMAGE_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png", ".gif"}

# platform 会参与本地路径拼接，必须是白名单字符（防路径穿越）
PLATFORM_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
VERDICT_LEVELS = {"normal", "suspect", "strong"}
_verdict_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _safe_platform(platform: str) -> str:
    if not PLATFORM_RE.fullmatch(platform or ""):
        raise HTTPException(status_code=400, detail="invalid platform")
    return platform


# ---------------------------------------------------------------- 数据读取

def _iter_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix in {".json", ".jsonl"})


def _read_records(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix == ".jsonl":
            rows = []
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            return rows
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return [data]
        return [r for r in data if isinstance(r, dict)]
    except Exception:
        return []


def _data_dirs(platform: str) -> list[Path]:
    base = DATA_DIR / platform
    return [base / "json", base / "jsonl"]


def _load_contents(platform: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in _data_dirs(platform):
        for path in _iter_json_files(directory):
            if "contents" not in path.name:
                continue
            rows.extend(_read_records(path))

    # 同一 note_id 只保留最后一条
    dedup: dict[str, dict[str, Any]] = {}
    for row in rows:
        note_id = row.get("note_id")
        if note_id:
            dedup[str(note_id)] = row
    return list(dedup.values())


def _load_comments(platform: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for directory in _data_dirs(platform):
        for path in _iter_json_files(directory):
            if "comments" not in path.name:
                continue
            for row in _read_records(path):
                note_id = row.get("note_id")
                if note_id:
                    grouped.setdefault(str(note_id), []).append(row)
    return grouped


def _media_images(platform: str, note_id: str) -> list[str]:
    note_dir = DATA_DIR / platform / "media" / note_id
    if not note_dir.is_dir():
        return []
    names = sorted(
        p.name for p in note_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    return [f"/api/label/media/{platform}/{note_id}/{name}" for name in names[:9]]


def _load_verdicts() -> dict[str, dict[str, Any]]:
    """读取标注文件（append-only，同一 note_id 以最后一条为准）。

    单行损坏只跳过该行并记 WARNING：不能因为一行坏数据返回空 dict，
    否则下一次保存会把历史标注全部覆盖掉。
    """
    if not VERDICT_FILE.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        with VERDICT_FILE.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "%s 第 %d 行不是合法 JSON，已跳过: %s",
                        VERDICT_FILE, lineno, exc,
                    )
                    continue
                if isinstance(row, dict) and row.get("note_id"):
                    result[str(row["note_id"])] = row
    except OSError as exc:
        logger.warning("读取 %s 失败: %s", VERDICT_FILE, exc)
    return result


# ---------------------------------------------------------------- 工具

def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _iso(ms: Any) -> Optional[str]:
    stamp = _as_int(ms, 0)
    if stamp <= 0:
        return None
    # 兼容秒/毫秒
    if stamp < 10_000_000_000:
        stamp *= 1000
    return datetime.fromtimestamp(stamp / 1000, tz=TZ).isoformat()


def compute_score(signals: dict[str, Any]) -> int:
    """把 0/1/2 的判据状态折算成 0–100 的排序分。仅用于排序。"""
    total = sum(SIGNAL_WEIGHTS.values()) * 2
    if total <= 0:
        return 0
    earned = 0
    for key, weight in SIGNAL_WEIGHTS.items():
        level = _as_int(signals.get(key), 0)
        level = max(0, min(2, level))
        earned += weight * level
    return round(earned / total * 100)


def _build_post(
    row: dict[str, Any],
    comments: list[dict[str, Any]],
    verdict: Optional[dict[str, Any]],
    platform: str,
) -> dict[str, Any]:
    note_id = str(row.get("note_id"))
    top_comments = sorted(
        comments, key=lambda c: _as_int(c.get("like_count"), 0), reverse=True
    )[:12]
    images = _media_images(platform, note_id)

    return {
        "note_id": note_id,
        "type": row.get("type") or "normal",
        "title": row.get("title") or "",
        "desc": row.get("desc") or "",
        "tags": _as_tags(row.get("tag_list")),
        "author": row.get("creator_hash") or "",
        "nickname": row.get("nickname") or "",
        "published_at": _iso(row.get("time")),
        "updated_at": _iso(row.get("last_update_time")),
        "liked": _as_int(row.get("liked_count")),
        "collected": _as_int(row.get("collected_count")),
        "comments_count": _as_int(row.get("comment_count")),
        "share": _as_int(row.get("share_count")),
        "images": images,
        "has_media": bool(images),
        "note_url": row.get("note_url") or "",
        "source_keyword": row.get("source_keyword") or "",
        "comments": [
            {
                "author": c.get("creator_hash") or "",
                "nickname": c.get("nickname") or "",
                "content": c.get("content") or "",
                "likes": _as_int(c.get("like_count")),
                "at": _iso(c.get("create_time")),
            }
            for c in top_comments
        ],
        "verdict": verdict,
    }


# ---------------------------------------------------------------- 路由

@router.get("/posts")
async def list_posts(
    platform: str = "xhs",
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    only_unlabeled: bool = False,
):
    """返回可标注的帖子列表（含图片、前排评论、已有标注）。"""
    platform = _safe_platform(platform)
    contents = _load_contents(platform)
    if not contents:
        return {
            "posts": [],
            "total": 0,
            "platform": platform,
            "message": f"未在 data/{platform}/json 下找到 contents 数据",
        }

    verdicts = _load_verdicts()
    # 先按发布时间倒序、过滤，再只为当前窗口组装完整 post：
    # 避免为全部笔记列 media 目录 / 排评论（数据量大时差别明显）。
    contents.sort(key=lambda row: _as_int(row.get("time")), reverse=True)
    if only_unlabeled:
        contents = [row for row in contents if str(row.get("note_id")) not in verdicts]

    total = len(contents)
    window_rows = contents[offset: offset + limit]
    comments_map = _load_comments(platform)
    posts = [
        _build_post(
            row,
            comments_map.get(str(row.get("note_id")), []),
            verdicts.get(str(row.get("note_id"))),
            platform,
        )
        for row in window_rows
    ]
    return {"posts": posts, "total": total, "platform": platform}


@router.get("/media/{platform}/{note_id}/{filename}")
async def get_media(platform: str, note_id: str, filename: str):
    """按 note_id 返回本地已下载的图片。"""
    platform = _safe_platform(platform)
    for value in (note_id, filename):
        if "/" in value or "\\" in value or value in {"..", "."}:
            raise HTTPException(status_code=400, detail="invalid path")

    note_dir = (DATA_DIR / platform / "media" / note_id).resolve()
    target = (note_dir / filename).resolve()
    if not str(target).startswith(str(note_dir)):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="media not found")

    media_type = "image/webp" if target.suffix.lower() == ".webp" else None
    return FileResponse(target, media_type=media_type)


FOOD_TOKENS = (
    "探店", "美食", "餐厅", "好吃", "烤肉", "火锅", "咖啡",
    "外卖", "攻略", "小吃", "宝藏", "吃", "店",
)


def _looks_like_food(note: dict[str, Any]) -> bool:
    text = (note.get("title") or "") + str(note.get("tag_list") or "")
    return any(token in text for token in FOOD_TOKENS)


def _shingles(text: str, n: int = 3) -> set[str]:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return {cleaned[i:i + n] for i in range(max(0, len(cleaned) - n + 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@router.get("/graph")
async def get_graph(
    note_id: str = "",
    platform: str = "xhs",
    max_commenters: int = Query(12, ge=1, le=50),
    depth: int = Query(2, ge=2, le=3),
    max_peer_comments: int = Query(5, ge=0, le=20),
    max_peer_notes: int = Query(24, ge=0, le=100),
):
    """以一篇笔记为中心的关系图。

    depth=2：中心 → 评论者 → 评论者自己的内容
    depth=3：继续展开「同行笔记的评论区」，用来发现跨笔记重复出现的同一批人
    """
    platform = _safe_platform(platform)
    contents = _load_contents(platform)
    if not contents:
        raise HTTPException(status_code=404, detail=f"no contents in data/{platform}")

    by_id = {str(c.get("note_id")): c for c in contents}
    if note_id:
        center = by_id.get(note_id)
        if center is None:
            raise HTTPException(
                status_code=404, detail=f"note {note_id} not found in data/{platform}"
            )
    else:
        center = max(contents, key=lambda c: _as_int(c.get("comment_count")))
    cid = str(center.get("note_id"))

    comments_map = _load_comments(platform)
    comments = sorted(comments_map.get(cid, []), key=lambda c: _as_int(c.get("create_time")))
    ordered: list[str] = []
    for c in comments:
        h = str(c.get("creator_hash") or "")
        if h and h not in ordered:
            ordered.append(h)
    ordered = ordered[: max(1, max_commenters)]

    by_author: dict[str, list[dict[str, Any]]] = {}
    for row in contents:
        by_author.setdefault(str(row.get("creator_hash") or ""), []).append(row)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        if node["id"] not in seen:
            seen.add(node["id"])
            nodes.append(node)

    def add_edge(source: str, target: str, kind: str, weight: float) -> None:
        edges.append({"source": source, "target": target, "kind": kind, "weight": weight})

    note_node = f"n:{cid}"
    add_node({
        "id": note_node,
        "kind": "note",
        "label": (center.get("title") or "（无标题）")[:24],
        "sub": f"赞{_as_int(center.get('liked_count'))} 评{_as_int(center.get('comment_count'))}",
        "r": 3.0,
        "hop": 0,
        "center": True,
    })

    author_hash = str(center.get("creator_hash") or "")
    author_node = f"u:{author_hash}"
    add_node({
        "id": author_node,
        "kind": "author",
        "label": center.get("nickname") or author_hash[:8],
        "sub": author_hash[:8],
        "r": 2.0,
        "hop": 1,
    })
    add_edge(author_node, note_node, "own", 1.0)

    peers = 0
    hop1_ids: dict[str, str] = {}
    pnote_ids: list[str] = []

    for h in ordered:
        mine = [r for r in by_author.get(h, []) if str(r.get("note_id")) != cid]
        food = [r for r in mine if _looks_like_food(r)]
        is_peer = len(mine) >= 2 and len(food) * 2 >= len(mine)
        if is_peer:
            peers += 1
        user_node = f"u:{h}"
        add_node({
            "id": user_node,
            "kind": "peer" if is_peer else "user",
            "label": f"同行·{h[:6]}" if is_peer else h[:8],
            "sub": f"{len(mine)} 篇内容",
            "r": 1.5 if is_peer else 1.1,
            "hop": 1,
        })
        hop1_ids[h] = user_node
        add_edge(user_node, note_node, "comment", 1.0)

        for note in mine[:6]:
            pn_id = str(note.get("note_id"))
            pn = f"n:{pn_id}"
            add_node({
                "id": pn,
                "kind": "pnote",
                "label": (note.get("title") or "")[:18],
                "sub": "",
                "r": 0.7,
                "hop": 2,
            })
            add_edge(user_node, pn, "own", 0.6)
            pnote_ids.append(pn_id)

    # ---- 第三跳：同行笔记的评论区
    shared: set[str] = set()
    hop3_users = 0
    back_edges = 0
    if depth >= 3:
        for pn_id in pnote_ids[:max_peer_notes]:
            src = f"n:{pn_id}"
            rows = sorted(
                comments_map.get(pn_id, []),
                key=lambda c: _as_int(c.get("create_time")),
            )[:max_peer_comments]
            for c in rows:
                h = str(c.get("creator_hash") or "")
                if not h or h == author_hash:
                    continue
                if h in hop1_ids:
                    add_edge(src, hop1_ids[h], "back", 1.0)
                    shared.add(h)
                    back_edges += 1
                else:
                    nid = f"u2:{h}"
                    if nid not in seen:
                        hop3_users += 1
                    add_node({
                        "id": nid,
                        "kind": "user2",
                        "label": h[:6],
                        "sub": f"{pn_id[:6]} 的评论者",
                        "r": 0.9,
                        "hop": 3,
                    })
                    add_edge(src, nid, "comment2", 1.0)

    # ---- 内容相似（笔记之间）
    sim_edges = 0
    note_nodes = [n for n in nodes if n["kind"] in ("note", "pnote")]
    text_of: dict[str, set[str]] = {}
    for n in note_nodes:
        raw = by_id.get(n["id"][2:])
        if raw is not None:
            text_of[n["id"]] = _shingles((raw.get("title") or "") + (raw.get("desc") or ""))
    for a, b in itertools.combinations(list(text_of), 2):
        if sim_edges >= 40:
            break
        pair_score = _jaccard(text_of[a], text_of[b])
        if pair_score >= 0.34:
            add_edge(a, b, "sim", round(pair_score, 2))
            sim_edges += 1

    total_commenters = len(ordered)
    return {
        "center": cid,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "commenters": total_commenters,
            "peers": peers,
            "peer_ratio": round(peers / total_commenters, 2) if total_commenters else 0,
            "shared_commenters": len(shared),
            "back_edges": back_edges,
            "hop3_users": hop3_users,
            "nodes": len(nodes),
            "edges": len(edges),
            "sim_edges": sim_edges,
        },
    }


@router.post("/verdict")
async def save_verdict(payload: dict[str, Any] = Body(...)):
    """保存一条标注（append-only，读取时同一 note_id 以最后一条为准）。"""
    note_id = str(payload.get("note_id") or "").strip()
    if not note_id:
        raise HTTPException(status_code=400, detail="note_id required")

    level = str(payload.get("level") or "normal")
    if level not in VERDICT_LEVELS:
        raise HTTPException(status_code=400, detail=f"invalid level: {level}")

    signals = payload.get("signals") or {}
    if not isinstance(signals, dict):
        raise HTTPException(status_code=400, detail="signals must be an object")

    record = {
        "note_id": note_id,
        "level": level,
        "signals": {key: max(0, min(2, _as_int(signals.get(key), 0))) for key in SIGNAL_WEIGHTS},
        "memo": (payload.get("memo") or "")[:2000],
        "saved_at": datetime.now(tz=TZ).isoformat(),
    }
    record["score"] = compute_score(record["signals"])

    # append-only：避免 read-modify-write 在并发/坏行时丢标注，也避免写一半截断文件
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _verdict_lock:
        with VERDICT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)

    return {"ok": True, "verdict": record}


@router.get("/stats")
async def label_stats(platform: str = "xhs"):
    """标注进度。"""
    platform = _safe_platform(platform)
    contents = _load_contents(platform)
    verdicts = _load_verdicts()

    labeled = sum(1 for row in contents if str(row.get("note_id")) in verdicts)
    levels: dict[str, int] = {"normal": 0, "suspect": 0, "strong": 0}
    for row in contents:
        verdict = verdicts.get(str(row.get("note_id")))
        if verdict:
            key = verdict.get("level") or "normal"
            levels[key] = levels.get(key, 0) + 1

    try:
        verdict_file = str(VERDICT_FILE.relative_to(PROJECT_ROOT))
    except ValueError:
        verdict_file = str(VERDICT_FILE)

    return {
        "platform": platform,
        "total": len(contents),
        "labeled": labeled,
        "unlabeled": len(contents) - labeled,
        "levels": levels,
        "verdict_file": verdict_file,
    }
