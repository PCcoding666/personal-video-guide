# Personal Video Guide v2 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付私人视频导游第一版：本地工具包 `video-guide`（CLI + stdio MCP server）加通用 Skill。在 WorkBuddy 上跑通每日导览和陪看，在第二个宿主上跑通按需陪看。

**Architecture:** 见[设计 v2](../specs/2026-10-31-personal-video-guide-design.md)。工具包承担确定性工作：发现、字幕、大纲、读取、检索、链接、本地状态。宿主负责模型推理、定时、通知和记忆。Skill 写方法、模板和证据规则。

**Tech Stack:** Python 3.10+，复用现有依赖 `httpx`、`pydantic`、`typer`；MCP server 用官方 `mcp` Python SDK，放在可选 extra `guide`。测试用 pytest 和 `httpx.MockTransport`，全部离线。

**说明：** v1 计划（`e912aae`）已被本计划取代。Task 1 的实测结果可能改变 Task 4 的实现方式和 Task 7 的接入方式；结果出来后先更新本计划，再继续。

## Global Constraints

- 遵守 AGENTS.md 中关于 personal video guide 的条款：
  - 代码放在 `src/video_guide/`，不依赖核心 runtime 和 providers，不调用模型。
  - 只对白名单 HTTPS 地址发 GET 请求，不下载媒体，不内置反爬绕过，不做调度和通知；唯一例外是调用用户自己安装的 `yt-dlp` 只取字幕（见 Task 4）。
  - 本地状态默认放在 `~/.video-guide/`，可用环境变量 `VIDSNAP_GUIDE_HOME` 改写；不写进仓库。
- 不改现有 Harness 的 public contracts、CLI、recipe、RunBundle、run index 和 conformance 行为。
- 测试离线且确定：网络通过 `httpx.MockTransport` 注入，时钟通过参数注入。测试数据在测试代码里手写，不提交真实字幕、媒体或用户状态。
- `src/` 下的代码不能出现 `oauth`、`jwt`、`smtp`、`redis` 等字样（`tests/test_no_saas_dependencies.py` 会检查）。
- 每个任务先写失败测试，再写实现。每个任务结束时运行 `python -m pytest -q tests`；Task 9 跑全部公开 gates。
- 只暂存本任务的文件，禁止 `git add .`；工作区里已有的评测改动不混入提交。

## 文件与职责

| 路径 | 职责 |
| --- | --- |
| `src/video_guide/__init__.py` | 包说明 |
| `src/video_guide/state.py` | 状态目录、`profile.json`、`sources.json`、`seen.jsonl`、`feedback.jsonl`、原子写 |
| `src/video_guide/models.py` | Pydantic 模型：Profile、Source、VideoItem、Sentence、Transcript、Window、Hit、FeedbackKind |
| `src/video_guide/fetch.py` | 白名单 HTTPS GET；可注入 transport；超时与大小上限 |
| `src/video_guide/sources.py` | 规范化 YouTube 频道链接和播客 RSS 链接 |
| `src/video_guide/feeds.py` | 解析 YouTube Atom 和播客 RSS；去重；48 小时重叠窗口；每个来源单独报告失败 |
| `src/video_guide/captions.py` | 字幕获取：YouTube 字幕轨、`podcast:transcript`；解析 VTT、SRT 和 JSON3；缓存 |
| `src/video_guide/transcript.py` | 章节解析、时间窗大纲、区间读取（字符上限） |
| `src/video_guide/search.py` | 时间窗上的 BM25 词法检索 |
| `src/video_guide/links.py` | 生成跳转链接（提前 5 秒）；只接受 http(s) |
| `src/video_guide/service.py` | 组合以上模块，作为 CLI 和 MCP 的共同入口（工具名与设计第 4 节一致） |
| `src/video_guide/cli.py` | `video-guide` typer 应用 |
| `src/video_guide/mcp_server.py` | stdio MCP server，把 service 方法注册为工具 |
| `skills/personal-video-guide/` | `SKILL.md` 与 references：onboarding、daily-digest、watch-along、evidence |
| `docs/personal-video-guide-compatibility.md` | 宿主能力表与实测记录（非敏感） |
| `tests/` | 各模块离线测试 |

## Task 1：宿主与字幕获取实测（WorkBuddy 已完成，2026-11-01）

**Files:** `docs/personal-video-guide-compatibility.md`（能力表与结论）。

- [x] 摸清 WorkBuddy 5.7.6 的 Skill 目录、MCP 配置（`~/.workbuddy-ai/mcp.json`）、定时任务和记忆文件。
- [x] 实测字幕：宿主借本机 `yt-dlp` 加 Chrome cookie 拿到全片自动字幕，用时约 10 分钟；它返回的链接里有一个与标注时间差 37 秒，另一个带了乱码。
- [x] 实测本地 MCP：stdio server 写入配置、重启、点一次 Trust 后，聊天和定时任务都能调用。
- [x] 实测定时：宿主能在聊天中自行创建定时任务，到点准时运行并调用 MCP 工具。应用没开时任务会错过，下次打开补跑一次。
- [x] 本机取字幕：RSS 6/6 成功；不带 cookie 时字幕 0/10，`youtube-transcript-api` 和 `yt-dlp` 都被 YouTube 拦截。
- [x] 测试用的定时任务和 MCP 配置已删除。
- [ ] 未测：长期记忆、浏览器页面上下文、微信等应用外推送、第二个宿主。放到 Task 8 一并验证。

结论影响后续任务：字幕获取必须依赖用户自己提供的 cookie（见 Task 4 的待决定事项）；跳转链接必须由工具生成；WorkBuddy 接入方式已确定（见 Task 7）。

## Task 2：状态存储与模型

**Files:** `models.py`、`state.py`、`tests/test_state.py`。

- [ ] 先写失败测试：
  - 首次使用时目录不存在，自动创建；
  - `VIDSNAP_GUIDE_HOME` 改写目录；
  - profile 读写往返；
  - `seen.jsonl` 追加后 `is_seen` 为真；
  - 写入中途异常时，旧文件保持完整（原子写）；
  - 未知 `schema_version` 拒绝读取；
  - 反馈种类只接受 `interested`、`not_interested`、`watched`、`correct`。
- [ ] 实现。每个文件都带 `schema_version: 1`；JSONL 每行一条记录。
- [ ] 测试通过后提交：`feat(guide): add local state store`。

## Task 3：来源与发现

**Files:** `fetch.py`、`sources.py`、`feeds.py`、`tests/test_feeds.py`。

- [ ] 先写失败测试，样例 XML 在测试里手写：
  - `@handle`、`/channel/UC…` 和 RSS 链接都能规范化；解析不了的返回明确错误；
  - YouTube Atom 和播客 RSS 解析出视频 ID、标题、发布时间、简介和链接；
  - 已推荐的视频不再返回；
  - 从上次成功检查时间往前重叠 48 小时；
  - 一个来源失败不影响其他来源，失败的来源不推进检查时间；
  - 白名单外的主机、非 https 链接、超过大小上限的响应都被拒绝。
- [ ] 实现：`fetch.py` 只暴露 `get(url) -> bytes`，白名单为 YouTube 的 feed 和字幕主机，加上用户添加的 feed 主机。
- [ ] 测试通过后提交：`feat(guide): discover new videos from feeds`。

## Task 4：字幕获取与解析

**Files:** `captions.py`、`models.py`（Profile 新增字幕设置）、`tests/test_captions.py`。

**已决定：方案 A（2026-11-01 用户决定）。** YouTube 字幕通过调用用户自己安装的 `yt-dlp` 获取，只取字幕；播客的 `podcast:transcript` 仍走 `fetch.py` 的白名单 HTTPS GET。边界写在 AGENTS.md 和设计第 5 节。

**Interfaces:**

- `Profile` 新增两个可选字段：`ytdlp_path: str | None = None`（缺省时从 `PATH` 查找）、`cookies_from_browser: str | None = None`（缺省不带 cookie；只接受 `chrome`、`firefox`、`safari`、`edge`、`brave`）。两个字段都有默认值，旧的 `profile.json` 照常可读，`schema_version` 保持 1。
- `captions.py` 通过注入的 `run(argv, timeout) -> CompletedRun` 调用子进程，测试用假的 runner 替代，不真正执行 `yt-dlp`。
- 参数只来自白名单：`--skip-download --write-subs --write-auto-subs --sub-langs <langs> --sub-format json3/vtt --no-playlist -o <临时目录模板>`，开启 cookie 时追加 `--cookies-from-browser <browser>`。视频 URL 只接受规范化后的 `https://www.youtube.com/watch?v=<id>`。

步骤：

- [x] 先写失败测试：
  - VTT、SRT 和 JSON3 解析成带起止秒数的句子；
  - 自动字幕优先选 `en-orig` 原始轨，不选机器翻译的 `en`；人工字幕优先于自动字幕；
  - 字幕类型和语言如实标注；
  - 生成的 argv 只含白名单参数，绝不含下载媒体的参数；只有 profile 开启时才出现 `--cookies-from-browser`；
  - 找不到 `yt-dlp` 返回 `ytdlp_missing`；bot 检查返回 `bot_check`（未开启 cookie 时附带 `no_cookies` 提示）；没有字幕轨返回 `no_captions`；超时返回 `timeout`；都不抛出未处理异常；
  - `yt-dlp` 的原始 stderr 不进入返回值；临时目录在成功和失败后都被删除；
  - 缓存命中时不再调用 runner；
  - 字幕文本里的指令性内容原样作为数据返回。
- [x] 实现 `captions.py` 和 `podcast:transcript` 路径；更新 AGENTS.md 的 Layout 段。播客转录的 CLI 入口还没接上：RSS 会记下 `podcast:transcript` URL，`load_podcast_transcript` 能拉取并解析，`video-guide transcript get` 目前只接受 YouTube URL。后续补充：profile 的 `cookies_file`；字幕 HTTP 429 为 `rate_limited` 并退避重试；`curl_cffi` extra；YouTube feed 404/5xx 重试。2026-11-05 实机后又收窄字幕请求：先 `--list-subs`，再只下原语言轨加至多一种 `preferred_caption_languages`；argv 增加 `--ignore-no-formats-error`；`yt-dlp` 还会在解释器同目录查找；浏览器 cookie 数据库不可读时返回 `cookies_unreadable`。
- [x] 离线 gates（`ruff format --check`、`ruff check`、`mypy src`、`pytest`、`git diff --check`）通过。
- [ ] 用本机 `yt-dlp` 加 Chrome cookie 手动取一期字幕核对一次（不进 pytest，不提交字幕内容）。这次没有在带已登录浏览器的机器上重跑。
- [x] 测试通过后提交：`feat: fetch and parse timed captions via user-installed yt-dlp`。

## Task 5：大纲、读取、检索与链接

**Files:** `transcript.py`、`search.py`、`links.py`、`tests/test_transcript.py`、`tests/test_search.py`。

- [x] 先写失败测试：
  - 从简介解析章节（`00:00 Intro` 这类格式）；没有章节时按 5 分钟切窗；
  - `read` 按区间返回句子，超过 8,000 字符时截断并标注；
  - BM25 对多个查询短语合并打分，排序稳定；
  - 无命中返回空列表和覆盖范围；
  - `link.at` 提前 5 秒、不小于 0，生成 `youtube.com/watch?v=…&t=…s`；非 http(s) 链接拒绝。
- [x] 实现。检索只用标准库，不引入新依赖。
- [x] 测试通过后提交：`feat(guide): outline, read, search and link transcripts`。

观看循环的 CLI（`transcript get` / `read` / `outline`、`search`、`link`、`profile`）随上面两项一起落地，走 `service.py`。Task 6 里 MCP server 仍未做。

## Task 6：服务层、CLI 与 MCP server

观看循环用到的服务层和 CLI 已在 Task 4/5 完成。下面未勾的项仍然待做，主要是 MCP server、`guide` extra，以及和 `digest.record` 连起来的回归。

**Files:** `service.py`、`cli.py`、`mcp_server.py`、`pyproject.toml`、`tests/test_cli.py`、`tests/test_mcp.py`。

- [x] 先写失败测试：
  - CLI 子命令与设计第 4 节的工具一一对应，输出 JSON（观看循环部分已在 Task 4/5 落地）；
  - MCP server 注册的工具名和参数与 CLI 一致（`transcript_get`、`transcript_outline`、`transcript_read`、`search`、`link`、`profile_get`、`profile_set`，外加 `open_guide`）；
  - 未安装 `mcp` 时，CLI 照常可用，启动 MCP server 给出安装提示。
- [ ] `digest.record` 之后，`feeds.check` 不再返回这些视频。这一项还没做。
- [x] `pyproject.toml` 的可选 extra 名是 `mcp`（设计里写过 `guide`，按当前仓库的 extra 名安装）。`dev` extra 已加入 `mcp`。`video-guide` 入口本来就有。
- [x] 测试通过后提交 MCP server。本仓库没有 `vidsnap` CLI。

## Task 7：Skill 与宿主接入

**Files:** `skills/personal-video-guide/SKILL.md`、`references/{onboarding,daily-digest,watch-along,evidence}.md`、`skills/personal-video-guide/hosts/workbuddy.md`、`tests/test_skill_package.py`。

- [x] 先写失败测试：SKILL.md 有合法 frontmatter（name、description）；引用的 references 文件都存在；提到的工具名都在 MCP server 上存在。
- [x] 写 SKILL.md：触发条件、模式选择（每日导览或陪看）、工具调用顺序、卡片模板和证据规则。卡片中的链接只能用 `link` 生成，不得自行拼接。正文保持精简，细节放进 references。Codex 插件清单（`.codex-plugin/plugin.json`、`.mcp.json`、`.agents/plugins/marketplace.json`，以及现行 schema 的根 `plugin.json` / `mcp.json`）和本地陪看页 `open_guide` 一并落地。
- [x] 写 WorkBuddy 接入说明，依据 Task 1 的实测结果：
  - Skill 复制到 `~/.workbuddy/skills/personal-video-guide/`；
  - MCP 写入 `~/.workbuddy-ai/mcp.json`（`command` + `args`），重启 WorkBuddy，在 Connectors → Custom connectors 里点一次 Trust；
  - 首次对齐结束时，由宿主在对话中创建定时任务，并把任务名和时间回报给用户；
  - 说明 WorkBuddy 必须在推送时间处于运行状态，否则下次打开时补推一次；
  - 暂停和取消都在 Scheduled 页面操作。
- [x] 测试通过后提交：MCP server、Skill、Codex 插件和本地陪看页在同一分支。

## Task 8：真实验收

**Files:** 更新 `docs/personal-video-guide-compatibility.md`。原始输入输出保存在仓库外。

- [ ] 在 WorkBuddy 完成首次对齐，选 3–5 个栏目，创建定时任务，连续运行 7 天。
- [ ] 按设计第 12 节检查：
  - 每天按时推送，不重复推送；
  - 抽查 10 个片段的链接和原话；
  - 10 个中文模糊问题中至少 7 个找到正确段落。
- [ ] 补测 Task 1 未覆盖的项：WorkBuddy 的长期记忆、浏览器页面上下文、微信等应用外推送。
- [ ] 在第二个宿主跑通按需陪看。
- [ ] 只把结论、版本、日期和限制写入兼容性文档。验收结束后，按用户意愿保留或取消定时任务。

## Task 9：发布检查

**Files:** `docs/README.md`、`README.md`（可选入口）。

- [ ] 运行全部公开 gates：

```bash
ruff format --check src tests scripts
ruff check .
mypy src
python -m pytest -q
python -m build
vidsnap conformance
python scripts/secret_scan.py
git diff --check
```

- [ ] 确认 wheel 里包含 `vidsnap/guide`，`video-guide --help` 可用，现有 `vidsnap` CLI 输出没有变化。
- [ ] 更新文档入口，写明支持的宿主和尚未验证的宿主。公开发布需另行获得用户授权。

## 已知基线问题

2026-10-31 的基线检查中，`ruff check .`、`mypy src` 和全量 pytest 已有失败，与本功能无关；其中一个 pytest 失败来自本地未跟踪的 `frontend/` 目录。实施时单独记录这些问题，不顺手修复，也不改 gates。
