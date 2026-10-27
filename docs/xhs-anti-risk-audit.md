# 小红书反爬 / 风控视角的项目现状评估

> 评估对象：本 fork（`MediaCrawler`，commit `8f58399`，2026-10-19 工作区）
> 评估范围：`media_platform/xhs/*`、`base/base_crawler.py`、`proxy/*`、`cache/*`、`config/*`、`tools/*`、依赖 `xhshow==0.2.0`
> 评估方式：**纯离线代码审阅 + 本地算法复现**，未向 xiaohongshu.com / rednote.com 发起任何请求。
> 定位：个人研究用，非商业化。下文提到的"绕过/规避"均指**提高请求真实性、降低对目标平台的无效打扰**，不涉及破解验证码或绕过付费/权限。

## 证据分级

| 标记 | 含义 |
|---|---|
| ✅ | 已在本机复现（贴了命令与输出） |
| 🔍 | 由代码路径确定，无需运行 |
| ❓ | 高置信推测，需要用自己账号抓一份真实请求对比后才能定论 |

---

## 0.5 实施状态（2026-10-19 更新）

第一批"低风险、高收益"项已经落地，全部离线可验证（`tests/test_xhs_anti_detect.py`，42 条用例）。
本轮**不提供任何反检测开关**：行为固定，先跑通、体验一版；需要 A/B 时切 git 版本即可。

| 原评估项 | 本次改动（固定行为） | 验证 |
|---|---|---|
| F1/F16（x2 平台与 UA/CH 冲突） | 启动后从 `page.evaluate` 采集真实 UA / UA-CH / 平台 / 语言；`x-s`、`x-s-common` 的 `x2` 与 HTTP 头同源；headful 下不覆盖浏览器 UA；headless / 采集失败时退回一组**自洽**的稳定身份样张（Mac Chrome 155，UA/UA-CH/x2 同源，启动时同时覆盖浏览器 UA）；启动时做 UA↔UA-CH↔x2 一致性检查 | `test_signature_platform_matches_configured_identity`、`test_stable_identity_is_internally_coherent`、`test_headless_capture_uses_stable_identity_with_real_b1`、`test_identity_conflicts_flags_headless_and_mismatch` |
| F3（b1 合成指纹逐请求抖动） | 读取 `localStorage['b1']` 注入 `x-s-common.x8`，并重算 `x9=crc32(b1)`；取不到时自动退回库的合成 b1 | `test_real_b1_overrides_synthetic_x_s_common` |
| F4（每请求自称新开页面） | 客户端生命周期内复用 `SessionManager`：`page_load_timestamp` 固定、`sequence` 递增；每 30 分钟（常量）模拟一次页面重载 | `test_session_state_evolves_within_one_page_load` |
| F5（库算好的头被丢弃） | 返回并使用 `x-xray-traceid` / `x-mns` / `xy-direction` | `test_sign_headers_carry_full_browser_header_set` |
| F6（Referer / 缓存头） | 删除 `pragma`；按接口生成 Referer（search_result / explore / user profile） | `test_endpoint_referer_follows_real_browsing_path`、`test_pre_headers_does_not_mutate_shared_headers` |
| F11（固定 2s 间隔） | `polite_sleep()`：在基准间隔上做 ±50% 均匀抖动，默认 base=2s 即每次 1~3s 随机（sleep 仍留在信号量内，作为每个并发槽位的节流器；理由见下） | `test_next_delay_stays_within_jitter_bounds`、`test_default_interval_covers_one_to_three_seconds` |
| F14（Cookie 登录打挂签名） | cookie 登录同时写入 `web_session` 与 `a1` | 代码审阅 + 真实登录需账号 |
| F18/F21（验证码重放旧签名） | `CaptchaError` 单独建模，**不做内联冷却重试**：抛给上层跳过该条数据；业务重试（最多 3 次）每次重新签名，不再重放 `x-s/x-t` | `test_captcha_response_raises_dedicated_exception`、`test_fetch_does_not_retry_captcha`、`test_fetch_re_signs_on_every_attempt` |
| F19（429/403 击穿任务） | 429 退避重试（2→4s，上限 60s），403/401 立即停；search/评论层降级跳过而非崩溃 | `test_fetch_retries_429_with_backoff`、`test_fetch_does_not_retry_403`、`test_search_breaks_gracefully_when_access_restricted` |
| 附录 D（has_more 丢数据） | 先消费本页 `items`，再判断 `has_more` | `test_search_processes_items_even_when_has_more_is_false` |

尚未实施（属于第二/三层，需要更多环境依赖或账号实验）：HTTP/2 + 连接复用 + cookie jar（F7/F10）、`curl_cffi` 指纹（F8）、代理与浏览器同源（F15）、预热浏览与会话预算（F12/F22）、软封禁判定（F20）。请求级审计日志本轮也不做：先体验行为，控制台里的验证码/限流日志足够判断；要量化时再加。

> **关于"把 sleep 移出信号量"**：实现时发现这条不能单独做。当前 sleep 放在信号量内部，等价于"每个并发槽位的最小请求间隔"，整体是平滑的；一旦移出去而并发 >1，就变成"先连发一串请求、再集体静默"的突发形态，反而比固定间隔更像脚本。正确做法是先加一层**全局请求节流器**（按 `MAX_CONCURRENCY_NUM` 分摊间隔，保证相邻请求开始时间恒定），这属于 1~2 天档位的重构，因此本轮保留 sleep 在信号量内，只做随机化。

**涉及文件**：新增 `media_platform/xhs/browser_profile.py`、`tools/humanize.py`、`tests/test_xhs_anti_detect.py`；修改 `media_platform/xhs/{playwright_sign,client,core,login,exception}.py`、`config/{base_config,xhs_config}.py`。回归：`python -m pytest tests/ -q`（2026-10-27 复核：354 passed / 8 skipped）。

> **review 后收敛（2026-10-19）**：删除了验证码内联冷却重试（461 通常意味着出口已被标记，冷却重放只会加重风控）、`x-rap` / `sign_format` 两个未验证的实验开关、`browser_profile` 里 UA→UA-CH/语言的合成兜底（没采到就不发该头），以及未使用的属性（`sign_with_xhshow` 作为旧版公共接口保留，内部仍走原始实现）。随后按"先体验一版"的要求，把其余反检测开关（对齐身份 / 真实 b1 / 按接口 referer / 会话轮换 / 重试参数 / 抖动比例）全部固化为代码常量，配置面回到上游原样；长停顿、审计日志则直接删除（前者明确不做，后者等要量化时再加）。同时修了三个小问题：导航头里 `sec-fetch-site: none` 与 `referer` 打架、`query_self` 绕过重试导致启动抖动被误判为未登录、`get_creator_info` 丢失重试。

> **review 后修复（2026-10-27）**：修了两个会改变运行结果的回归，并确定 headless 的 UA 策略：
> 1. 非 JSON 响应（WAF/验证码 HTML 页、空 body、5xx 页面）在 `request()` 里转成 `DataFetchError`；重试耗尽的传输层错误（`httpx.RequestError`）也统一包装成 `DataFetchError`，不再让 `JSONDecodeError` / `httpx.ReadTimeout` 穿过 `asyncio.gather` 终止整批任务。
> 2. 笔记详情 API 失败（`DataFetchError`）后恢复 HTML 兜底：父版本里这类错误经 tenacity 包成 `RetryError` 走到兜底，改成 `_fetch` 后该路径丢失，现在显式补回。
> 3. 登录后 `refresh_browser_profile()` 只做补充：采集失败、异常或新结果没有 b1 时保留启动时的真实 b1，不再把 b1 冲成空（那会退回逐请求变化的合成指纹）。
> 4. headless（`HEADLESS` / `CDP_HEADLESS`）使用稳定身份样张 Chrome 155 / macOS：启动时同时覆盖浏览器 UA（避免 `HeadlessChrome` 自报），采集时只保留真实 b1；headful / CDP 连接真实浏览器仍使用真实身份。注意 headless 仍有其他可识别特征，正式采集建议 headful。
> 另外：xhshow 导入失败改为 fail-fast（不再静默发送无签名请求）；已登录但启动时没等到 b1 会在登录校验后补采一次；修正 iOS UA 被误判为 macOS、异常 `brands` 结构导致 capture 抛错的问题；`identity_conflicts` 的平台检查前移（UA-CH 缺失时也能发现平台矛盾）。

> **UA 一致性（2026-10-27 更新）**：UA、UA-CH 三件套、签名 `x2`、媒体下载 UA 现在都由同一份 `BrowserProfile` 派生，不存在两套来源。启动时 `identity_conflicts()` 会检查四件事并打 WARNING：UA 含 `HeadlessChrome`（headless 模式自报家门）、UA 大版本不在 `sec-ch-ua` 版本里、UA 平台与 `sec-ch-ua-platform` 不一致、平台无法推导导致 `x2` 退回 Windows。headful / CDP 连接真实浏览器时使用真实身份；headless（Playwright 的 chrome-headless-shell 会自报 `HeadlessChrome`，且没有 `navigator.userAgentData`）与采集失败时使用一组自洽的 Mac Chrome 155 稳定样张，保证 HTTP 头、签名、媒体 UA 内部一致。headless 下已经不再把 `HeadlessChrome` 发出去，但 headless 仍有其他可识别特征，正式采集建议 `HEADLESS=False` 或 CDP 连接真实浏览器。

**怎么验证这一版**：跑一次默认配置（搜索 1~2 个关键词即可），看控制台是否出现 `CAPTCHA appeared`、`Access restricted`、`300011/300012`；再对比 `data/` 里的笔记/评论数量是否正常。启动时会打印浏览器身份与 UA/UA-CH 一致性告警，每次请求的休眠时长也会打印。要更细的指标（461 率、间隔 CV、b1 去重数）时再加审计日志。

---

## 0. TL;DR：如果我是小红书的策略工程师，我会先盯这 7 个点

按"信号强度 ÷ 修复成本"排序。前 6 项都不需要动架构，改参数/加几行就能做 A/B。

| # | 可观测信号 | 项目中的位置 | 判定 | 修复成本 |
|---|---|---|---|---|
| 1 | 同一 `a1`/`web_session` 的请求，`x-s` 与 `x-s-common` 内部声明 **Windows**，HTTP `User-Agent` 却声明 **macOS**；`sec-ch-ua-platform` 又是 Windows，且 Chrome 大版本有 **126 / 136 / 137 三个值**同时出现在同一条链路上 | `core.py:68`、`core.py:402-408`、`xhshow/config/config.py` | ✅ 已解出 `x-s` 明文 | S |
| 2 | `x-s-common` 的 `b1` 是**合成指纹**：所有用户共享同一个 canvas hash `742cc32c`、同一组 SEC 占位符、同一个 `x36` 每请求随机跳变；而项目**以前的实现**是直接读浏览器 `localStorage["b1"]` | `playwright_sign.py:43-72`、`xhshow/generators/fingerprint.py`、git `b5dab6d` | ✅ 已解出 `b1` 并做过跨 cookie 对比 | S～M |
| 3 | `x-s` 内层 144 字节 payload 的 `sequence` / `page_load_timestamp` / `window_props_length` **每个请求都在随机**（项目没用 `SessionManager`），等于每个请求都自称"刚刚新开了一个页面" | `playwright_sign.py:46-47`、`xhshow/core/crypto.py:104-136` | 🔍 代码确定 | S |
| 4 | 所有 API 请求走 **HTTP/1.1**、`accept-encoding: gzip, deflate`、**每个请求新建 TCP+TLS 连接**、TLS 是 Python/OpenSSL 指纹；而真实网页是 h2 + Chrome(BoringSSL) | `tools/httpx_util.py`、`client.py:150` | ✅ 已打印出线上实际请求头 | M |
| 5 | 请求间隔是**固定 2.000s**（并且 sleep 被放在信号量内部）；提交 `2bce359` 明确把 `random.uniform(1, MAX)` 改成了固定值 | `core.py:190,326,371,381`、git `2bce359` | 🔍 代码 + git 历史 | S |
| 6 | 遇到 461/471 验证码 / 429 时**没有冷却和退避**：同一份签名被 tenacity 重放 3 次；关键词搜索层只 catch `DataFetchError`，一个 429 直接终止整个任务 | `client.py:127-196`、`core.py:192` | ✅ 已用 mock 复现"429 直接击穿" | S |
| 7 | 登录态只在启动时 `pong()` 校验一次；响应里的 `Set-Cookie` 被丢弃；代理刷新只换 httpx 不换浏览器 → 浏览器 IP 与 API IP 可能不一致 | `client.py:263-297`、`proxy/proxy_mixin.py:47-71` | 🔍 代码确定 | M |

一句话结论：**在默认配置（15 篇笔记、单关键词、2s 间隔）下，限速不是主要矛盾；"身份一致性"才是。** 现在的请求形态是"一个自称 Windows 的合成指纹 + 一个自称 Mac Chrome 126/137 的 UA + Python 的 TLS，共用同一个账号 Cookie"，任何一层做交叉校验都会命中。

---

## 1. 第一层：单请求内就能看出来的矛盾（改参数即可，优先级最高）

这一层不需要限速、不需要代理，甚至不需要登录态，是"性价比最高"的实验区。

### F1. `x-s` / `x-s-common` 声明 Windows，UA 声明 Mac ✅

`media_platform/xhs/client.py:90-125` 用 `xhshow` 生成签名；`playwright_sign.py:43-72` 把 `uri/data/cookie` 交给它。
`x-s` 外层是 `XYS_` + 自定义 base64 的**明文 JSON**，可以直接解出来：

```text
x-s decoded payload: {"x0":"4.3.5","x1":"xhs-pc-web","x2":"Windows","x3":"mns0301_...","x4":"object"}
x-s-common decoded:  {"s0":5,...,"x2":"Windows","x3":"xhs-pc-web","x4":"4.86.0","x5":"<a1>","x8":"<b1>",...}
```

而 `core.py:408` 发出去的 `user-agent` 是 `Macintosh ... Chrome/137.0.0.0`。
**同一个请求里，"签名说 Windows"和"UA 说 macOS"不可能同时为真。** 这不是需要破解 x3 才能发现的东西，解一层 base64 就够了。

> 有意思的是，重构前的实现（git `b5dab6d` 删掉的 `secsign.py`）里写的是 `"x2": "Mac OS"`，是按真实浏览器填的。换成 xhshow 之后被写死成 Windows。

**实验**：把 `CryptoConfig` 里的平台相关字段改成与运行环境一致后再跑 24h，对比 461/471 与 `300011` 的出现率。xhshow 支持 `CryptoConfig().with_overrides(...)`，但 `SIGNATURE_DATA_TEMPLATE` / `SIGNATURE_XSCOMMON_TEMPLATE` 是 `dataclass` 的默认工厂，最省事的是在 `playwright_sign.py` 里构造后用 `dataclasses.replace` 覆盖，或者直接包一个子类。

### F2. `sec-ch-ua` / `sec-ch-ua-platform` 与 UA 主版本号互相打架 ✅

`core.py:402-408` 三个值全部硬编码，而且不是同一次取值：

| 头 | 值 | 问题 |
|---|---|---|
| `user-agent` | `Chrome/137.0.0.0`（Mac） | 与浏览器真实 UA 无关 |
| `sec-ch-ua` | `"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"` | 136 ≠ 137 |
| `sec-ch-ua-platform` | `"Windows"` | 与 UA 的 Mac 冲突 |
| `self.user_agent`（浏览器上下文） | `Chrome/126.0.0.0`（Mac） | 第三个版本号 |

也就是说一条完整链路（浏览器导航 → Cookie 签发 → API 请求）上，Chrome 大版本同时存在 **126 / 136 / 137**。真实 Chrome 的 CH 与 UA 大版本永远一致，这是最便宜的一致性检查之一。

**实验（10 分钟）**：在页面里打印真实值，然后把硬编码替换掉：

```python
ua   = await page.evaluate("navigator.userAgent")
ch   = await page.evaluate("navigator.userAgentData.getHighEntropyValues(['brands','platform','platformVersion','fullVersionList'])")
```

如果 `ua` 与 `ch` 本身就对不上（Playwright 的 `user_agent` 覆盖只改 `navigator.userAgent` 和请求头，**不改 `navigator.userAgentData`**），说明 `core.py:68 + core.py:444` 的 UA 覆盖本身就是一个可被 JS 读出来的矛盾点 —— 这类"覆盖 UA 但不覆盖 UA-CH"的泄漏在 Playwright 上是已知问题，值得先花 5 分钟验证（❓）。

### F3. `b1` 合成指纹：所有用户共享同一个 canvas hash ✅

`x-s-common.x8` 是 `b1`（ARC4 + 自定义 base64，key `xhswebmplfbt`），解出来是浏览器指纹。实测结果：

```text
# 同一个 a1，连续 4 次请求
unstable fields: ['x36', 'x44']        # x36 随机 1..20；x44 是毫秒时间戳
stable fields:   x33-x39, x42-x46, x48-x52, x82

# 换一个完全不同的 a1 再签
different a1 -> differing b1 keys: ['x36', 'x44']
shared canvas hash x43: 742cc32c / 742cc32c
shared SEC placeholders x45: __SEC_CAV__1-1-1-1-1|__SEC_WSA__|
x36 (random 1..20): ['4', '8', '20', '9', '17', '7']
```

两个结论：

1. **跨用户碰撞**：`742cc32c` 是 `xhshow/data/fingerprint_data.py:127` 的常量，所有用这个库的人上报同一个 canvas 指纹、同一组 `__SEC_CAV__` 占位符、同一组事件位图 `x37/x38`。这等于自带一个"同款客户端"的聚类标签。
2. **同一设备不稳定**：`x36` 每个请求随机跳变。真实浏览器的设备指纹在同一标签页内应当是稳定的（`x44` 时间戳型字段可能每请求更新，但 `x36` 这种计数/布尔型字段没有理由跳）。

拼接起来就是：**同一个 `a1` 的设备指纹在抖动，不同 `a1` 的指纹又长得一样。**

最扎心的是，项目自己以前的实现是对的 —— `secsign.py` 里：

```python
local_storage = await self.playwright_page.evaluate("() => window.localStorage")
signs = sign(a1=..., b1=local_storage.get("b1", ""), x_s=x_s, x_t=...)
```

**实验**：在登录后的页面里 `localStorage` 读一次真实 `b1`，与 xhshow 生成的 `b1` 做字段级 diff。如果差异明显（大概率），直接把真实 `b1` 注入 `sign_xs_common`（`x-s-common` 的结构很简单：`{s0:5,...,x5:a1,x8:b1,x9:crc32(b1)}`），改动不到 30 行，是**投入产出比最高的一项**。

### F4. 每个请求都自称"刚打开一个新页面" 🔍

`playwright_sign.py:46-47` 每次调用都 `Xhshow()` 新建实例，且从不传 `session=`。xhshow 在无 session 时的分支（`xhshow/core/crypto.py:118-136`）会：

```python
time_offset        = random.randint(10, 50)          # 假装页面是 10~50s 前加载的
sequence_value     = random.randint(15, 50)          # 序列号随机跳
window_props_length= random.randint(1000, 1200)      # window 属性数量随机跳
```

而 xhshow 已经提供了 `SessionManager`（`xhshow/session.py`）：`page_load_timestamp` 固定、`sequence` 逐请求 +0/1、`window_props_length` 缓慢增长。**这是"同一会话内状态应该连续"的标准语义**，项目把它丢掉了。

**实验**：`XiaoHongShuClient.__init__` 里挂一个 `SessionManager`，`XhsSigner` 把它透传给 `sign_headers_*`，观察 `300011`/`461` 是否下降。注意还要模拟"页面重载"：跑几小时后应重建 SessionManager（否则等于一个页面开了 6 小时）。

### F5. 库算好了的头被丢掉 🔍

`xhshow.sign_headers_*` 返回 7 个字段：

```text
['x-b3-traceid', 'x-mns', 'x-s', 'x-s-common', 'x-t', 'x-xray-traceid', 'xy-direction']
```

`playwright_sign.py:58-63` 只保留 4 个，并且从不传 `x_rap=True`：

| 被丢弃/未生成 | 说明 |
|---|---|
| `x-xray-traceid` | 链路追踪，真实客户端会带 |
| `x-mns: "unload"` | 页面生命周期信号 |
| `xy-direction` | 分片键，数据类接口（`user_posted` 等）相关 |
| `x-rap-param` | docstring 自己写着 "Required for feed, search, and note publishing endpoints" |

这是"零成本补齐"的典型：库已经算好了，只要把 dict 合并进去（`x_rap=True` 需要额外传参）。

### F6. Referer / 缓存头不像浏览器 ✅

- `core.py:401` 把 `referer` 固定成 `https://www.xiaohongshu.com/`。真实客户端搜索接口的 referer 是 `/search_result?keyword=...`，笔记详情是 `/explore/<note_id>`。**接口类型和来源页对不上**，是最容易做的一条规则。
- `core.py:396,399` 带了 `cache-control: no-cache` + `pragma: no-cache`。Chrome 的 fetch/XHR 默认**不会**带 `pragma`（这是 curl / Postman / requests 的习惯）。实测 wire 头里两个都在，属于"浏览器不会发的多余头"。
- 实测中 HTTP/1.1 还带了 `connection: keep-alive`（h2 不应存在此头）。

**实验**：按接口类型设置 referer；删掉 `pragma`。这类改动风险最低，可以直接看 461 率。

---

## 2. 第二层：传输层指纹（不改架构做不干净）

### F7. 全程 HTTP/1.1 + 每请求新建连接 ✅

`client.py:150`：

```python
async with make_async_client(proxy=self.proxy) as client:
    response = await client.request(...)
```

`tools/httpx_util.py` 只设了 `verify`，没有 `http2=True`，而且 **venv 里根本没装 `h2`**（已确认 `{h2: False, brotli: False, zstandard: False}`）。于是：

```text
host: edith.xiaohongshu.com
accept-encoding: gzip, deflate
connection: keep-alive        ← h1.1 独有
```

真实网页对 `edith.xiaohongshu.com` 走 h2，一条连接上 multiplex 几十个请求，`accept-encoding: gzip, deflate, br, zstd`，没有 `connection` 头。差异点有三个，且互相独立：

1. 协议版本（h1.1 vs h2）；
2. 连接复用（每请求三次握手 + TLS 握手 vs 长连接）；
3. 压缩算法集合（缺 `br`/`zstd`）。

第 2 点在没有代理时尤其明显：一个公网 IP 每 2 秒新建一条 TLS 连接，持续几十分钟，在连接层统计上就不像人。

**实验**：`httpx.AsyncClient(http2=True)` + `pip install "httpx[http2,brotli,zstd]"`，再把 client 提到成员变量复用。改动小，但只是"更接近"，TLS 指纹仍是 OpenSSL（见 F8）。

### F8. TLS / JA3 / JA4 是 Python 的 🔍

即使开了 h2，`ssl` 模块的 cipher suite 顺序、extension 顺序、ALPN（只 advertise `http/1.1`）都和 Chrome 的 BoringSSL 不同。这是 Cloudflare/Akamai 类方案的标准输入，也是小红书这类自研网关最容易拿到的"零成本"信号。

三条路线，成本递增、收益递增：

| 方案 | 改动 | 收益 | 风险 |
|---|---|---|---|
| `curl_cffi`（`impersonate="chrome"`）| 替换 `make_async_client`，保持 request 接口 | JA3/JA4 + h2 + 头顺序一次性对齐 | 需要适配 API（`AsyncSession`），代理参数略不同 |
| 浏览器内 `fetch`（`page.evaluate`） | 请求发起方换成页面 | TLS/h2/头/Cookie/Referer 全部天然正确 | 并发受 tab 限制、速度慢 |
| CDP `Fetch` 拦截 | 保留 httpx 的代码结构，把网络层代理给 Chrome | 同上，且能继续用现成的解析逻辑 | 实现复杂度最高 |

作为研究 fork，**方案 2 其实是最"诚实"的**：你已经有真实登录态的浏览器了，签名却还在 Python 里用合成环境造 —— 这是当前架构最根本的割裂。旧实现至少是"在页面里算签名"，现在连签名环境都是编的。

### F9. 头集合 / 头顺序 🔍

`client.py:118-123` 生成的头与浏览器 XHR 的头集合不完全一致（多了 `pragma`，少了 `x-xray-traceid`/`x-mns`/`xy-direction`/`x-rap-param`，`accept-encoding` 不同）。另外 `headers` 里用的是 `"X-S"`、`"x-S-Common"` 这种混合大小写；h1.1 下会原样上线，h2 下会被强制小写。启用 h2 后这一项自动消失，优先级低。

**建议的验证方法（一次投入，长期复用）**：用自己账号在 Chrome 里做一次搜索，DevTools → Network → 右键目标 XHR → *Copy as cURL*，和 `client.py` 实际发出的头做集合 diff。这条基线比任何猜测都可靠，附录 A 给了可用的解码脚本。

### F10. `Set-Cookie` 被丢弃，Cookie 只在启动时快照一次 🔍

`client.py:296` 的 `update_cookies` 只在登录后调用一次；之后每个请求都是新 `AsyncClient`，响应里的 `Set-Cookie` 没人接。真实浏览器会持续更新 `web_session` 等 cookie。后果：**服务端悄悄轮换了会话，客户端还在用旧的**，表现为"突然所有人 401/461"，而且因为 `pong()` 只在启动时跑（`core.py:108`），跑到一半失效不会被发现。

**实验**：把 client 复用起来（顺带解决 F7 的连接复用），用 `httpx.Cookies` 持久化，并在每 N 个请求后从 `browser_context.cookies()` 回灌一次。

---

## 3. 第三层：会话行为（最便宜、最容易被规则化检测）

### F11. 固定 2.000s 间隔，方差≈0 ✅（代码 + git）

git `2bce359`（"feat: support time deplay for all platform"）之前是：

```python
crawl_interval = random.uniform(1, config.CRAWLER_MAX_SLEEP_SEC)   # 旧
```

之后被改成：

```python
crawl_interval = config.CRAWLER_MAX_SLEEP_SEC                      # 现在，见 core.py:226/371
```

而且新增的 sleep 被放进了信号量内部（`core.py:326`）：任务持有 semaphore 睡 2s，导致 `MAX_CONCURRENCY_NUM` 实际上既不能加速也不能打散节奏。若把并发调大，行为会变成"**5 个请求同时发出 → 静止 2s → 再 5 个**"的脉冲，这比均匀低速更像机器人。

**实验**：
1. ✅ 已做：`sleep(random.uniform(0.5, 1.5) * base)`，默认 base=2s 时即 1~3s；sleep 仍留在 semaphore 内（理由见 §0.5）；
2. ❌ 暂不做：偶发长停顿；
3. ❌ 暂不做：token bucket / 全局节流器。

> **决策（2026-10-19）**：长停顿，以及"真人间隔右偏 → 用对数正态/三角分布替代均匀分布"这类设计，**明确不做**。当前节奏就是 1~3s 均匀随机，不再把"分布形状是否像人"作为本轮优化项；要改也只换公式，不加开关。

### F12. 没有预热浏览 🔍

`core.py:103-104` 打开首页后立刻 `pong()`（`/user/selfinfo`）→ 登录 → 直接打搜索接口。真实用户的行为序列是：首页 → `/homefeed` 多次 → 滚动 → 点进笔记 → 搜索 → 搜索页滚动 → 点笔记。当前会话里**没有任何 feed/笔记浏览请求**，"会话第一次动作就是搜索 API"本身就是一个强特征（尤其对新 `a1`）。

**实验**：`context_page.goto` 之后，随机滚动 2~4 屏 + `wait 3~8s`，再开始爬；或者先打 2~3 次 homefeed。代价是几十秒，收益在账号信任度上。

### F13. 登录态只在启动时校验一次 🔍

`core.py:108` 的 `if not await self.xhs_client.pong()` 是唯一的校验点；`pong()` 失败也不会重登。长跑任务里，`web_session` 失效后只能靠一个个请求失败来暴露。

**实验**：每 N 个请求或每次 4xx 时调一次 `pong()`，失败就走 `XiaoHongShuLogin` 重登（CDP 模式连的是真实浏览器，重登成本很低）。

### F14. Cookie 登录路径直接把签名打挂 🔍

`login.py:213-224`：

```python
for key, value in ...:
    if key != "web_session":   # Only set web_session
        continue
```

只写 `web_session`，不写 `a1`。而 `xhshow` 的 `sign_headers` 在 `a1` 缺失时直接 `raise ValueError("Missing 'a1' in cookies")`。所以"cookie 登录 + 全新 profile"是必然失败的组合；只有在 `browser_data` 里已经有 `a1` 时才能跑。这不是风控问题，但它把"用别人的 cookie 快速复现"这条实验路径堵死了，值得修。

### F15. 代理：只在过期时换，且浏览器不跟着换 🔍

- `proxy/proxy_mixin.py:57-71` 只在 `is_current_proxy_expired()` 时换 IP；**被 429/300011 打中不会立刻换**。
- 换的时候只改 `self.proxy`（httpx 用），浏览器上下文是启动时用原代理建的（`core.py:94-99`），**浏览器和 API 从此走不同出口 IP**。如果服务端把 `a1`/登录态与 IP 绑定，这等于自己制造"异地登录"。
- 代理可用性校验是 `https://echo.apifox.cn/`（`proxy/proxy_ip_pool.py`），只能证明"能上网"，不能证明"没被小红书拉黑"，而且把一个第三方域名写进了爬虫的探测链路。
- 默认 `ENABLE_IP_PROXY = False`，绝大多数人就是单 IP 裸跑。

**实验**：捕获到 429/300011 时，主动 `invalidate` 当前代理并重建**浏览器 + httpx** 两边的出口；校验目标换成平台自身的轻量接口（如首页 HTML），并记录"每个 IP 的首次被拒时间"，这才是真正的 IP 信誉指标。

### F16. 浏览器指纹参数没对齐 🔍

`core.py:430-444` / `tools/cdp_browser.py:378-395`：

- `viewport` 固定 `1920x1080`，无 `device_scale_factor`、无 `locale`、无 `timezone_id`；
- 如果宿主机时区/语言不是 `Asia/Shanghai`/`zh-CN`，而出口 IP 在国内，`Intl.DateTimeFormat().resolvedTimeZone` 与 IP 归属地对不上；
- CDP 模式新建 context 时又把 UA 覆盖成 `Chrome/126 Mac`（`cdp_browser.py:385`），如果连的是用户的真实 Chrome（137+/Edge），`navigator.userAgent` 与 `navigator.userAgentData` 会当场打架。

**实验**：`new_context(locale="zh-CN", timezone_id="Asia/Shanghai", device_scale_factor=2)`，并删掉 UA 覆盖（让浏览器用自己的）。`libs/stealth.min.js` 是 2023-06 由 puppeteer-extra 生成的，只补老一代特征（`webdriver`、`plugins`、`permissions`、`iframe.contentWindow`、`Function.prototype.toString`），且**只在 standard 模式加载**，CDP 模式不加载（也没有 `add_stealth_script` 调用）。在现代 Chromium 上它能覆盖的面已经很窄。

### F17. 并发结构 🔍

`MAX_CONCURRENCY_NUM = 1`（`base_config.py:105`）默认是串行，不算问题；但代码结构里 semaphore 是**每个批次新建**（`core.py:169,245,356`），没有全局节流器。一旦有人为了"快"把它改大，就会出现 F11 说的脉冲式并发，而且没有任何"单会话同时最多 1~2 个在途请求"的约束 —— 真实单标签页用户不会同时发 5 个搜索/详情请求。

---

## 4. 第四层：风控响应处理（"被打了之后怎么办"）

这一层不改判定，但决定"被判定后是降级还是崩溃"。

### F18. 461/471 验证码：重放同一请求 3 次，然后向上抛裸 `Exception` 🔍

`client.py:158-164`：

```python
if response.status_code == 471 or response.status_code == 461:
    verify_type = response.headers["Verifytype"]   # 缺头会 KeyError
    verify_uuid = response.headers["Verifyuuid"]
    raise Exception(msg)                            # 裸 Exception
```

`request()` 包了 tenacity 重试，而 `Exception` 不在豁免列表里 → **同一份 `x-s/x-t` 会被原样重放 3 次**（1s 间隔）。对验证码页面反复重放，是最容易把"一次挑战"升级成"封号"的操作。

补充：`get_note_detail_async_task` 只在**内层** `get_note_by_id` 上 catch 了 `RetryError`（`core.py:311-314`）；HTML 兜底那次（`core.py:317`）抛出的 `RetryError` 没人接，会穿透 `asyncio.gather`。

### F19. 429/403 直接击穿整个搜索任务 ✅

`core.py:192` 的 `except DataFetchError` 是搜索循环唯一的异常出口，而 `PlatformAccessError` 不是它的子类（`exception.py:29-31`）。已用 mock 复现：

```text
search() raised PlatformAccessError -> run aborts: HTTP 429
```

一条笔记被限流有防护（`core.py:334`），但**搜索接口本身被限流就把整个进程带走**。正确姿势：429 → 指数退避 + 换 IP/降速 + 继续，而不是退出。

### F20. 软封禁与"没有更多"不可区分 🔍

```python
notes_res = await self.xhs_client.get_note_by_keyword(...)
if not notes_res or not notes_res.get("has_more", False):
    break        # core.py:166-168
```

`get_note_by_keyword` 在 `success:false` 时抛 `DataFetchError`，但如果服务端换个姿势返回 `200 + success:true + items:[]`（软风控常见），代码只会打印 "No more content!" 并**静默结束**。更糟的是 `has_more` 的判断在取 `items` **之前**：只要第一页 `has_more=false`（结果不足 20 条的关键词就会这样），这一页的数据会**全部被丢弃**。这是个独立的数据完整性 bug，建议顺手修（把 `has_more` 判断挪到处理 `items` 之后）。

### F21. 重试语义：重放旧签名，且没有 `Retry-After` 🔍

`client.py:127-133`：`wait=wait_fixed(1)`，签名在 `request()` 之外生成。于是每次重试发的都是**同一个 `x-t`、同一份 `x-s`**，间隔 1s。真实客户端不会"每重发一次就复用同一个签名"。另外没有读 `Retry-After`、没有指数退避、没有 jitter。

**实验**：把签名移进重试循环（每次重签），`wait=wait_exponential_jitter(initial=2, max=60)`，并尊重 `Retry-After`。改动 10 行，效果直接体现在"被拒后是否还能继续"。

### F22. 无预算 / 无账号池 / 无熔断 🔍

没有"单账号每小时 N 次"的预算，没有"连续 K 次 461 就停 30 分钟"的熔断，也没有多账号轮换。对研究用途问题不大，但意味着**一旦进入风控状态，会持续撞墙**，而不是自动降级。

---

## 5. 实验矩阵（照着做就行）

建议一次只改一项，每个配置跑固定样本（例如 5 关键词 × 15 笔记 × 3 轮），记录 §6 的指标。

| # | 实验 | 改哪里 | 预期观察 |
|---|---|---|---|
| E1 | 对齐平台字段 | `x-s`/`x-s-common` 的 `x2` 改成实际 OS；`sec-ch-ua*`/UA 从 `page.evaluate` 现取 | 461/471 率下降；`300011` 减少 |
| E2 | 注入真实 `b1` | 从 `localStorage["b1"]` 读取，替换 `x-s-common.x8`（x9 同步用 crc32） | 指纹稳定性指标消失 |
| E3 | 会话序列连续 | `XhsSigner` 内复用 `SessionManager` | 若服务端校验 sequence，效果最明显 |
| E4 | 补齐头 | 加 `x-xray-traceid`/`x-mns`/`xy-direction`（`x-rap-param` 未验证，暂未加） | 单请求"缺头"告警消失 |
| E5 | 按接口设 referer | search → `/search_result?...`；feed/comment → `/explore/<id>` | 低风险的额外一致性收益 |
| E6 | 去掉浏览器不会发的头 | 删 `pragma`（以及 `cache-control`，视抓包基线而定） | 头集合 diff 缩小 |
| E7 | 打开 h2 + 连接复用 | `http2=True`、client 提为成员变量、`httpx[http2,brotli,zstd]` | 连接层统计正常化 |
| E8 | 随机化节奏 | ✅ `sleep(uniform(0.5,1.5)*base)`（默认 1~3s）；⬜ 长停顿 / token bucket（暂不做） | 间隔方差 CV 从 ~0.02 升到 ~0.3 |
| E9 | 预热浏览 | 首页滚动 2~4 屏、先打 homefeed | 新账号首次搜索的挑战率 |
| E10 | 风控响应分级 | 461/471 → 冷却 10~30min + 换 IP；429 → 指数退避 + 降速 | "一次挑战导致长跑中断"消失 |
| E11 | IP 与浏览器同源 | 换代理时重建 context；被拒时立即标记该 IP 死亡 | `300012`/`300011` 的恢复速度 |
| E12 | 登录态回灌 | 复用 client 的 cookie jar + 定期从浏览器同步 | 长跑中 401 大幅减少 |

---

## 6. 怎么衡量"有没有变好"

别用"能跑通"当指标。建议在 `XiaoHongShuClient.request()` 里加一行结构化日志（JSONL），每次请求记：

```json
{"ts": 1792377640.123, "uri": "/api/sns/web/v1/search/notes", "method": "POST",
 "status": 200, "code": 0, "verifytype": null, "proxy": "1.2.3.4:8080",
 "x_t": "1792377640071", "elapsed_ms": 312, "session_seq": 42,
 "bytes": 18345, "success": true}
```

然后算这几个指标（每个配置跑同样样本）：

| 指标 | 怎么看 | 健康区间（经验值，用于自我对比） |
|---|---|---|
| `461+471 / 100 req` | 验证码触发率 | 越低越好，目标是 0 |
| `300011 / 100 req` | 账号安全限制 | 0 |
| `300012 / 100 req` | IP 被拦 | 0（有代理时看单 IP） |
| `success:false / 100 req` | 业务拒绝率 | < 2 |
| 请求间隔 **CV**（std/mean） | 行为规律性 | 固定 2s 时约 0.01~0.05；目标 > 0.3 |
| 相同 `x-t` 的重复次数 | 重放 | 恒为 1 |
| `x-s-common.b1` 去重后数量 / 请求数 | 指纹稳定性 | 同一 `a1` 应 ≈ 1/K（仅时间戳字段变化时），现在每请求都不同 |
| 首次被拒延迟（分钟） | 账号"存活时间" | 越长越好 |

同时留一份**基线抓包**：用自己账号在 Chrome 里做同样操作，导出 HAR，把 `x-s`/`x-s-common`/`b1`/头集合和爬虫的逐字段对比。这是唯一能把 ❓ 变成 ✅ 的方法。

---

## 7. 路线图

**半天（只改参数 / 十几行）—— 已完成 ✅**
1. ✅ `x2` 平台字段 + UA/CH 从 `page.evaluate` 取（E1）
2. ✅ 删 `pragma`、按接口设 referer（E5/E6）
3. ✅ 补 `x-xray-traceid`/`x-mns`/`xy-direction`（`x-rap-param` 未验证，本轮不加）（E4）
4. ✅ sleep 随机化；⬜ 移出 semaphore（需先加全局请求节流器，见 §0.5 说明）

**1~2 天**
5. ✅ 注入真实 `b1` + `SessionManager`（E2/E3）
6. ⬜ `http2=True` + client 复用 + cookie jar（E7/E12）
7. ✅ 461/471/429 分级处理 + 指数退避 + 每请求重签（E10）
8. ✅ 修 `has_more` 顺序 bug；⬜ 软封禁判定（F20）

**一周（治本）**
9. ⬜ `curl_cffi` 或"请求回浏览器"二选一（E8 的根治）
10. ⬜ IP/浏览器同源 + IP 信誉表（E11）
11. ⬜ 预热浏览 + 会话预算/熔断（E9/F22）
12. ⬜ 把 §6 的指标做成每次运行的报告（审计日志本轮未做，先靠控制台日志判断）

---

## 附录 A：离线复现脚本

以下脚本**不联网**，只调用本地 `xhshow`，用来验证 F1/F3/F4。放在任意目录，用项目 venv 运行。

```python
# decode_xhs.py  —  解码 x-s / x-s-common / b1
import base64, json, time
from Crypto.Cipher import ARC4
from xhshow import Xhshow

CUSTOM = "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5"
STD    = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
T = str.maketrans(CUSTOM, STD)

def b64d(s):            return base64.b64decode(s.translate(T))
def decode_x_s(s):      return json.loads(b64d(s[4:]))          # 去掉 "XYS_"
def decode_x_s_common(s): return json.loads(b64d(s))
def decode_b1(b1):
    raw = b64d(b1).decode("utf-8").encode("latin1")             # 还原 ARC4 密文
    return json.loads(ARC4.new(b"xhswebmplfbt").decrypt(raw).decode())

COOKIE = "a1=<你的a1>; web_session=<你的web_session>"
h = Xhshow().sign_headers_post(
    uri="/api/sns/web/v1/search/notes",
    cookies=COOKIE,
    payload={"keyword": "编程副业", "page": 1, "page_size": 20,
             "search_id": "8638957120882984752", "sort": "general", "note_type": 0},
)
print("x-s        :", json.dumps(decode_x_s(h["x-s"]), ensure_ascii=False)[:300])
sc = decode_x_s_common(h["x-s-common"])
print("x-s-common :", json.dumps({k: v for k, v in sc.items() if k != "x8"}, ensure_ascii=False))
print("b1         :", json.dumps(decode_b1(sc["x8"]), ensure_ascii=False)[:600])
print("headers    :", sorted(h.keys()))
```

预期输出（本次实测）：

```text
x-s        : {"x0": "4.3.5", "x1": "xhs-pc-web", "x2": "Windows", ...}
x-s-common : {"s0": 5, "s1": "", "x0": "1", "x1": "4.3.5", "x2": "Windows", "x3": "xhs-pc-web", "x4": "4.86.0", ...}
b1         : {"x33": "0", "x34": "0", "x35": "0", "x36": "8", "x37": "0|0|...", "x38": "0|1|...",
              "x39": 0, "x42": "3.4.4", "x43": "742cc32c", "x44": "1792377640073",
              "x45": "__SEC_CAV__1-1-1-1-1|__SEC_WSA__|", "x46": "false", ...}
headers    : ['x-b3-traceid', 'x-mns', 'x-s', 'x-s-common', 'x-t', 'x-xray-traceid', 'xy-direction']
```

## 附录 B：抓真实基线（把 ❓ 变成 ✅）

1. Chrome 登录小红书 → 搜索一个关键词 → F12 Network → 过滤 `XHR` → 选 `/api/sns/web/v1/search/notes` → 右键 *Copy as cURL (bash)*。
2. 把这段 curl 粘到一个文本文件，与 `core.py:391-410` 的头做集合/顺序对比（附录 A 的解码脚本可以直接解出 `x-s`/`x-s-common`）。
3. 重点看四件事：
   - `x-s.x2`（平台）与 `user-agent`、`sec-ch-ua-platform` 是否三方一致；
   - `x-s-common` 的 `b1` 两次请求之间哪些字段变了（真实浏览器应几乎不变）；
   - 头集合与顺序（尤其 `x-rap-param`、`x-xray-traceid`、`x-mns`、`pragma`）；
   - `:authority` 下是 h2 还是 h1.1、`accept-encoding` 的值。
4. 注意：**不要**把抓包命令直接贴给爬虫跑 —— 里面的 `x-s` 和 `x-t` 是一次性的，且与页面 load 状态绑定。

## 附录 C：与重构前实现的对比（为什么这是一次"反检测上的退化"）

git `b5dab6d` 的 diff 显示，`secsign.py` 被删除，替换为 `xhshow`。旧实现：

```python
s = await page.evaluate("(c, d) => window.mnsv2(c, d)", [c, d])   # 在真实页面里算签名
f = {"x0": "4.2.6", "x1": "xhs-pc-web", "x2": "Mac OS", "x3": s, "x4": a}
b1 = (await page.evaluate("() => window.localStorage")).get("b1", "")   # 真实设备指纹
```

新实现：纯 Python、合成环境、平台写死 Windows、`b1` 自造、不使用 session 状态。
**旧方案唯一的问题是"慢"和"依赖页面存活"，但它的签名环境是真的。** 如果要在"性能"和"真实性"之间取一个中间点，最划算的是：**保留 httpx 的吞吐，但把签名材料（UA/CH/b1/平台/timezone）全部从浏览器取，并维护会话序列状态**；再往前走一步就是 F8 的"请求回浏览器"。

## 附录 D：顺带发现的非风控问题

- `core.py:166-178`：`has_more` 判断在取 `items` 之前，最后一页（以及结果不足 20 条时的唯一一页）数据会被整体丢弃。这是**静默的数据丢失**，和"软封禁"混在一起看会互相掩盖。
- `README.md:60` 仍写"无需 JS 逆向……通过 JS 表达式获取签名参数"，与当前纯算法签名实现不一致，会让后续实验者误判架构。
- `api/main.py:205`：`uvicorn.run(app, host="0.0.0.0", port=8080)` —— 研究机器上如果网络环境不可信，建议改回 `127.0.0.1`。
- `login.py:213-224`：只写 `web_session` 不写 `a1`，cookie 登录路径在全新 profile 下必然 `ValueError`（见 F14）。

---

## 边界说明

- 本评估没有对小红书发起任何请求，所有结论来自源码 + 本地算法复现；带 ❓ 的项必须用自己账号的抓包基线确认。
- 建议实验强度保持"单账号、低频、短时段"，`CRAWLER_MAX_SLEEP_SEC` 的实验不要向"更快"方向做 —— 本报告的目的是让请求更**真实**，不是更**高频**。
- 若后续要做 A/B，务必固定样本量并记录 §6 指标，否则"感觉好多了"没有意义。
