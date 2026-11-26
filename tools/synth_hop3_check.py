# -*- coding: utf-8 -*-
"""3 跳（hop-3）合成数据验证。

用一个假的 platform 目录造一组可控数据，验证 get_graph(depth=3) 的：
  - 第三圈节点（user2）
  - 回流边（back）：同一人既评论中心帖，又评论同行帖
  - shared_commenters / back_edges / hop3_users 三个指标

不联网、不碰真实数据（写入 data/_synthtest，跑完删除）。

用法:
    .venv/bin/python -m tools.synth_hop3_check            # 验证 + 出图
    .venv/bin/python -m tools.synth_hop3_check --keep     # 保留合成数据
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLATFORM = "_synthtest"
BASE = PROJECT_ROOT / "data" / PLATFORM

# ---- 合成剧本 ----------------------------------------------------------
# 中心帖 N0 的作者 A；三个评论者 U1/U2（同行）与 U3（普通用户）
# 同行笔记 P1a/P2a 的评论区里，出现了 U2/U1（回流）与 U4/U5（第三跳新面孔）
A = "A"
U1, U2, U3, U4, U5 = "U1", "U2", "U3", "U4", "U5"

N0 = "note_center_0000"
P1A, P1B = "note_peer1_a000", "note_peer1_b000"
P2A, P2B = "note_peer2_a000", "note_peer2_b000"


def _note(note_id, author, title, tags, liked=100, comments=10):
    return {
        "note_id": note_id,
        "type": "normal",
        "title": title,
        "desc": title,
        "tag_list": tags,
        "time": 1790000000000,
        "last_update_time": 1790000000000,
        "creator_hash": author,
        "nickname": f"作者{author}",
        "liked_count": str(liked),
        "collected_count": str(liked // 2),
        "comment_count": str(comments),
        "share_count": str(liked // 3),
        "image_list": "",
        "note_url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "source_keyword": "合成",
    }


def _comment(cid, note_id, author, text, ts):
    return {
        "comment_id": cid,
        "note_id": note_id,
        "creator_hash": author,
        "nickname": f"作者{author}",
        "content": text,
        "create_time": ts,
        "like_count": 1,
    }


def build_fixture() -> None:
    if BASE.exists():
        shutil.rmtree(BASE)
    (BASE / "jsonl").mkdir(parents=True)

    contents = [
        _note(N0, A, "武汉探店｜这家新店", "探店,武汉美食,餐厅"),
        _note(P1A, U1, "武汉好吃的烤肉店", "探店,武汉,烤肉"),
        _note(P1B, U1, "武汉火锅店推荐", "探店,武汉,火锅"),
        _note(P2A, U2, "珠海探店｜海边咖啡店", "探店,珠海,咖啡"),
        _note(P2B, U2, "珠海美食攻略", "探店,珠海,美食"),
    ]
    with (BASE / "jsonl" / "synth_contents.jsonl").open("w", encoding="utf-8") as fh:
        for row in contents:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    comments = [
        # 中心帖评论区
        _comment("c0", N0, U1, "看起来不错", 1790000100),
        _comment("c1", N0, U2, "人均多少", 1790000200),
        _comment("c2", N0, U3, "码住", 1790000300),
        # P1a 的评论区：U2 回流 + U4 新面孔
        _comment("c3", P1A, U2, "这家我也去过", 1790000400),
        _comment("c4", P1A, U4, "求地址", 1790000500),
        # P2a 的评论区：U1 回流 + U5 新面孔
        _comment("c5", P2A, U1, "下次去珠海试试", 1790000600),
        _comment("c6", P2A, U5, "好想去", 1790000700),
    ]
    with (BASE / "jsonl" / "synth_comments.jsonl").open("w", encoding="utf-8") as fh:
        for row in comments:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


EXPECTED = {
    "commenters": 3,
    "peers": 2,
    "shared_commenters": 2,   # U1、U2 既评论中心帖，又评论同行帖
    "back_edges": 2,
    "hop3_users": 2,          # U4、U5
}


def verify() -> dict:
    from api.routers.label import get_graph

    # 直接调用路由函数（绕过 FastAPI Query 解析），显式传全部参数
    kwargs = {
        "note_id": N0,
        "platform": PLATFORM,
        "max_commenters": 12,
        "max_peer_comments": 5,
        "max_peer_notes": 24,
    }
    two = asyncio.run(get_graph(depth=2, **kwargs))
    three = asyncio.run(get_graph(depth=3, **kwargs))

    errors: list[str] = []

    # depth=2 不应有任何第三跳痕迹
    for key in ("shared_commenters", "back_edges", "hop3_users"):
        if two["stats"].get(key):
            errors.append(f"depth=2 的 {key} 应为 0，实际 {two['stats'].get(key)}")

    # depth=3 的指标
    for key, expected in EXPECTED.items():
        actual = three["stats"].get(key)
        if actual != expected:
            errors.append(f"{key} 期望 {expected}，实际 {actual}")

    # 回流边必须指向中心帖的评论者节点
    hop1_ids = {n["id"] for n in three["nodes"] if n.get("hop") == 1}
    backs = [e for e in three["edges"] if e["kind"] == "back"]
    for edge in backs:
        if edge["target"] not in hop1_ids:
            errors.append(f"回流边目标不在第一圈: {edge}")
        if not str(edge["source"]).startswith("n:"):
            errors.append(f"回流边源头不是笔记: {edge}")

    # 第三跳节点必须挂在笔记上
    hop3 = [n for n in three["nodes"] if n.get("hop") == 3]
    if len(hop3) != EXPECTED["hop3_users"]:
        errors.append(f"第三圈节点数 {len(hop3)} != {EXPECTED['hop3_users']}")

    return {"errors": errors, "two": two, "three": three}


def render(data: dict, out: Path) -> None:
    """层级示意图（非力导向），只为确认四种边和四层节点都在。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    # 仓库自带中文字体，避免标签变方块
    font_path = PROJECT_ROOT / "docs" / "STZHONGS.TTF"
    if font_path.exists():
        fm.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = fm.FontProperties(fname=str(font_path)).get_name()

    nodes = {n["id"]: n for n in data["nodes"]}
    pos = {"n:" + N0: (0.0, 0.0)}
    ring1 = [n for n in data["nodes"] if n.get("hop") == 1]
    ring2 = [n for n in data["nodes"] if n.get("hop") == 2]
    ring3 = [n for n in data["nodes"] if n.get("hop") == 3]

    import math

    def place(items, radius, offset):
        for i, node in enumerate(items):
            angle = 2 * math.pi * i / max(1, len(items)) + offset
            pos[node["id"]] = (radius * math.cos(angle), radius * math.sin(angle))

    # 每圈错开起始角，避免四圈节点落在同一条射线上
    place(ring1, 1.0, -math.pi / 2)
    place(ring2, 2.1, -math.pi / 2 + 0.55)
    place(ring3, 3.15, -math.pi / 2 + 1.1)

    colors = {"note": "#22d3ee", "author": "#f472b6", "peer": "#fbbf24",
              "user": "#94a3b8", "pnote": "#64748b", "user2": "#a78bfa"}
    edge_color = {"comment": "#22d3ee", "own": "#f472b6", "sim": "#fbbf24",
                  "comment2": "#a78bfa", "back": "#f43f5e"}

    fig, ax = plt.subplots(figsize=(9, 9), facecolor="#0b1020")
    ax.set_facecolor("#0b1020")
    for edge in data["edges"]:
        a = pos.get(edge["source"])
        b = pos.get(edge["target"])
        if not a or not b:
            continue
        ax.plot([a[0], b[0]], [a[1], b[1]],
                color=edge_color.get(edge["kind"], "#64748b"),
                lw=2.4 if edge["kind"] == "back" else 1.0,
                alpha=0.95 if edge["kind"] == "back" else 0.4,
                zorder=2 if edge["kind"] == "back" else 1)
    for node_id, node in nodes.items():
        x, y = pos[node_id]
        ax.scatter(x, y, s=(16 if node["kind"] == "note" else 9) ** 2,
                   c=colors.get(node["kind"], "#94a3b8"),
                   edgecolors="#0b1020", zorder=3)
        if node["kind"] in ("note", "peer", "user2", "author"):
            ax.annotate(node["label"][:12], (x, y), xytext=(0, 11),
                        textcoords="offset points", ha="center",
                        color="#cbd5e1", fontsize=8)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(-3.9, 3.9)
    ax.set_ylim(-3.9, 3.9)
    ax.set_title("hop-3 synthetic fixture (red = back edge)", color="#e2e8f0", fontsize=11)
    plt.tight_layout()
    plt.savefig(out, dpi=130, facecolor="#0b1020")
    print(f"图已保存: {out}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留合成数据目录")
    parser.add_argument("--png", default="/tmp/hop3-synth.png")
    args = parser.parse_args()

    build_fixture()
    try:
        result = verify()
        stats = result["three"]["stats"]
        print("depth=3 指标:")
        for key in ("commenters", "peers", "peer_ratio", "shared_commenters", "back_edges", "hop3_users", "nodes", "edges"):
            print(f"  {key:18s} {stats.get(key)}")
        if result["errors"]:
            print("\n验证失败:")
            for err in result["errors"]:
                print("  ✗", err)
            return 1
        print("\n验证通过 ✓")
        render(result["three"], Path(args.png))
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
