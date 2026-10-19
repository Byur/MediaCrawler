# Agent 接口设计

> 目标：让 agent（Claude Code / DeepSeek Harness / 其它带 shell 的编码 agent）能安全、可观测地驱动本仓库采集数据。
> 实现：`tools/agent_cli.py` + `tools/agent_runner.py` + 全局 skill `~/.agents/skills/mediacrawler/SKILL.md`

## 1. 为什么选 CLI 而不是 MCP

| 维度 | CLI + Skill（本方案） | MCP Server |
|---|---|---|
| 宿主要求 | 只要有 shell / bash 工具 | 需要 MCP 客户端注册、传输层（stdio/HTTP）、常驻进程 |
| 接入成本 | 0 配置，`python -m ...` 即可 | 每个宿主单独配置；本仓库不假设宿主支持 |
| 可测试性 | 子命令可直接单测、可手工复现 | tool schema + JSON-RPC 需要额外测试桩 |
| 失败语义 | 退出码 + JSON，天然可编程 | `isError` 字段 + 结构化内容 |
| 复用现有代码 | 直接复用 `main.py` 的参数与流程 | 同左，但要再包一层 |
| 跨客户端复用 | 任何 shell 环境都能用 | MCP 客户端的优势 |

结论：**CLI 作为稳定内核，Skill 作为操作手册**。MCP 的收益主要在"跨客户端复用"，而本场景是单机、单仓库、已有成熟 CLI，额外起一个常驻服务不划算。若将来要做 MCP，只需把 8 个子命令各包一个 tool（见 §7），底层契约不变。

## 2. 架构

```
agent
  └─ 读取 skill: ~/.agents/skills/mediacrawler/SKILL.md   （何时用、怎么用、什么时候停）
       └─ bash: python -m tools.agent_cli <cmd>       （JSON 输出 + 退出码）
            └─ tools/agent_runner.py                  （子进程入口：argv、cookie、退出码）
                 └─ main.py → media_platform/*         （现有爬虫，未改业务逻辑）
                      └─ data/<platform>/<format>/...  （结果文件）
```

作业元数据放在 `data/agent/jobs/`（已在 `.gitignore` 的 `data/` 之下）：

```
<id>.json        作业记录（状态、pid、参数、时间）
<id>.spec.json   子进程启动参数（不含 cookie）
<id>.done.json   退出码 / 错误（由 runner 写）
<id>.log         子进程 stdout/stderr
```

## 3. 命令契约

| 命令 | 作用 | 关键参数 | 输出要点 |
|---|---|---|---|
| `doctor` | 环境自检 | — | `ok`、`checks[]`、`config` |
| `start` | 后台启动作业 | `--platform --type --keywords/--note-urls/--creator-ids --max-notes --comments --sub-comments --media --login --cookie-file --save-option --save-path --concurrency --headless` | `job.id`、`pid`、`argv`、`next` |
| `status [id]` | 状态 + 最近 5 行日志 | 省略 id 取最近作业 | `status`、`elapsed_sec`、`exit_code`、`error`、`last_log[]` |
| `logs [id]` | 日志尾部 | `-n`、`--grep` | `lines[]` |
| `stop [id]` | SIGTERM，超时后可 `--force` | `--timeout`、`--force` | `stopped`、`forced` |
| `list` | 作业列表 | `--limit` | `jobs[]` |
| `results [id]` | 本次作业产出的文件 | — | `files[]`（path/item/records）、`totals` |
| `peek [id]` | 预览结果记录 | `--kind contents/comments/all`、`--limit` | `items[]` |

设计约束：

- **不复制业务逻辑**：`start` 只负责把参数翻译成 `main.py` 的 argv，平台差异由 `main.py` 处理。
- **默认小样本**：20 篇、不抓评论、不下媒体、并发 1；agent 必须显式放大参数。
- **同平台互斥**：默认拒绝同平台并行作业（浏览器 profile 与限速都会互相踩），除非 `--allow-concurrent`。
- **`start` 秒回**：不阻塞等待爬取完成，避免 agent 的 shell 超时。

## 4. JSON 与退出码

所有命令 stdout 只输出一个 JSON 对象（`indent=2`），失败也输出：

```json
{"ok": false, "error": "job not found: xxx", "job_id": "xxx"}
```

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数/用法错误 |
| 3 | 作业或结果不存在 |
| 4 | `doctor` 检查未通过 |
| 5 | 子进程启动失败 |
| 6 | 同平台已有作业在跑 |

agent 应优先按退出码分支，其次读 `error`；不要用正则解析人类日志来判断成败。

## 5. 作业状态机

```
created ──start──▶ running ──exit 0──▶ finished
                     │
                     ├──exit≠0──▶ failed
                     ├──stop────▶ stopped
                     └──进程消失且无 done 文件──▶ lost
```

- `status` 每次都会用 pid 存活与 `done.json` 校正状态，不依赖上一次写盘。
- `lost` 通常意味着被系统/用户强杀；此时只能看日志末尾推断原因。

## 6. 安全与合规设计

- **Cookie 不进 argv**：`--cookie-file` 读取后经 `MC_COOKIES` 环境变量传给 runner，`ps`、shell history、作业 JSON 里都不会出现 Cookie 值；作业记录只保存 `cookie_source: file:<path>`。
- **不自动对抗风控**：验证码（461/471）不重试、直接抛给上层；429 只退避 2 次；403/401 立即停。
- **默认参数即上限**：agent 不显式放大就不会产生大流量。
- **不删除数据**：CLI 只读 `data/` 与 `browser_data/`，不清理用户历史。
- **Skill 里写死升级规则**：同一错误出现 2 次必须停下来问人，禁止 agent 自行尝试绕过。

## 7. 如果将来要 MCP

CLI 的 JSON 契约就是 MCP tool 的返回体，包装成本很低：

```python
# 伪代码
@tool("crawl_start", schema={"platform": str, "keywords": str, "max_notes": int})
def crawl_start(**kwargs):
    return run_cli(["start", ...])          # 复用同一个 argparse
```

需要新增的只有：tool 注册、长期存活的 job 监听（或继续让 agent 轮询 status）。不建议现在做，因为宿主是否支持 MCP 不可控。

## 8. 自测

```bash
.venv/bin/python -m pytest tests/test_agent_cli.py -q
.venv/bin/python -m tools.agent_cli doctor
python -m tools.agent_cli start --help
```

`tests/test_agent_cli.py` 用假 runner 覆盖：参数翻译、作业落盘、状态机（finished/lost）、结果统计、peek、cookie 不落 argv/JSON。
