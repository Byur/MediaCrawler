#!/usr/bin/env python3
"""把 doumi_2026.json 生成 Markdown 报告 + CSV。"""

import csv
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"
DATA = json.loads((OUT / "doumi_2026.json").read_text(encoding="utf-8"))
ITEMS = DATA["all_2026"]
YEARS = {int(k): v for k, v in DATA["years"].items()}

FANS_PATH = OUT / "author_fans.json"
FANS = {}
if FANS_PATH.exists():
    FANS = json.loads(FANS_PATH.read_text(encoding="utf-8")).get("items", {})


def tier_of(fans):
    """粉丝量级分档。"""
    if not fans:
        return "—"
    if fans >= 500000:
        return "头部"
    if fans >= 100000:
        return "达人"
    if fans >= 10000:
        return "中腰部"
    return "素人"


def humanize_fans(v):
    """把接口返回的纯数字粉丝数转成「12.6万」这种可读形式。"""
    if v in (None, "", "—"):
        return "—"
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        if n >= 100000000:
            return f"{n / 100000000:.1f}亿"
        if n >= 10000:
            return f"{n / 10000:.1f}万"
        return f"{n}"
    return s


def fmt_fans(raw, fans):
    if not raw:
        return "—"
    return f"{raw}（{tier_of(fans)}）"

# 细分类 -> 大类
BUCKET = {
    "探店": "探店实测", "探店（菜品横向点评）": "探店实测", "探店（土风豆米火锅）": "探店实测",
    "探店（性价比实测）": "探店实测", "探店（事件向）": "探店实测", "探店（新店）": "探店实测",
    "探店体验+疑问": "探店实测",
    "探店合集": "探店合集/攻略", "探店合集+避坑": "探店合集/攻略", "探店合集（多店实测）": "探店合集/攻略",
    "美食介绍（做饭/复刻）": "美食介绍/教程", "美食介绍（科普向）": "美食介绍/教程",
    "美食介绍（乡村/家做）": "美食介绍/教程", "美食介绍（家做软哨火锅）": "美食介绍/教程",
    "美食教程（保姆级）": "美食介绍/教程", "美食分享（短视频）": "美食介绍/教程",
    "美食分享（家做短视频）": "美食介绍/教程", "美食安利（游记吐槽）": "美食介绍/教程",
    "居家好物/底料测评": "好物/底料", "居家好物/食材清单": "好物/底料",
    "求助（求推荐）": "求助/避雷/讨论", "避雷/维权": "求助/避雷/讨论",
    "避雷（食安/服务）": "求助/避雷/讨论", "品牌对比/求推荐": "求助/避雷/讨论",
}

# 人工分类：(点赞原文, 标题前缀) -> (类型, 地点/场景)
CLASS = [
    ("3.8万", "面对豆米火锅", "美食分享（短视频）", "未标注地点"),
    ("1.3万", "来，来，来，吃贵州豆米火锅", "美食介绍（做饭/复刻）", "贵州（家做）"),
    ("8536", "一天吃遍贵阳", "探店合集", "贵阳"),
    ("6534", "盒马豆米火锅底料", "居家好物/底料测评", "盒马"),
    ("6174", "去贵州旅游就是个错误", "美食安利（游记吐槽）", "贵州"),
    ("4601", "这个酸汤豆米火锅到底啥味儿", "美食介绍（科普向）", "贵州"),
    ("4402", "至今不知道这个豆米火锅到底多美味", "美食分享（短视频）", "贵州"),
    ("4140", "和朋友们一致认为贵阳好吃的店", "探店合集", "贵阳"),
    ("2883", "盒马贵州豆米火锅食材推荐", "居家好物/食材清单", "盒马"),
    ("2686", "豆米火锅吃了，一个疑问", "探店体验+疑问", "贵阳"),
    ("2560", "可惜你不是贵州人", "美食介绍（乡村/家做）", "贵州"),
    ("2348", "小象 你到底在豆米火锅底料里加了什么", "居家好物/底料测评", "小象超市"),
    ("2162", "开启周五的快乐放纵餐", "美食介绍（家做软哨火锅）", "家中自制"),
    ("2116", "超下饭的豆米火锅", "美食教程（保姆级）", "家中自制"),
    ("2039", "北京！！！吃火锅偶遇仁科", "探店", "北京·大望路"),
    ("1683", "来了贵阳才知道之前看的攻略有多吓人", "探店合集+避坑", "贵阳"),
    ("1416", "二刷爽爽贵阳", "探店合集（多店实测）", "贵阳"),
    ("1109", "@所有人都来给我吃上海这家豆米火锅", "探店", "上海"),
    ("1081", "好吃的贵阳豆米火锅", "探店", "贵阳·花果园"),
    ("1074", "陈氏豆米火锅各店吃9次菜品点评", "探店（菜品横向点评）", "北京·五道口等（陈氏多店）"),
    ("1051", "感觉是贵阳top1好吃的", "探店", "贵阳·甲秀楼/云岩"),
    ("1042", "请赐予我无敌香香豆米火锅店", "探店（土风豆米火锅）", "贵阳·黔灵东路"),
    ("963", "贵阳豆米火锅唐记、土风、新大新、陈氏", "品牌对比/求推荐", "贵阳"),
    ("954", "福建泉州", "探店", "福建泉州"),
    ("930", "🫘豆米火锅🍲", "美食分享（家做短视频）", "家中自制"),
    ("883", "贵阳美食求助", "求助（求推荐）", "贵阳"),
    ("827", "（无广）荔波让谁都想二刷的小吃", "探店合集", "贵州荔波"),
    ("800", "贵阳哪家豆米火锅好吃", "求助（求推荐）", "贵阳"),
    ("678", "儿豁嘛，福师大西门", "探店", "福州·福师大西门"),
    ("637", "来贵阳请避雷青云市集", "避雷/维权", "贵阳·青云市集"),
    ("564", "结账一人25", "探店（性价比实测）", "贵阳"),
    ("442", "上海我不愿公开的贵州爆炒和豆米火锅", "探店", "上海"),
    ("426", "福州豆米火锅", "探店", "福州"),
    ("421", "全重庆第一好吃的豆米软糖软哨泡饭", "探店", "重庆·龙头寺"),
    ("359", "天菩萨！！在贵阳吃豆米火锅遇到阿联酋亲王", "探店（事件向）", "贵阳·黔小豆"),
    ("336", "集美新开", "探店（新店）", "厦门·集美"),
    ("319", "来番禺南村吃了个豆米火锅", "探店", "广州·番禺南村"),
    ("293", "吃出虫子还这个态度", "避雷（食安/服务）", "厦门·集美"),
    ("277", "闽师大黔来顺", "探店", "漳州·闽师大"),
    ("270", "地道贵州豆米火锅，在广州", "探店", "广州"),
]

TAG_RE = re.compile(r"#[^#\[\]]{1,30}\[话题\]#")


def match_class(item):
    for liked, title_prefix, kind, where in CLASS:
        if item["liked_raw"] == liked and item["title"].startswith(title_prefix):
            return kind, where
    return "未分类", "-"


def clean_desc(desc):
    """把正文和末尾话题标签分开，去掉话题标记。"""
    tags = TAG_RE.findall(desc)
    body = TAG_RE.sub("", desc).strip()
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body, [t.strip("#").replace("[话题]", "") for t in tags]


def fmt(n):
    return f"{n:,}"


def main():
    rows = []
    for i, it in enumerate(ITEMS, 1):
        kind, where = match_class(it)
        body, tags = clean_desc(it["desc"])
        f = FANS.get(it["note_id"]) or {}
        it.update(rank=i, kind=kind, where=where, body=body, tags=tags,
                  bucket=BUCKET.get(kind, "其他"),
                  fans_raw=humanize_fans(f.get("fans_raw")), fans=f.get("fans"),
                  fans_exact=f.get("fans") if f.get("fans") is not None else "",
                  tier=tier_of(f.get("fans")))
        rows.append(it)

    # ---------- CSV ----------
    with (OUT / "doumi_2026_posts.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["排名", "发布日期", "标题", "正文", "点赞", "收藏", "评论", "分享",
                    "作者粉丝数", "粉丝数(原始)", "粉丝量级", "类型", "地点/场景", "作者", "笔记链接"])
        for r in rows:
            w.writerow([r["rank"], r["date"], r["title"], r["body"].replace("\n", " / "),
                        r["liked"], r["collected"], r["comment"], r["share"],
                        r["fans_raw"], r["fans_exact"], r["tier"],
                        r["kind"], r["where"], r["nickname"], r["url"]])

    # ---------- Markdown ----------
    L = []
    A = L.append
    A("# 小红书「豆米火锅」2026 年探店/美食笔记整理")
    A("")
    A(f"- **采集时间**：2026-10-24（Asia/Shanghai）")
    A(f"- **平台**：小红书（MediaCrawler 公开搜索采集）")
    A(f"- **关键词**：豆米火锅 / 贵州豆米火锅 / 贵阳豆米火锅 / 豆米火锅 探店 / 豆米火锅 好吃")
    A(f"- **样本**：相关笔记去重 {DATA['total_dedup']} 篇，其中 **2026 年 {len(rows)} 篇**（2025 年 "
      f"{YEARS.get(2025,0)} 篇、2024 年 {YEARS.get(2024,0)} 篇已剔除）")
    A(f"- **排序**：按点赞数倒序，点赞相同再看收藏数")
    A(f"- **说明**：`正文` 为笔记文案原文；视频类笔记文案普遍较短，信息主要在画面里，已在「类型」列标注")
    A("")

    from collections import Counter
    buckets = Counter(r["bucket"] for r in rows)
    places = Counter(r["where"].split("·")[0] for r in rows)
    A("**类别分布**：" + "、".join(f"{k} {v} 篇" for k, v in buckets.most_common()))
    A("")
    A("**地域分布**：" + "、".join(f"{k} {v}" for k, v in places.most_common(8)))
    A("")
    tiers = Counter(r["tier"] for r in rows)
    A("**作者粉丝量级分布**：" + "、".join(f"{k} {v} 篇" for k, v in tiers.most_common()))
    A("")
    A("**点赞 / 收藏双高 TOP5**（点赞与收藏均进入前 10）")
    A("")
    top_like = {r["rank"] for r in rows[:10]}
    top_fav = {r["rank"] for r in sorted(rows, key=lambda x: -x["collected"])[:10]}
    dual = sorted(top_like & top_fav)
    for rank in dual:
        r = rows[rank - 1]
        A(f"- **{r['rank']}. {r['title'] or r['body'][:20]}**（{r['date']}，赞 {r['liked_raw']} / "
          f"藏 {r['collected_raw']}，{r['where']}）")
    A("")

    # 概览表
    A("## 一、总览表（按点赞倒序）")
    A("")
    A("| # | 日期 | 标题 | 赞 | 藏 | 评 | 作者粉丝 | 类别 | 地点 |")
    A("|---:|---|---|---:|---:|---:|---:|---|---|")
    for r in rows:
        title = (r["title"] or f"（无标题）{r['body'][:18]}").replace("|", "｜")
        if len(title) > 34:
            title = title[:33] + "…"
        fans_cell = f"{r['fans_raw']}<br>{r['tier']}" if r["fans_raw"] != "—" else "—"
        A(f"| {r['rank']} | {r['date']} | {title} | {r['liked_raw']} | {r['collected_raw']} "
          f"| {r['comment']} | {fans_cell} | {r['bucket']} | {r['where']} |")
    A("")

    # 内容明细
    A("## 二、逐条内容明细")
    A("")
    for r in rows:
        title = r["title"] or "（无标题）"
        A(f"### {r['rank']}. {title}")
        A("")
        A(f"**{r['date']}｜赞 {r['liked_raw']} · 藏 {r['collected_raw']} · 评 {r['comment']} · "
          f"分享 {r['share']}｜作者粉丝 {r['fans_raw']}（{r['tier']}）｜{r['kind']}｜"
          f"{r['where']}｜@{r['nickname']}**")
        A("")
        body = r["body"] or "（视频/图片笔记，正文为空——内容在画面里）"
        for line in body.split("\n"):
            A(f"> {line}" if line.strip() else ">")
        if r["tags"]:
            A("")
            A("标签：" + " ".join(f"`{t}`" for t in r["tags"][:10]))
        A("")
        A(f"链接：{r['url']}")
        A("")

    # ---- 量级 × 互动表现 ----
    A("## 三、账号量级 × 互动表现")
    A("")
    A("| 量级 | 篇数 | 中位点赞 | 中位收藏 | 点赞中位/粉丝中位 | 代表笔记 |")
    A("|---|---:|---:|---:|---:|---|")
    order = ["头部", "达人", "中腰部", "素人", "—"]
    for t in order:
        grp = [r for r in rows if r["tier"] == t]
        if not grp:
            continue
        likes = sorted(r["liked"] for r in grp)
        favs = sorted(r["collected"] for r in grp)
        fans = sorted(r["fans"] for r in grp if r["fans"])
        med_like = likes[len(likes) // 2]
        med_fav = favs[len(favs) // 2]
        if fans:
            med_fan = fans[len(fans) // 2]
            ratio = f"{med_like / med_fan:.2f}x"
        else:
            ratio = "—"
        best = max(grp, key=lambda x: x["liked"])
        A(f"| {t} | {len(grp)} | {med_like:,} | {med_fav:,} | {ratio} | "
          f"{best['rank']}. {(best['title'] or best['body'][:16])[:26]} |")
    A("")
    A("> 读法：比值 >1 说明这条笔记的点赞数超过了作者粉丝体量，属于「出圈」；"
      "比值 <0.1 说明主要是老粉在看，流量来自关注页而非推荐页。")
    A("")

    (OUT / "豆米火锅_2026_小红书笔记.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- 控制台摘要 ----------
    print(f"写入 {len(rows)} 条")
    from collections import Counter
    print("类别分布:", Counter(r["bucket"] for r in rows).most_common())
    print("地点分布:", Counter(r["where"] for r in rows).most_common(8))


if __name__ == "__main__":
    main()
