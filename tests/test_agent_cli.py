# -*- coding: utf-8 -*-
"""Agent CLI 的回归测试。

用假 runner / 假 Popen 覆盖作业生命周期，不启动浏览器、不访问平台。
"""

import json
import time
from argparse import Namespace
from pathlib import Path

import pytest

from tools import agent_cli


def _ns(**overrides) -> Namespace:
    values = dict(
        platform="xhs",
        crawler_type="search",
        keywords="咖啡",
        note_urls="",
        creator_ids="",
        max_notes=20,
        comments="no",
        sub_comments="no",
        media="no",
        login="cookie",
        cookie_file="",
        save_option="jsonl",
        save_path="",
        concurrency=0,
        headless="",
        allow_concurrent=False,
    )
    values.update(overrides)
    return Namespace(**values)


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    target = tmp_path / "jobs"
    target.mkdir()
    monkeypatch.setattr(agent_cli, "JOBS_DIR", target)
    return target


def _write_job(job_id: str, **overrides) -> dict:
    job = {
        "id": job_id,
        "status": "running",
        "pid": 4242,
        "created_at": time.time(),
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "platform": "xhs",
        "crawler_type": "search",
        "keywords": "咖啡",
        "save_option": "jsonl",
        "save_path": "",
        "argv": ["--platform", "xhs"],
        "log_path": str(agent_cli.JOBS_DIR / f"{job_id}.log"),
        "status_file": str(agent_cli.JOBS_DIR / f"{job_id}.done.json"),
    }
    job.update(overrides)
    agent_cli._save_job(job)
    return job


# --------------------------------------------------------------------- 参数
def test_build_argv_search_defaults():
    argv = agent_cli._build_argv(_ns())

    assert argv[:6] == ["--platform", "xhs", "--type", "search", "--keywords", "咖啡"]
    assert "--crawler_max_notes_count" in argv
    assert argv[argv.index("--get_comment") + 1] == "no"
    assert argv[argv.index("--save_data_option") + 1] == "jsonl"


def test_build_argv_detail_requires_note_urls():
    with pytest.raises(agent_cli.CliError) as exc_info:
        agent_cli._build_argv(_ns(crawler_type="detail", keywords=""))

    assert exc_info.value.code == agent_cli.EXIT_USAGE


def test_build_argv_creator_uses_creator_ids():
    argv = agent_cli._build_argv(
        _ns(crawler_type="creator", keywords="", creator_ids="https://example.com/u/1")
    )
    assert argv[argv.index("--creator_id") + 1] == "https://example.com/u/1"


# --------------------------------------------------------------------- 启动
def test_start_records_job_and_keeps_cookie_out_of_argv(tmp_path, jobs_dir, monkeypatch):
    cookie_file = tmp_path / "xhs.cookie"
    cookie_file.write_text("a1=SECRET_VALUE; web_session=TOKEN\n", encoding="utf-8")

    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return FakeProc()

    monkeypatch.setattr(agent_cli.subprocess, "Popen", fake_popen)

    payload = agent_cli.cmd_start(_ns(cookie_file=str(cookie_file)))

    assert payload["ok"] and payload["job"]["pid"] == 4242
    assert "--cookie-file" not in captured["command"]
    assert captured["env"]["MC_COOKIES"].startswith("a1=SECRET_VALUE")
    assert "SECRET_VALUE" not in json.dumps(payload)

    job = json.loads((jobs_dir / f"{payload['job']['id']}.json").read_text(encoding="utf-8"))
    assert job["cookie_source"] == f"file:{cookie_file}"
    assert "SECRET_VALUE" not in json.dumps(job)

    spec = json.loads(
        (jobs_dir / f"{payload['job']['id']}.spec.json").read_text(encoding="utf-8")
    )
    assert "SECRET_VALUE" not in json.dumps(spec)


def test_start_rejects_concurrent_job_on_same_platform(jobs_dir, monkeypatch):
    _write_job("xhs-search-running", status="running")
    monkeypatch.setattr(agent_cli, "_pid_alive", lambda pid: True)

    with pytest.raises(agent_cli.CliError) as exc_info:
        agent_cli.cmd_start(_ns())

    assert exc_info.value.code == agent_cli.EXIT_BUSY


# --------------------------------------------------------------------- 状态机
def test_refresh_reads_done_marker(jobs_dir):
    job = _write_job("xhs-search-done")
    agent_cli._done_path("xhs-search-done").write_text(
        json.dumps({"exit_code": 0, "finished_at": time.time(), "error": None}),
        encoding="utf-8",
    )

    refreshed = agent_cli._refresh(job)

    assert refreshed["status"] == "finished"
    assert refreshed["exit_code"] == 0
    assert refreshed["finished_at"]


def test_refresh_marks_lost_when_process_disappears(jobs_dir, monkeypatch):
    job = _write_job("xhs-search-lost")
    monkeypatch.setattr(agent_cli, "_pid_alive", lambda pid: False)

    refreshed = agent_cli._refresh(job)

    assert refreshed["status"] == "lost"


def test_stop_on_finished_job_is_noop(jobs_dir):
    _write_job("xhs-search-finished", status="finished")

    payload = agent_cli.cmd_stop(Namespace(job_id="xhs-search-finished", timeout=1.0, force=False))

    assert payload == {"ok": True, "job_id": "xhs-search-finished", "status": "finished", "stopped": False}


# --------------------------------------------------------------------- 结果
def _seed_results(tmp_path: Path, job_id: str) -> dict:
    out = tmp_path / "out" / "xhs" / "jsonl"
    out.mkdir(parents=True)
    target = out / "search_contents_2026-10-19.jsonl"
    target.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in ({"note_id": "n1", "title": "一"}, {"note_id": "n2", "title": "二"})
        )
        + "\n",
        encoding="utf-8",
    )
    return _write_job(job_id, save_path=str(tmp_path / "out"), started_at=time.time() - 10)


def test_results_counts_records(tmp_path, jobs_dir):
    _seed_results(tmp_path, "xhs-search-results")

    payload = agent_cli.cmd_results(Namespace(job_id="xhs-search-results"))

    assert payload["totals"]["files"] == 1
    assert payload["totals"]["records"] == 2
    assert payload["files"][0]["item"] == "contents"


def test_peek_returns_limited_items(tmp_path, jobs_dir):
    _seed_results(tmp_path, "xhs-search-peek")

    payload = agent_cli.cmd_peek(
        Namespace(job_id="xhs-search-peek", kind="contents", limit=1)
    )

    assert payload["total"] == 2
    assert payload["returned"] == 1
    assert payload["items"][0]["note_id"] == "n1"


def test_peek_missing_kind_raises(tmp_path, jobs_dir):
    _seed_results(tmp_path, "xhs-search-peek-missing")

    with pytest.raises(agent_cli.CliError) as exc_info:
        agent_cli.cmd_peek(Namespace(job_id="xhs-search-peek-missing", kind="comments", limit=5))

    assert exc_info.value.code == agent_cli.EXIT_NOT_FOUND
