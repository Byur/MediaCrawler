# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/agent_runner.py
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

"""Agent 作业的子进程入口（由 tools/agent_cli.py 启动，不建议手动调用）。

职责只有三件：

1. 从 ``MC_JOB_SPEC`` 读取 argv，等价于执行 ``python main.py <argv>``；
2. 从 ``MC_COOKIES`` 读取 Cookie（避免出现在 argv / ps / shell history 里）；
3. 运行结束后把退出码写入 ``MC_JOB_STATUS_FILE``，供父进程轮询。

Cookie 只存在于本进程的内存与环境变量中，不落盘、不打印。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

# 以脚本方式启动时 sys.path[0] 是 tools/，需要把仓库根目录加回来
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_status(path: Path, exit_code: int, error: str | None) -> None:
    payload = {
        "exit_code": exit_code,
        "finished_at": time.time(),
        "error": error,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    spec_path = Path(os.environ["MC_JOB_SPEC"])
    status_path = Path(os.environ["MC_JOB_STATUS_FILE"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    # main.py 内部的 parse_cmd() 会重新读取 sys.argv，这里先把参数摆好。
    sys.argv = ["main.py", *spec["argv"]]

    cookies = os.environ.get("MC_COOKIES")
    if cookies:
        # 必须在 import main 之前写入 config：main 里的 parse_cmd()
        # 以 config.COOKIES 作为 --cookies 的默认值。
        import config

        config.COOKIES = cookies

    exit_code = 0
    error: str | None = None
    try:
        import main as main_module

        asyncio.run(main_module.main())
    except SystemExit as exc:  # typer 参数校验失败等
        exit_code = int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 - 需要把任何失败写回状态文件
        exit_code = 1
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        _write_status(status_path, exit_code, error)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
