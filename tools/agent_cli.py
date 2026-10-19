# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/agent_cli.py
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

"""给 agent 用的 MediaCrawler CLI（机器优先，JSON 输出）。

设计要点：

- **后台作业**：``start`` 秒回 job_id，爬取在独立进程组里跑；
  调用方用 ``status`` / ``logs`` 轮询，``stop`` 终止。
- **只为 agent 暴露稳定动词**：start / status / logs / stop / list /
  results / peek / doctor。参数与 ``main.py`` 一一对应，不复制业务逻辑。
- **默认值收敛到"小样本"**：20 篇、不抓评论、不下载媒体、并发 1，
  避免一次误调用打出大流量。
- **Cookie 不上命令行**：``--cookie-file`` 或 ``MEDIACRAWLER_COOKIES``
  经环境变量传给子进程，不出现在 argv / ps / shell history 里。
- **所有命令都输出 JSON**，失败时也输出 ``{"ok": false, "error": ...}``，
  并用不同退出码区分错误类型。

用法见 ``python -m tools.agent_cli --help``，
agent 侧的完整操作手册见全局 skill ``~/.agents/skills/mediacrawler/SKILL.md``。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "data" / "agent" / "jobs"
RUNNER = ROOT / "tools" / "agent_runner.py"

PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")
CRAWLER_TYPES = ("search", "detail", "creator")
LOGIN_TYPES = ("cookie", "qrcode", "phone")
SAVE_OPTIONS = ("jsonl", "json", "csv", "excel", "sqlite", "db", "mongodb", "postgres")
CRITICAL_MODULES = ("playwright", "httpx", "xhshow", "aiofiles", "typer", "pandas")

# 退出码：agent 可以按码分支，不必解析文案
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_DOCTOR = 4
EXIT_START_FAILED = 5
EXIT_BUSY = 6


class CliError(RuntimeError):
    def __init__(self, code: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --------------------------------------------------------------------- 通用
def _now() -> float:
    return time.time()


def _iso(timestamp: Optional[float]) -> Optional[str]:
    if not timestamp:
        return None
    # 用本机时区并带偏移，避免 agent 在 UTC / 本地时间之间来回换算
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _done_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.done.json"


def _spec_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.spec.json"


def _log_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.log"


def _save_job(job: Dict[str, Any]) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(job["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_job(job_id: str) -> Dict[str, Any]:
    path = _job_path(job_id)
    if not path.exists():
        raise CliError(EXIT_NOT_FOUND, f"job not found: {job_id}", job_id=job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _all_jobs() -> List[Dict[str, Any]]:
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for path in JOBS_DIR.glob("*.json"):
        if path.name.endswith(".done.json") or path.name.endswith(".spec.json"):
            continue
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    jobs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return jobs


def _latest_job() -> Dict[str, Any]:
    jobs = _all_jobs()
    if not jobs:
        raise CliError(EXIT_NOT_FOUND, "no jobs yet, run 'start' first")
    return jobs[0]


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _refresh(job: Dict[str, Any]) -> Dict[str, Any]:
    """把作业状态与子进程实际状态对齐。"""
    if job.get("status") != "running":
        return job

    done = _done_path(job["id"])
    if done.exists():
        try:
            payload = json.loads(done.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        exit_code = payload.get("exit_code")
        job["exit_code"] = exit_code
        job["finished_at"] = payload.get("finished_at") or _now()
        job["status"] = "finished" if exit_code == 0 else "failed"
        if payload.get("error"):
            job["error"] = payload["error"]
        _save_job(job)
        return job

    if not _pid_alive(job.get("pid")):
        log = _log_path(job["id"])
        job["status"] = "lost"
        job["finished_at"] = log.stat().st_mtime if log.exists() else _now()
        _save_job(job)
    return job


def _elapsed(job: Dict[str, Any]) -> Optional[float]:
    started = job.get("started_at")
    if not started:
        return None
    end = job.get("finished_at") or _now()
    return round(max(0.0, end - started), 1)


def _tail_lines(path: Path, count: int) -> List[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=count)]


def _count_records(path: Path) -> Optional[int]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".jsonl":
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return sum(1 for line in handle if line.strip())
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 1
        if suffix == ".csv":
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return max(0, sum(1 for _ in handle) - 1)
    except (OSError, ValueError):
        return None
    return None


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _save_root(job: Dict[str, Any]) -> Path:
    configured = job.get("save_path")
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else (ROOT / path)
    return ROOT / "data"


def _find_results(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = _save_root(job) / job["platform"]
    if not base.exists():
        return []
    since = (job.get("started_at") or 0) - 5
    rows: List[Dict[str, Any]] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        if stat.st_mtime < since:
            continue
        name = path.name
        item = "contents" if "contents" in name else ("comments" if "comments" in name else path.suffix.lstrip("."))
        rows.append(
            {
                "path": _relative(path),
                "item": item,
                "kind": path.suffix.lstrip("."),
                "bytes": stat.st_size,
                "records": _count_records(path),
                "modified_at": _iso(stat.st_mtime),
            }
        )
    rows.sort(key=lambda item: item["modified_at"] or "", reverse=True)
    return rows


def _read_records(path: Path, limit: int) -> List[Any]:
    if path.suffix.lower() == ".jsonl":
        items: List[Any] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(items) >= limit:
                    break
        return items
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data[:limit] if isinstance(data, list) else [data]
    raise CliError(EXIT_USAGE, f"cannot peek {path.suffix} files, use jsonl/json")


# --------------------------------------------------------------------- 命令
def cmd_doctor(_: argparse.Namespace) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None, critical: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "critical": critical, "detail": detail})

    check("python", sys.version_info >= (3, 11), sys.version.split()[0])
    check("repo", (ROOT / "main.py").exists() and (ROOT / "config").is_dir(), str(ROOT))

    modules = {name: importlib.util.find_spec(name) is not None for name in CRITICAL_MODULES}
    check("dependencies", all(modules.values()), modules)

    candidates = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        str(Path.home() / "Library" / "Caches" / "ms-playwright"),
        str(Path.home() / ".cache" / "ms-playwright"),
    ]
    browsers = next((c for c in candidates if c and Path(c).exists()), None)
    check("playwright_browsers", browsers is not None, browsers or "not found (run: playwright install chromium)")

    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        probe = JOBS_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError as exc:
        writable = False
        probe_error = str(exc)
    check("data_writable", writable, None if writable else probe_error)

    profiles = ROOT / "browser_data"
    saved = sorted(p.name for p in profiles.glob("*") if p.is_dir()) if profiles.exists() else []
    check("saved_login_profiles", bool(saved), saved or "none (first run needs a human login)", critical=False)

    config_info: Dict[str, Any] = {}
    config_error: Optional[str] = None
    try:
        import config

        config_info = {
            "platform": config.PLATFORM,
            "login_type": config.LOGIN_TYPE,
            "save_option": config.SAVE_DATA_OPTION,
            "cdp_mode": config.ENABLE_CDP_MODE,
            "headless": config.HEADLESS,
            "cookies_configured": bool(config.COOKIES),
        }
    except Exception as exc:  # noqa: BLE001 - doctor 不应该因配置错误崩溃
        config_error = str(exc)
    check("config_import", bool(config_info), config_info or config_error)

    critical_ok = all(item["ok"] for item in checks if item["critical"])
    return {"ok": critical_ok, "checks": checks, "config": config_info}


def _build_argv(args: argparse.Namespace) -> List[str]:
    argv: List[str] = ["--platform", args.platform, "--type", args.crawler_type]

    if args.crawler_type == "search":
        if not args.keywords:
            raise CliError(EXIT_USAGE, "search 模式需要 --keywords")
        argv += ["--keywords", args.keywords]
    elif args.crawler_type == "detail":
        if not args.note_urls:
            raise CliError(EXIT_USAGE, "detail 模式需要 --note-urls（逗号分隔的笔记 URL/ID）")
        argv += ["--specified_id", args.note_urls]
    else:
        if not args.creator_ids:
            raise CliError(EXIT_USAGE, "creator 模式需要 --creator-ids（逗号分隔的创作者 URL/ID）")
        argv += ["--creator_id", args.creator_ids]

    argv += [
        "--lt", args.login,
        "--crawler_max_notes_count", str(args.max_notes),
        "--get_comment", args.comments,
        "--get_sub_comment", args.sub_comments,
        "--get_media", args.media,
        "--save_data_option", args.save_option,
    ]
    if args.concurrency:
        argv += ["--max_concurrency_num", str(args.concurrency)]
    if args.save_path:
        argv += ["--save_data_path", args.save_path]
    if args.headless:
        argv += ["--headless", args.headless]
    return argv


def _read_cookie_file(path: str) -> str:
    text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):].strip()
    if not text:
        raise CliError(EXIT_USAGE, f"cookie file is empty: {path}")
    return text


def cmd_start(args: argparse.Namespace) -> Dict[str, Any]:
    argv = _build_argv(args)
    platform = args.platform

    for other in _all_jobs():
        if _refresh(other).get("status") == "running" and other.get("platform") == platform:
            if not args.allow_concurrent:
                raise CliError(
                    EXIT_BUSY,
                    f"{platform} 已有作业在跑：{other['id']}；先 stop 它，或加 --allow-concurrent",
                    running_job=other["id"],
                )

    job_id = (
        f"{platform}-{args.crawler_type}-"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    )
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    cookies = os.environ.get("MEDIACRAWLER_COOKIES", "").strip()
    cookie_source = "env:MEDIACRAWLER_COOKIES" if cookies else "none"
    if args.cookie_file:
        cookies = _read_cookie_file(args.cookie_file)
        cookie_source = f"file:{args.cookie_file}"

    spec = {"argv": argv, "created_at": _now()}
    _spec_path(job_id).write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["MC_JOB_SPEC"] = str(_spec_path(job_id))
    env["MC_JOB_STATUS_FILE"] = str(_done_path(job_id))
    if cookies:
        env["MC_COOKIES"] = cookies

    log_path = _log_path(job_id)
    log_handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, str(RUNNER)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise CliError(EXIT_START_FAILED, f"启动子进程失败: {exc}") from exc
    finally:
        log_handle.close()

    job = {
        "id": job_id,
        "status": "running",
        "pid": process.pid,
        "created_at": _now(),
        "started_at": _now(),
        "finished_at": None,
        "exit_code": None,
        "platform": platform,
        "crawler_type": args.crawler_type,
        "login_type": args.login,
        "keywords": args.keywords or args.note_urls or args.creator_ids,
        "save_option": args.save_option,
        "save_path": args.save_path or "",
        "cookie_source": cookie_source,
        "argv": argv,
        "log_path": str(log_path),
        "status_file": str(_done_path(job_id)),
    }
    _save_job(job)

    warnings: List[str] = []
    if args.login == "cookie" and not cookies:
        warnings.append(
            "未提供 cookie（--cookie-file / MEDIACRAWLER_COOKIES），将依赖已保存的登录态；"
            "若日志出现 'Login state result: False'，请让用户重新登录"
        )
    if args.allow_concurrent:
        warnings.append("已允许同平台并发：多个作业会争用同一个浏览器 profile，通常不建议")

    return {
        "ok": True,
        "warnings": warnings,
        "job": {
            **job,
            "log_path": _relative(log_path),
            "status_file": _relative(_done_path(job_id)),
        },
        "next": f"python -m tools.agent_cli status {job_id}",
    }


def cmd_status(args: argparse.Namespace) -> Dict[str, Any]:
    job = _refresh(_load_job(args.job_id) if args.job_id else _latest_job())
    tail = _tail_lines(Path(job["log_path"]), 5)
    return {
        "ok": True,
        "job": {
            "id": job["id"],
            "status": job["status"],
            "pid": job.get("pid"),
            "platform": job["platform"],
            "crawler_type": job["crawler_type"],
            "keywords": job.get("keywords"),
            "started_at": _iso(job.get("started_at")),
            "finished_at": _iso(job.get("finished_at")),
            "elapsed_sec": _elapsed(job),
            "exit_code": job.get("exit_code"),
            "error": job.get("error"),
            "log_path": _relative(Path(job["log_path"])),
        },
        "last_log": tail,
    }


def cmd_logs(args: argparse.Namespace) -> Dict[str, Any]:
    job = _refresh(_load_job(args.job_id) if args.job_id else _latest_job())
    lines = _tail_lines(Path(job["log_path"]), max(1, args.lines))
    matched = lines
    if args.grep:
        try:
            pattern = re.compile(args.grep)
            matched = [line for line in lines if pattern.search(line)]
        except re.error:
            matched = [line for line in lines if args.grep in line]
    return {
        "ok": True,
        "job_id": job["id"],
        "status": job["status"],
        "log_path": _relative(Path(job["log_path"])),
        "lines": matched,
        "returned": len(matched),
    }


def cmd_stop(args: argparse.Namespace) -> Dict[str, Any]:
    job = _refresh(_load_job(args.job_id) if args.job_id else _latest_job())
    if job.get("status") != "running":
        return {"ok": True, "job_id": job["id"], "status": job["status"], "stopped": False}

    pid = job.get("pid")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = _now() + max(1.0, args.timeout)
    while _now() < deadline and _pid_alive(pid):
        time.sleep(0.2)

    forced = False
    if _pid_alive(pid):
        if args.force:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            forced = True
        else:
            return {
                "ok": False,
                "job_id": job["id"],
                "status": "still_running",
                "hint": "进程未在超时内退出；加 --force 强杀",
            }

    job["status"] = "stopped"
    job["finished_at"] = _now()
    job["exit_code"] = None
    _save_job(job)
    return {"ok": True, "job_id": job["id"], "status": "stopped", "forced": forced}


def cmd_list(args: argparse.Namespace) -> Dict[str, Any]:
    jobs = [_refresh(job) for job in _all_jobs()][: max(1, args.limit)]
    return {
        "ok": True,
        "jobs": [
            {
                "id": job["id"],
                "status": job["status"],
                "platform": job["platform"],
                "crawler_type": job["crawler_type"],
                "keywords": job.get("keywords"),
                "created_at": _iso(job.get("created_at")),
                "elapsed_sec": _elapsed(job),
                "exit_code": job.get("exit_code"),
            }
            for job in jobs
        ],
    }


def cmd_results(args: argparse.Namespace) -> Dict[str, Any]:
    job = _refresh(_load_job(args.job_id) if args.job_id else _latest_job())
    files = _find_results(job)
    return {
        "ok": True,
        "job_id": job["id"],
        "status": job["status"],
        "save_root": _relative(_save_root(job)),
        "files": files,
        "totals": {
            "files": len(files),
            "records": sum(item["records"] or 0 for item in files),
        },
    }


def cmd_peek(args: argparse.Namespace) -> Dict[str, Any]:
    job = _refresh(_load_job(args.job_id) if args.job_id else _latest_job())
    files = _find_results(job)
    if args.kind != "all":
        wanted = "contents" if args.kind == "notes" else args.kind
        files = [item for item in files if item["item"] == wanted]
    if not files:
        raise CliError(
            EXIT_NOT_FOUND,
            f"no {args.kind} result files for job {job['id']} yet",
            job_id=job["id"],
            status=job["status"],
        )

    target = Path(files[0]["path"])
    if not target.is_absolute():
        target = ROOT / target
    items = _read_records(target, max(1, args.limit))
    return {
        "ok": True,
        "job_id": job["id"],
        "file": _relative(target),
        "total": files[0]["records"],
        "returned": len(items),
        "items": items,
    }


# --------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.agent_cli",
        description="MediaCrawler agent CLI：后台作业 + JSON 输出",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="启动一个后台爬取作业")
    start.add_argument("--platform", required=True, choices=PLATFORMS)
    start.add_argument("--type", dest="crawler_type", default="search", choices=CRAWLER_TYPES)
    start.add_argument("--keywords", default="", help="搜索关键词，逗号分隔")
    start.add_argument("--note-urls", default="", help="detail 模式的笔记 URL/ID，逗号分隔")
    start.add_argument("--creator-ids", default="", help="creator 模式的创作者 URL/ID，逗号分隔")
    start.add_argument("--max-notes", type=int, default=20)
    start.add_argument("--comments", default="no", choices=("yes", "no"))
    start.add_argument("--sub-comments", dest="sub_comments", default="no", choices=("yes", "no"))
    start.add_argument("--media", default="no", choices=("yes", "no"))
    start.add_argument("--login", default="cookie", choices=LOGIN_TYPES)
    start.add_argument("--cookie-file", default="", help="包含 Cookie 的文件；值不会进 argv/日志")
    start.add_argument("--save-option", dest="save_option", default="jsonl", choices=SAVE_OPTIONS)
    start.add_argument("--save-path", dest="save_path", default="")
    start.add_argument("--concurrency", type=int, default=0)
    start.add_argument("--headless", default="", choices=("", "yes", "no"))
    start.add_argument("--allow-concurrent", action="store_true", help="允许同平台多作业并行")
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="查看作业状态与最近日志")
    status.add_argument("job_id", nargs="?")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="读取作业日志尾部")
    logs.add_argument("job_id", nargs="?")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.add_argument("--grep", default="", help="按正则（失败则按子串）过滤")
    logs.set_defaults(func=cmd_logs)

    stop = sub.add_parser("stop", help="停止作业（先 SIGTERM，必要时 --force）")
    stop.add_argument("job_id", nargs="?")
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--force", action="store_true")
    stop.set_defaults(func=cmd_stop)

    listing = sub.add_parser("list", help="列出作业")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    results = sub.add_parser("results", help="列出本次作业产生的数据文件与记录数")
    results.add_argument("job_id", nargs="?")
    results.set_defaults(func=cmd_results)

    peek = sub.add_parser("peek", help="预览结果文件的前几条记录")
    peek.add_argument("job_id", nargs="?")
    peek.add_argument("--kind", default="contents", choices=("contents", "comments", "notes", "all"))
    peek.add_argument("--limit", type=int, default=5)
    peek.set_defaults(func=cmd_peek)

    doctor = sub.add_parser("doctor", help="检查运行环境（依赖、浏览器、数据目录、登录态）")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
    except CliError as exc:
        _emit({"ok": False, "error": exc.message, **exc.extra})
        return exc.code

    _emit(payload)
    if args.command == "doctor" and not payload.get("ok"):
        return EXIT_DOCTOR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
