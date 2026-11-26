#!/usr/bin/env python3
"""统计并整理小红书「豆米火锅」相关笔记：筛选 2026 年、去重、按点赞/收藏倒序。"""

import datetime as dt
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
JSONL_DIR = ROOT / "data" / "xhs" / "jsonl"
OUT_DIR = ROOT / "reports"
YEAR = 2026


def parse_count(v):
    """把 '3.8万' / '6533' / '' 解析成 int。"""
    if v is None:
        return 0
    s = str(v).strip()
    if not s:
        return 0
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"^([\d.]+)\s*亿$", s)
    if m:
        return int(float(m.group(1)) * 100000000)
    m = re.match(r"^([\d.]+)$", s)
    if m:
        return int(float(m.group(1)))
    return 0


def load_rows():
    rows = []
    for f in sorted(JSONL_DIR.glob("search_contents_*.jsonl")):
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def is_relevant(r):
    text = " ".join(
        str(x or "")
        for x in (r.get("title"), r.get("desc"), r.get("source_keyword"),
                  " ".join(t.get("name", "") if isinstance(t, dict) else str(t)
                          for t in (r.get("tag_list") or [])))
    )
    return "豆米" in text


def main():
    rows = load_rows()
    rel = [r for r in rows if is_relevant(r)]

    dedup = {}
    for r in rel:
        nid = r.get("note_id")
        if not nid:
            continue
        prev = dedup.get(nid)
        # 保留互动数据更完整的那条
        if prev is None or parse_count(r.get("liked_count")) > parse_count(prev.get("liked_count")):
            dedup[nid] = r

    items = []
    for r in dedup.values():
        ts = r.get("time")
        if not ts:
            continue
        d = dt.datetime.fromtimestamp(ts / 1000)
        items.append({
            "note_id": r["note_id"],
            "date": d.strftime("%Y-%m-%d"),
            "year": d.year,
            "title": (r.get("title") or "").replace("\n", " ").strip(),
            "desc": (r.get("desc") or "").strip(),
            "liked": parse_count(r.get("liked_count")),
            "liked_raw": str(r.get("liked_count") or ""),
            "collected": parse_count(r.get("collected_count")),
            "collected_raw": str(r.get("collected_count") or ""),
            "comment": parse_count(r.get("comment_count")),
            "share": parse_count(r.get("share_count")),
            "type": r.get("type"),
            "nickname": r.get("nickname"),
            "url": r.get("note_url") or "",
            "keyword": r.get("source_keyword"),
        })

    all2026 = [i for i in items if i["year"] == YEAR]
    all2026.sort(key=lambda x: (-x["liked"], -x["collected"]))

    out = {"all_2026": all2026, "total_dedup": len(items),
           "years": {}}
    for i in items:
        out["years"][i["year"]] = out["years"].get(i["year"], 0) + 1

    (OUT_DIR / "doumi_2026.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"相关去重笔记 {len(items)} 篇；2026 年 {len(all2026)} 篇")
    print("年份分布:", dict(sorted(out["years"].items())))
    print("-" * 70)
    for n, i in enumerate(all2026, 1):
        print(f"{n:>2}. {i['date']} 赞{i['liked_raw']:>6} 藏{i['collected_raw']:>6} "
              f"评{i['comment']:>5} | {(i['title'] or i['desc'][:24])[:38]}")


if __name__ == "__main__":
    main()
