# -*- coding: utf-8 -*-
"""标注工作台 API 的回归测试（离线，不联网、不碰真实 data/）。

覆盖两个容易丢数据的点：
- verdicts.jsonl 单行损坏时不能整体读空（否则下一次保存会覆盖历史标注）；
- 保存改为 append-only 后，历史标注与同一 note 的更新都要能正确读回。
另外覆盖 platform 白名单、分页、only_unlabeled 与非法 note_id。
"""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import api.routers.label as label


@pytest.fixture()
def verdict_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(label, "LABEL_DIR", tmp_path)
    monkeypatch.setattr(label, "VERDICT_FILE", tmp_path / "verdicts.jsonl")
    return tmp_path / "verdicts.jsonl"


def _write_verdict(path: Path, note_id: str, level: str = "normal") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"note_id": note_id, "level": level, "signals": {}, "memo": ""},
                ensure_ascii=False,
            )
            + "\n"
        )


def test_load_verdicts_skips_corrupt_lines(verdict_file: Path) -> None:
    verdict_file.write_text(
        json.dumps({"note_id": "ok1", "level": "strong", "signals": {}}) + "\n"
        + "{ this is not json\n"
        + json.dumps({"note_id": "ok2", "level": "normal", "signals": {}}) + "\n",
        encoding="utf-8",
    )

    verdicts = label._load_verdicts()

    assert set(verdicts) == {"ok1", "ok2"}


@pytest.mark.asyncio
async def test_save_verdict_append_only_preserves_history(verdict_file: Path) -> None:
    _write_verdict(verdict_file, "old_note", level="strong")

    await label.save_verdict(
        {"note_id": "new_note", "level": "suspect", "signals": {"staging": 2}, "memo": "m"}
    )
    await label.save_verdict(
        {"note_id": "new_note", "level": "normal", "signals": {"staging": 0}, "memo": "updated"}
    )

    verdicts = label._load_verdicts()
    assert "old_note" in verdicts  # 历史标注不能被覆盖丢失
    assert verdicts["new_note"]["level"] == "normal"  # 同一 note 以最后一条为准
    assert verdicts["new_note"]["memo"] == "updated"


@pytest.mark.asyncio
async def test_save_verdict_rejects_invalid_level(verdict_file: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await label.save_verdict({"note_id": "n1", "level": "bogus", "signals": {}})

    assert exc_info.value.status_code == 400


def _fake_contents() -> list[dict]:
    return [
        {"note_id": "n1", "title": "a", "time": 1000, "creator_hash": "u1"},
        {"note_id": "n2", "title": "b", "time": 3000, "creator_hash": "u1"},
        {"note_id": "n3", "title": "c", "time": 2000, "creator_hash": "u2"},
    ]


@pytest.mark.asyncio
async def test_posts_pagination_and_only_unlabeled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label, "_load_contents", lambda platform: _fake_contents())
    monkeypatch.setattr(label, "_load_comments", lambda platform: {})
    monkeypatch.setattr(label, "_load_verdicts", lambda: {})

    first = await label.list_posts(platform="xhs", limit=2, offset=0, only_unlabeled=False)
    second = await label.list_posts(platform="xhs", limit=2, offset=2, only_unlabeled=False)
    assert first["total"] == 3
    assert [post["note_id"] for post in first["posts"]] == ["n2", "n3"]  # 按发布时间倒序
    assert [post["note_id"] for post in second["posts"]] == ["n1"]

    monkeypatch.setattr(
        label, "_load_verdicts", lambda: {"n2": {"note_id": "n2", "level": "normal"}}
    )
    filtered = await label.list_posts(platform="xhs", limit=10, offset=0, only_unlabeled=True)
    assert filtered["total"] == 2
    assert "n2" not in [post["note_id"] for post in filtered["posts"]]


@pytest.mark.asyncio
async def test_invalid_platform_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("../../tmp/x", "xhs/../x", "a b", ""):
        with pytest.raises(HTTPException) as exc_info:
            await label.list_posts(platform=bad, limit=1, offset=0, only_unlabeled=False)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_graph_unknown_note_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label, "_load_contents", lambda platform: _fake_contents())

    with pytest.raises(HTTPException) as exc_info:
        await label.get_graph(
            note_id="does-not-exist",
            platform="xhs",
            max_commenters=12,
            depth=2,
            max_peer_comments=5,
            max_peer_notes=24,
        )

    assert exc_info.value.status_code == 404
