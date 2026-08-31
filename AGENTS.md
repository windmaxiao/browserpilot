# BrowserPilot — Agentic RPA Framework

> 本文件面向 AI 编程助手，描述项目的完整架构、模块职责、数据流和未来规划。
> 修改代码前请先阅读本文件以及对应当前版本的 `待解决问题.md`。

## 一、项目定位

基于 Playwright 的自主网页操作 Agent 框架。让大模型像人一样操作浏览器 — 理解网页、规划操作、执行任务。

**技术栈：** Python >= 3.10, Playwright (async), pytest

**核心原则：**
1. **LLM 不知道 Playwright** — LLM 输出高层级 Action，Playwright 只是执行器
2. **Browser Tool 与 Snapshot 分离** — Snapshot 描述页面，Tool 执行操作
3. **高内聚、低耦合** — 便于未来替换 Selenium、Appium 等执行器

---

## 二、目录结构

```
browserpilot/
├── AGENTS.md                          # ← 本文件，AI 编程助手手册
├── 待解决问题.md                       # 已知问题追踪（仅剩未解决项，不入 git）
├── 使用文档.md                         # 面向使用者的安装/运行/规则引擎指南
├── Agentic_RPA_项目规划_V0.1.md        # 原始项目规划文档
├── V0.5开发计划.md                     # V0.5 开发计划（✅ 已完成）
├── V1.0开发计划（新）.md               # V1.0 开发计划（iframe 能力已完成，其余规划中）
├── V1.1开发计划.md                     # V1.1 开发计划（稳定化与生产可观测，📋 待开始）
├── README.md                          # 项目入口 README
├── LICENSE                            # Apache License 2.0
├── .gitignore
│
└── agent-rpa/                         # 核心代码
    ├── pyproject.toml                 # Python 包配置
    ├── README.md                      # agent-rpa 模块说明
    │
    ├── agent/
    │   ├── __init__.py
    │   ├── logging.py                  #   setup_logging（控制台 + logs/ 按天滚动文件）
    │   │
    │   ├── schema/                    # 【数据模型层】— 无外部依赖，纯 dataclass
    │   │   ├── __init__.py
    │   │   ├── action.py              #   Action 数据模型 + 工厂函数 + 参数校验
    │   │   ├── observation.py         #   Observation 数据模型
    │   │   └── snapshot.py            #   Snapshot + ElementInfo 数据模型
    │   │
    │   ├── browser/                   # 【浏览器执行层】— Playwright 封装
    │   │   ├── __init__.py
    │   │   ├── playwright.py          #   BrowserTool + BrowserManager（新标签页跟随）
    │   │   └── snapshot.py            #   SnapshotGenerator（页面→Snapshot，关闭防御）
    │   │
    │   ├── core/                      # 【Agent 循环核心】— 编排层
    │   │   ├── __init__.py
    │   │   ├── agent.py               #   Agent 主循环（步骤/自由双模式 + 异常防护）
    │   │   ├── executor.py            #   Action → BrowserTool 翻译层
    │   │   ├── memory.py              #   HistoryMemory 增量式记忆（V0.5：摘要+窗口+压缩）
    │   │   ├── observer.py            #   SnapshotGenerator 的 Agent 包装
    │   │   └── planner.py             #   Planner 基类 + RuleBasedPlanner（8 条规则）
    │   │                               #   + LLMPlanner + TaskPlanner（两阶段）+ TaskStep/TaskQueue
    │   │
    │   ├── llm/                       # 【LLM 客户端层】（V0.3）
    │   │   ├── __init__.py
    │   │   ├── base.py                #   LLMClient 协议 + 错误分类
    │   │   ├── mock.py                #   确定性 Mock 客户端（无网络测试）
    │   │   └── openai_client.py       #   OpenAI 兼容 Provider 适配器
    │   ├── prompts/                   # 【提示词层】（V0.3）
    │   │   ├── __init__.py
    │   │   └── planner.py             #   系统/用户提示词 + Snapshot 序列化 + Action 解析
    │   └── tools/                     # 【预留】辅助工具
    │       └── __init__.py
    │
    ├── examples/
    │   ├── manual_demo.py             # 手动模式 Demo（直接调用工具）
    │   ├── agent_demo.py              # 规则 Agent 模式 Demo（本地搜索页）
    │   ├── baidu_demo.py              # 规则 Agent 模式 Demo（真实百度）
    │   ├── llm_agent_demo.py          # LLM Agent 自由模式 Demo（本地搜索页）
    │   ├── llm_baidu_demo.py          # LLM Agent 两阶段 Demo（真实百度）
    │   ├── check_llm_connectivity.py  # 7 家国内大模型预设连通性测试
    │   ├── search_page.html           # 本地确定性搜索页
    │   └── ex_robot/ydgx/             # 业务示例（LLM 局部辅助 + 多层 iframe，不入 git）
    │
    └── tests/                         # 16 个文件，300+ 个用例
        ├── __init__.py
        ├── test_action.py             # Action Schema + 参数校验
        ├── test_observation.py        # Observation Schema
        ├── test_snapshot.py           # Snapshot Schema + Generator 基础
        ├── test_executor.py           # Executor 调度 + target_id 定位
        ├── test_planner.py            # RuleBasedPlanner 规则引擎（8 条规则）
        ├── test_browser_tool.py       # BrowserTool（click 指纹/wait/scroll 防护）
        ├── test_snapshot_generator.py # SnapshotGenerator（selector 转义/ID 生命周期）
        ├── test_snapshot_frame.py     # SnapshotGenerator 多层 iframe 递归/消歧（V1.0 子计划 A）
        ├── test_regression_fixed_issues.py  # 已修复问题回归
        ├── test_agent_integration.py  # Agent 主循环 Mock 集成（含 LLM 驱动/步骤模式）
        ├── test_llm_client.py         # LLMClient 协议 / Mock / 错误分类
        ├── test_prompt_serialization.py      # Snapshot 序列化 / URL 脱敏 / 历史窗口
        ├── test_llm_planner.py        # LLMPlanner 解析 / 一次修复 / 完整链路
        ├── test_memory.py             # Memory 摘要/折叠/上下文压缩（V0.5）
        ├── test_logging.py            # setup_logging 控制台 / 文件 sink
        └── test_task_queue.py         # TaskStep/TaskQueue/拆解解析/TaskPlanner
```

---

## 三、架构与数据流

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                     Agent Loop                          │
│                                                         │
│  ┌────────┐   ┌──────────┐   ┌──────────┐   ┌──────┐  │
│  │ Observe │──→│  Plan    │──→│ Execute  │──→│Record│  │
│  └────┬───┘   └──────────┘   └────┬─────┘   └──────┘  │
│       │                           │                     │
└───────┼───────────────────────────┼─────────────────────┘
        │                           │
        ▼                           ▼
  ┌────────────┐             ┌──────────────┐
  │  Observer  │             │   Executor   │
  │  (agent/   │             │  (agent/     │
  │   core/    │             │   core/      │
  │   observer │             │   executor)  │
  │   .py)     │             │              │
  └─────┬──────┘             └──────┬───────┘
        │                           │
  ┌─────┴──────┐             ┌──────┴───────┐
  │ SnapshotGen│             │  BrowserTool │
  │ (browser/  │             │  (browser/   │
  │  snapshot) │             │  playwright) │
  └─────┬──────┘             └──────┬───────┘
        │                           │
        └──────────┬────────────────┘
                   ▼
          ┌────────────────┐
          │  Playwright    │
          │  (page对象)     │
          └────────────────┘
```

### 3.2 数据流

```
User Goal (string)
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  Agent.run()                                        │
│                                                     │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐  │
│  │ Observer │      │ Planner  │      │ Executor │  │
│  │ .observe │      │ .plan    │      │ .execute │  │
│  │          │      │          │      │          │  │
│  │  Page ──→│Snapshot ──→│Action ──→│Observation│  │
│  └──────────┘      └──────────┘      └──────────┘  │
│                                                     │
│  1. Observer 通过 SnapshotGenerator 把 DOM 转成     │
│     结构化 Snapshot                                 │
│  2. Planner 分析 Snapshot 输出下一步 Action          │
│  3. Executor 将 Action 翻译为 Playwright 调用       │
│  4. Observation 返回结果供下一轮决策                 │
└─────────────────────────────────────────────────────┘
```

### 3.3 类型依赖关系

```
Action ────→ Executor ────→ BrowserTool ────→ Playwright Page
  │                            │
  └──── 验证/工厂函数           └──── 返回 Observation
                                     │
SnapshotGenerator ────→ Observer ────┘
  │                        │
  └──── 生成 Snapshot       └──── 供 Planner 使用
```

---

## 四、模块详解

### 4.1 Schema 层 (`agent/schema/`)

#### `action.py` — Action 数据模型

```python
@dataclass
class Action:
    action: ActionType      # 12种之一: click/input/select/goto/scroll/wait/
                            #         download/upload/back/refresh/screenshot/done
    target: Optional[str]   # 语义化目标描述，例如"登录按钮"、"搜索框"
    value: Optional[str]    # 参数值，例如 input 的文本，goto 的 URL
    params: dict            # 额外参数: timeout, index, selector, force...
```

- **12 种原子动作类型**：click, input, select, goto, scroll, wait, download, upload, back, refresh, screenshot, done
- **工厂函数**：`click()`, `input_text()`, `goto()`, `select()`, `scroll()`, `wait()`, `done()`
- **验证**：`validate()` 返回错误列表，`is_valid()` 快捷判断

#### `observation.py` — Observation 数据模型

```python
@dataclass
class Observation:
    success: bool           # 执行是否成功
    url: Optional[str]      # 执行后的页面 URL
    title: Optional[str]    # 执行后的页面标题
    page_changed: bool      # 页面是否发生变化（对比执行前后 URL）
    error: Optional[str]    # 错误信息
    data: dict              # 附加数据（下载路径、截图 base64 等）
```

- 工厂方法：`Observation.ok()` / `Observation.fail()`
- 快捷属性：`.is_error`, `.has_error`

#### `snapshot.py` — Snapshot + ElementInfo 数据模型

```python
@dataclass
class ElementInfo:
    text: str               # 可见文本（最长 200 字符）
    element_id: str         # 全局唯一元素 ID（e0, e1, ...，单次 Snapshot 内有效）
    tag: str                # HTML 标签名
    element_type: str       # 语义类型: button/link/textbox/dropdown/text/image
    selector: str           # Playwright 选择器（供内部执行使用）
    bbox: Optional[dict]    # bounding box（V0.2+启用）
    aria_label: str         # aria-label 属性
    placeholder: str        # input placeholder
    attributes: dict        # 其他重要属性
    index: int              # 同类元素中的序号
    frame_path: tuple[str, ...]  # iframe 定位路径（V1.0 子计划 A，空元组=主页面）

@dataclass
class Snapshot:
    title: str              # 页面标题
    url: str                # 页面 URL
    inputs: list[ElementInfo]
    buttons: list[ElementInfo]
    links: list[ElementInfo]
    texts: list[ElementInfo]
    selects: list[ElementInfo]
    dialogs: list[dict]     # 弹窗信息（预留）
    loading: bool           # 页面加载状态
    page_type: str          # 页面类型: login/search/table/form/detail/unknown
```

- 快捷方法：`is_empty()`, `get_interactive_elements()`, `find(text)`, `summary()`

---

### 4.2 Browser 层 (`agent/browser/`)

#### `playwright.py` — BrowserTool + BrowserManager

**BrowserTool** — Playwright 操作封装，每个方法返回 `Observation`：

| 方法 | 参数 | 说明 |
|------|------|------|
| `goto(url, timeout)` | str, int | 导航到 URL |
| `click(selector, timeout, force)` | str, int, bool | 点击元素（对比 URL 判断 page_changed） |
| `input(selector, text, timeout, clear_first)` | str, str, int, bool | 输入文本 |
| `select(selector, value, timeout)` | str, str, int | 下拉选择（对比 URL 判断 page_changed） |
| `scroll(direction, amount)` | str, int | 滚动（down/up/bottom/top） |
| `wait(ms)` | int | 等待指定毫秒数 |
| `download(selector, save_path, download_dir, timeout)` | str, str/Path, str/Path, int | 下载文件（`download_dir` 目录语义优先于 `save_path`，目录与 `suggested_filename` 拼接；Executor 层恒传 `save_path=None`） |
| `upload(selector, file_path, timeout)` | str, str/Path, int | 上传文件 |
| `back()` | — | 浏览器后退 |
| `refresh()` | — | 刷新页面 |
| `screenshot(full_page)` | bool | 截图返回 base64 |

> V1.0 子计划 A：`click/input/select/download/upload` 五个元素定位方法均新增 `frame_path` 参数（元组，空为主页面，默认 `()`），按 iframe 定位段解析最终 Locator（见下方「iframe 定位」）。

**BrowserManager** — 浏览器生命周期管理：

| 方法 | 说明 |
|------|------|
| `start()` | 启动浏览器（默认 1280×720, zh-CN） |
| `stop()` | 关闭浏览器及所有资源 |
| `page` | 获取当前 Page 对象 |
| `create_tool()` | 创建 BrowserTool 实例 |

**iframe 定位（V1.0 子计划 A）：** BrowserTool 内部 `_locator(selector, frame_path)` 按 `(selector, frame_path)` 解析最终 Locator——空 frame_path 走 `page.locator(selector)`（主页面，向后兼容）；非空则逐层 `frame_locator(seg)` 穿透 iframe 再定位目标元素。

#### `snapshot.py` — SnapshotGenerator

从 Playwright Page 提取语义信息生成 Snapshot。

| 方法 | 说明 |
|------|------|
| `generate()` | 生成完整 Snapshot（递归 iframe，逐 scope 批量提取） |
| `detect_page_type()` | 通过 URL/Title 推断页面类型 |
| `_iter_scopes()` / `_walk_scopes()` | 递归遍历 frame 树（主页面 + 嵌套 iframe），每个子 frame 做有限超时加载等待（`asyncio.wait_for` 兜底），超时跳过该帧 |
| `_frame_segments()` | 为同一父 document 内 iframe 生成唯一定位段（id → name → 位置 `nth=j`）；重复 id/name 一律改用位置索引消歧 |
| `_extract_scope()` | 单 scope 提取：真实 Frame/Page 一次 `frame.evaluate` 返回 6 类原始数据（含可点击文本）；Mock 回退逐元素 |
| `_extract_buttons()` | button, [role=button], input[submit], a[class*=btn], [class*=button] |
| `_extract_inputs()` | input(非hidden), textarea, contenteditable, [role=textbox] |
| `_extract_links()` | a[href] |
| `_extract_texts()` | h1-h6, p, span, label, li, td, th, strong, em |
| `_extract_selects()` | select |
| `_build_selector()` / `_build_selector_from()` | 生成选择器：data-testid > id > role > aria-label > 标签+文本 > 标签名（ID/属性值经 CSS 转义）；后者为批量路径纯函数版 |

批量 JS 提取（`_EXTRACT_JS` + `_build_infos` / `_from_raw`）：把逐元素约 15 次 CDP 调用合并为每帧 1 次 `evaluate`，解决大页面 Snapshot 生成 500s+ 的问题（详见 [待解决问题.md](待解决问题.md) #9）。

---

### 4.3 Core 层 (`agent/core/`)

#### `agent.py` — Agent 主循环

```python
class Agent:
    def __init__(self, observer, planner, executor, max_steps=50)
```

| 方法 | 说明 |
|------|------|
| `run(goal, timeout_seconds)` | 完整 Agent 循环：先尝试 `planner.decompose()`，成功走步骤模式，否则走自由模式；`timeout_seconds` 为可选总执行超时（墙钟，兜底 max_steps） |
| `step(action)` | 单步执行（手动/调试模式） |
| `observe()` | 获取当前页面 Snapshot |

**run() 两种模式（V0.4 前瞻 + V1.0 批量增强）：**
- **步骤模式**（Planner.decompose 返回步骤队列）：按 `TaskQueue` 逐项执行；`wait` 步骤由框架直接 `wait(ms)`、`verify` 步骤由框架校验 URL/文本，**均不经过 LLM**；`action` 步骤经 `plan_step()` 携带「当前步骤 + 剩余步骤」上下文分步决策；队列耗尽即任务完成（无需 LLM 输出 done）
- **自由模式**（拆解失败 / Planner 不支持）：Observe→Plan→Execute→Record 循环直到 LLM 输出 done；**V1.0 批量增强**：每轮只 observe 一次、经 `plan_batch()` 一次 LLM 返回最多 10 个动作（`BATCH_SYSTEM_PROMPT` / `build_hybrid_schema` / `parse_action_list`），多个动作在同一 Snapshot 上连续执行，任一失败、页面变化或遇到 done 即结束本批回到主循环重新观察（表单填写从 N 次 LLM 降为 1 次）

**关键设计决策：**
- Step 索引从 1 开始
- **失败自动重试（V0.4）**：机械重试 1 次 → Reflection（LLM 分析失败给出替代动作）1 次 → 仍失败尝试页面恢复（back/refresh）→ 仍失败中止任务；机械重试**不替换 Snapshot**——iframe 目标经 `_pinned_retry_action()` 以原始 Snapshot 中该元素的显式 selector 固定定位，避免重新 Observe 后 element_id 重编号导致旧 target_id 命中错位元素（2026-08-27 修复）
- Planner 返回 None 表示无法规划
- **异常防护**：`_safe_observe()` / `_safe_execute()` 捕获浏览器关闭等异常，优雅返回失败 Observation 而非崩溃
- **停滞检测**：自由模式连续 2 次 wait 且页面无变化 → 提前终止；仅对 `Planner.stagnation_detection=True`（默认）的规划器生效——`RuleBasedPlanner` 覆盖为 `False`（自身用 `_WAIT_MAX_TRIES` 控制等待次数，不被截断）；计划内 wait 步骤不计入（步骤模式不做该检测，队列必然推进）
- **重复动作检测（V1.0 批量增强）**：页面未变化时连续对同一目标执行相同动作（input/click/select/scroll），连续 `_MAX_REPEAT_SKIPS=3` 次即判停滞终止，防止 LLM 反复填同一字段
- **iframe 定位与恢复（V1.0 子计划 A）**：`ElementInfo` 携带 `frame_path`（元组，空为主页面）；Executor 维护 `element_id → (selector, frame_path)` 映射并向 BrowserTool 透传；`target_id` 命中时优先于注入的 `params["selector"]`；element_id 只在单个 Snapshot 内有效——frame 失效后旧路径作废，重新 Observe 后须以新 Snapshot 的 target_id 重新定位（机械重试因此不替换 Snapshot，见「失败自动重试」）
- **历史记忆（V0.5）**：每步经 `_record_step()` 写入原始历史与 `HistoryMemory`，`plan_with_history` / `plan_step` / `reflect` 传 `memory.context_entries()`（摘要 + 最近窗口）而非原始全量历史

#### `executor.py` — Executor（Action → BrowserTool 翻译层）

```python
class Executor:
    def __init__(self, browser_tool: BrowserTool,
                 *, download_dir=None, allowed_upload_dirs=None)
```

- `execute(action)` — 分发表：action.type → handler
- `download_dir` / `allowed_upload_dirs`（M5 文件路径策略，可选）：
  - `download_dir`：download 落盘目录；未提供时 BrowserTool 默认落盘当前目录 + 服务器文件名。`save_path` 双防线剥离——parse 层（`parse_action_dict`）与 Executor 层均无条件剥离，不经 parse 构造的 Action 同样无法指定落点
  - `allowed_upload_dirs`：upload 文件所在目录白名单；**未配置则 upload 动作一律拒绝**（默认最严格），配置后 value 须落在允许目录内
- `_resolve_selector(target, params)` — 优先级：
  1. `params["selector"]` 显式指定
  2. CSS 选择器风格（以 `#`, `.`, `[`, `:` 开头）
  3. 纯 HTML 标签名直通（如 `input`、`button`，`_HTML_TAGS` 集合内）
  4. 默认 `:has-text("...")` 子串匹配（与 SnapshotGenerator 统一）
- `_build_frame_map(snapshot)` / `_resolve_frame_path(action)` — iframe 支持（V1.0 子计划 A）：构建 `element_id → frame_path` 映射；`target_id` 命中时透传 frame_path 给 BrowserTool，且**优先于注入的 `params["selector"]`**

#### `observer.py` — Observer

```python
class Observer:
    def __init__(self, snapshot_generator: SnapshotGenerator)
```

| 方法 | 说明 |
|------|------|
| `observe()` | 生成 Snapshot + 检测页面类型 |
| `observe_simplified()` | 返回简化版 dict（供 LLM 提示词使用） |

#### `planner.py` — Planner（V0.2 规则驱动 + V0.3 LLM 驱动 + V0.4 前瞻两阶段）

| 类/函数 | 说明 |
|----|------|
| `parse_goal(goal)` | 从目标提取 URL / 搜索词 / 点击目标 / 等待条件 → TaskSpec |
| `Planner` | 基类，`plan()` 抛出 NotImplementedError；`plan_with_history()` 默认转发 plan；`plan_batch()` 默认退化为单动作（V1.0 批量增强）；`decompose()` 默认返回 None（自由模式）；`plan_step()` 默认退化为 plan_with_history；`reflect()` 默认返回 None（不支持 Reflection）；`on_action_result()` / `reset()` 默认 no-op；`stagnation_detection=True`（M4，停滞检测开关）。**history 契约（V0.5 Memory breaking change）：** `plan_with_history`/`plan_step`/`reflect` 收到的 history 可能含 `{"kind":"summary","text":...}` 头部条目，应经 `prompts.serialize_history` 渲染或自行兼容 |
| `RuleBasedPlanner` | 规则引擎（V0.2 完成）：8 条内置规则 + `add_rule()` 自定义规则优先；`stagnation_detection=False`（M4，自身用 `_WAIT_MAX_TRIES` 控制等待） |
| `LLMPlanner` | LLM 规划器（V0.3 完成，自由模式）：Snapshot 序列化 → 提示词 → 模型输出 → 安全 Action 转换；内容层错误最多一次修复；`reflect()` 失败反思（V0.4）；`allowed_upload_dirs`（M5，可选，未配置时模型输出 upload 动作被拒绝） |
| `TaskStep` / `TaskQueue` | 任务步骤数据模型与队列（V0.4 前瞻）：kind ∈ action/wait/verify；wait/verify 由框架直接执行 |
| `TaskPlanner` | 两阶段规划器（V0.4 前瞻）：`decompose()` 一次 LLM 调用把目标拆成步骤队列 + `plan_step()` 提示词携带「当前步骤 + 剩余步骤」分步决策；拆解失败自动回退自由模式 |

**Goal 语法（parse_goal）：**

| 写法 | 示例 | 提取 |
|------|------|------|
| 打开 URL | `打开 https://www.baidu.com` | `url` |
| 搜索词 | `查找 X` / `搜索 X` / `查询 X`（单个词） | `search_keywords` |
| 点击目标 | `点击 北京时间 - 百度百科`（遇到下个动词截断）/ `点击"登录"` | `target_texts` |
| 等待加载 | `等待 页面加载完成` | `wait_loading` |
| 等待文本 | `等待 结果出现` | `wait_texts` |

**RuleBasedPlanner 内置规则优先级：**
1. 完成判定 —— 无点击目标：已提交且页面出现关键词 / 纯导航到达 URL；有点击目标：**全部目标点击完成**才收工 → `done`
2. 导航（目标含 URL 且未到达）→ `goto`
3. 搜索输入（有搜索框）→ `input`
4. 提交搜索（点击搜索按钮）→ `click`
5. 等待条件（页面未加载完 / 等待文本未出现，最多重试 10 次）→ `wait`
6. 点击目标（未点击过的目标文本，文本已归一化匹配）→ `click`
7. 结果首条链接 → `click`
8. 兜底等待（结果未渲染）→ `wait`

#### `memory.py` — HistoryMemory（V0.5 Memory）

增量式历史记忆：解决长任务中滑动窗口丢弃早期上下文的问题。

| 方法/属性 | 说明 |
|----|------|
| `summarize_entries(entries)` | 规则式摘要纯函数：每条历史压缩为一行「动作 目标 → 结果」；不含输入值/URL 等敏感内容 |
| `add(entry)` | 追加一条历史（与 `Agent.history` 共享引用，只读） |
| `maybe_summarize()` | 达到 `window + batch` 阈值时把最旧一批折叠为摘要（每条目只摘要一次，增量） |
| `context_entries()` | 压缩上下文 = `[摘要?]` + `[最近窗口]`，摘要不占窗口名额 |
| `summary` / `recent` / `count` / `clear()` | 摘要文本 / 未折叠条目 / 总数 / 重置 |

- 默认 `window=5, batch=10`；可注入异步摘要器（`async (entries) -> str`，如 LLM 摘要）替换规则式默认。
- Agent 每步经 `_record_step()` 写入记忆并触发折叠；`plan_with_history` / `plan_step` / `reflect` 改传 `context_entries()`。
- 序列化协议：内存条目 `{"kind": "summary", "text": ...}` → prompts 层渲染为 `{"summary": ...}` 且恒在头部。

### 4.4 LLM 层 (`agent/llm/`) 与 Prompts 层 (`agent/prompts/`) — V0.3

**依赖方向（单向，禁止反向）：**
```text
LLMClient / prompts → LLMPlanner → Action
Snapshot → LLMPlanner
Executor → BrowserTool
```
`llm/` 不导入 Playwright，`BrowserTool` 不导入 LLM 代码。

#### `llm/base.py` — LLMClient 协议 + 错误分类

```python
class LLMClient(Protocol):
    async def complete_json(self, *, system_prompt: str, user_prompt: str,
                            schema: dict, timeout: int) -> dict: ...
```

- 错误分类：`LLMRetryableError`（`LLMTimeoutError` / `LLMNetworkError` / `LLMRateLimitError`）与不可重试的 `LLMInvalidResponseError`。
- 异常消息不得包含 API Key、Cookie、完整提示词或敏感页面内容。

#### `llm/mock.py` — MockLLMClient

按预设响应队列返回 dict（可注入异常），记录每次调用参数，用于无网络测试与离线回归。

#### `llm/openai_client.py` — OpenAILLMClient

OpenAI 兼容 Chat Completions 适配器；API Key 只从 `OPENAI_API_KEY` 环境变量或显式参数读取；`max_retries=0`（重试交给 V0.4）。

#### `prompts/planner.py` — 序列化 / 提示词 / Action 解析

| 函数 | 说明 |
|------|------|
| `serialize_snapshot()` | Snapshot → 模型视图（只含可交互元素，脱敏 + 截断，元素超限标记 `elements_truncated`） |
| `sanitize_url()` | 移除 fragment；token/session/code 等查询参数值掩码为 `***` |
| `find_element_by_id()` | 当前 Snapshot 内 `element_id → ElementInfo` 映射（只含可见元素） |
| `serialize_history()` | 历史压缩为「摘要 + 最近窗口」（V0.3 滑动窗口 5 条 + V0.5 摘要条目渲染，剔除截图/下载路径/堆栈） |
| `build_action_schema()` | 模型输出 schema，action 枚举与 `Action.validate()` 同源 |
| `parse_action_dict()` | 模型 dict → 已验证 Action：伪造 selector 忽略、target_id 必须命中、未知字段丢弃 |
| `BATCH_SYSTEM_PROMPT` / `build_hybrid_schema()` / `parse_action_list()` | 批量规划（V1.0 自由模式增强）：导航/跳转场景输出单 Action、表单场景输出 `{"actions":[...]}`（最多 10 个）；解析兼容单/批量两种输出 |

**安全边界（模型不能越界）：** 模型只看到 `target_id` 与语义字段；可执行 selector 只由本地 Snapshot 映射注入；幻觉 ID、非法 action、伪造 selector 一律在进入 Executor 前被拦截；**M5 文件路径策略**：模型不得指定下载落盘路径（`save_path` 在 parse 层与 Executor 层双防线无条件剥离，落盘目录仅由 `Executor.download_dir` 决定，未配置时 BrowserTool 沿用 cwd + 服务器文件名）；upload 必须显式允许（`allowed_upload_dirs` 未配置即拒绝，配置后 value 须落在允许目录内，Executor 二次校验为防御纵深）。

---

## 五、当前状态 (V0.5 + V1.0 子计划 A)

### 已完成（V0.1 ~ V0.5 + V1.0 子计划 A）

- ✅ 12 种 Action 类型定义 + 工厂函数 + 参数验证
- ✅ Observation 统一返回格式
- ✅ Snapshot + ElementInfo 数据结构（含 element_id 全局定位）
- ✅ BrowserTool 11 个 Playwright 操作封装
- ✅ SnapshotGenerator 从页面提取语义信息（已过滤不可见元素）
- ✅ Executor Action→BrowserTool 翻译层（支持 target_id 精确定位）
- ✅ Observer SnapshotGenerator 包装
- ✅ Agent 主循环框架 (Observe→Plan→Execute→Record)，V0.3 起经 `plan_with_history()` 传入有限历史
- ✅ **RuleBasedPlanner 规则引擎（V0.2）**：parse_goal 目标解析（URL/搜索词/点击目标/等待条件）+ 8 条内置规则
- ✅ **click() page_changed 增强（V0.2）**：URL + 标题 + DOM 指纹三重判定
- ✅ **执行契约加固（V0.2 阶段 A）**：Action 参数校验完整化、BrowserTool 异常边界统一（wait/scroll 防护）、Snapshot selector CSS 转义与 element_id 生命周期重置、Observation.fail() 显式 data
- ✅ **LLM 接入（V0.3）**：`LLMClient` 协议 + 错误分类、`MockLLMClient`（无网络测试）、`OpenAILLMClient`（OpenAI 兼容 + 7 家国内大模型预设）、`LLMPlanner`（Snapshot 序列化 → 提示词 → 安全 Action 转换，内容层错误一次修复）
- ✅ **安全边界（V0.3）**：模型上下文脱敏（无 selector/HTML/Cookie/截图，URL 敏感参数掩码）、伪造 selector 忽略、幻觉 target_id 拦截
- ✅ **两阶段任务队列（V0.4 前瞻）**：`TaskPlanner`（`decompose` 拆解目标为步骤队列 + `plan_step` 分步决策）、`TaskStep/TaskQueue`、Agent 双模式（步骤模式/自由模式自动回退）、wait/verify 步骤由框架直接执行（不经过 LLM）、队列耗尽即完成
- ✅ **异常防护（V0.4 前瞻）**：`_safe_observe()` / `_safe_execute()` 浏览器关闭时优雅失败；停滞检测（LLM 连续 2 次 wait 且页面无变化提前终止）
- ✅ **Reflection 重试（V0.4）**：Agent 执行失败混合重试（机械 1 次 → `Planner.reflect()` 失败反思给出替代动作 1 次 → 仍失败尝试页面恢复 → 中止）；LLM 可重试错误（超时/限流/网络）指数退避自动重试并封顶（默认 5 次、基础间隔 2s、封顶 16s，`LLMPlanner(llm_retries/llm_retry_delay/llm_retry_max_delay)` 可覆盖）；等待步骤框架兜底（拆解误拆 action 时按描述自动纠正为 wait 并提取毫秒）；**后退/刷新恢复**（失败后自动 back/refresh 重置页面状态再重新规划，默认最多 2 次）
- ✅ **工程化增强**：`setup_logging`（控制台 + `logs/` 按天滚动文件）、Snapshot 批量 JS 提取提速（每帧 1 次 evaluate 替代逐元素 CDP 往返；Mock 逐元素回退路径属性并发取回，帧间仍串行遍历）、新标签页轮询跟随、`.env` 零依赖加载链
- ✅ **Snapshot 批量 JS 提取（V1.0 前性能优化）**：把逐元素约 15 次 CDP 调用合并为每帧 1 次 `frame.evaluate`（`_EXTRACT_JS` + `_extract_scope` + `_from_raw`），解决大页面（如 SAP 多层 iframe）Snapshot 生成 500s+ 的问题（详见 [待解决问题.md](待解决问题.md) #9）；真实 Page/Frame 走批量路径，Mock 自动回退逐元素路径保持测试兼容
- ✅ **iframe 支持（V1.0 子计划 A）**：`ElementInfo` 新增 `frame_path`（元组，空为主页面）；`SnapshotGenerator` 递归遍历多层 iframe（`_iter_scopes`/`_walk_scopes`/`_frame_segments`，重复 id/name 用位置 `nth=j` 消歧，子 frame 加载有限超时跳过）；`Executor` 构建 `element_id → frame_path` 映射（target_id 优先于注入 selector）；`BrowserTool._locator` 逐层 `frame_locator` 穿透；frame 失效后重新 Observe 再解析；真实 Playwright 浏览器三层 iframe fixture 测试（`test_snapshot_frame.py`）
- ✅ **Memory（V0.5）**：`HistoryMemory` 增量式历史记忆（滚动摘要：超出窗口的旧条目按批折叠，默认 `window=5, batch=10`，可注入异步 LLM 摘要器）、`summarize_entries()` 规则式摘要（不含输入值/URL 等敏感内容）、上下文压缩（`context_entries()` = 摘要 + 最近窗口，摘要不占窗口名额）、`serialize_history` / `build_user_prompt` 摘要协议、Agent `_record_step()` 同步记录 + `plan_with_history` / `plan_step` / `reflect` 传压缩上下文
- ✅ **批量规划（V1.0 自由模式增强）**：`plan_batch()` 一次 LLM 返回最多 10 个动作（`BATCH_SYSTEM_PROMPT` / `build_hybrid_schema` 混合 schema / `parse_action_list` 兼容单/批量输出），同 Snapshot 连续执行、任一失败/页面变化/done 即断批；`Planner.plan_batch` 默认退化为单动作保证兼容；**重复动作检测**（页面未变化时连续 `_MAX_REPEAT_SKIPS=3` 次相同动作判停滞）；**`run(goal, timeout_seconds)`** 墙钟超时兜底
- ✅ **接口契约加固（M4，2026-08-24 评审修复）**：自由模式 `plan_batch` 改 `getattr` 逐级回退（`plan_batch → plan_with_history → plan`），V0.3 鸭子类型 Planner 不再崩溃；基类声明 history 摘要条目契约（breaking change）；停滞检测按 `Planner.stagnation_detection` 启用（`RuleBasedPlanner` 覆盖 `False`，`_WAIT_MAX_TRIES=10` 不再被截断）
- ✅ **文件路径安全（M5，2026-08-24/27 评审修复）**：模型 download `save_path` 双防线无条件剥离（`parse_action_dict` + Executor 层，落盘目录仅由 `Executor.download_dir` 决定）；`BrowserTool.download` 新增 `download_dir` 目录参数（优先于 save_path，目录与 `suggested_filename` 拼接）；upload 强制目录白名单（`allowed_upload_dirs` 未配置即拒绝、`LLMPlanner` 透传、Executor 二次校验）；`schema/action.py` 纯函数 `is_path_within_allowed` 改用 `Path.resolve()` 解析符号链接/junction，防白名单目录内链接指向外部路径绕过
- ✅ 16 个测试文件，400+ 个用例（Schema / Executor / Planner / BrowserTool / SnapshotGenerator / Snapshot iframe / Agent 集成 / LLMClient / 序列化 / LLMPlanner / Memory / Logging / TaskQueue）
- ✅ 5 个 Demo（手动 / 规则 Agent 本地页 / 规则 Agent 百度 / LLM Agent 自由模式 / LLM Agent 两阶段真实百度，端到端跑通）+ 1 个业务示例（`examples/ex_robot/ydgx`：LLM 局部辅助 + 多层 iframe + 失败回退确定性，不入 git）

### 已知问题（详见 [待解决问题.md](待解决问题.md)，下表为摘要）

| # | 问题 | 优先级 | 状态 |
|---|------|--------|------|
| #4 | download 直接拼接 `suggested_filename` 落盘，不可信文件名存在路径穿越面 | 🔴 | 待修复 |
| #5 | texts 含 span 噪音 | 🟡 | ⏸️ 暂不处理 |
| — | 规则 7「点击首条结果」非死代码，设计取舍保留（本文件自管，未入 待解决问题.md） | 🟡 | 🔒 保留 |

> 完整列表见 [待解决问题.md](待解决问题.md)：当前跟踪 46 项（🔴 1、🟡 12、🟢 33、⏸️ 1），已解决条目随修复移除，编号不复用。2026-08-27 修复三项：文件路径白名单 `Path.resolve()` 防符号链接/junction 绕过、download 新增 `download_dir` 目录参数且 Executor 无条件剥离 `save_path`、iframe 动作机械重试固定显式 selector 定位（不再刷新 Snapshot）；更早修复历史见该文件「最近移除」。

---

## 六、开发路线

| 版本 | 目标 | 关键变更 | 状态 |
|------|------|----------|------|
| **V0.1** | 执行层：Browser Tool + Snapshot + Observation + Schema | 核心执行框架 | ✅ 完成 |
| **V0.2** | Agent Loop：规则驱动 Planner + 执行契约加固 | `RuleBasedPlanner` 规则引擎（目标解析：URL/搜索词/点击目标/等待条件 + 8 条内置规则）；Snapshot 不可见元素过滤 + selector CSS 转义 + element_id 生命周期；click() page_changed DOM 指纹检测；Action 参数校验完整化；BrowserTool 异常边界统一 | ✅ 完成 |
| **V0.3** | 接入 LLM：LLM Planner | `LLMClient` 协议 + 错误分类；`MockLLMClient` + `OpenAILLMClient`；`LLMPlanner`（Snapshot 脱敏序列化 → 提示词 → 安全 Action 转换 + 一次修复）；Agent `plan_with_history()` 传历史 | ✅ 完成 |
| **V0.4** | Reflection：错误恢复与重试 | 任务步骤队列（TaskPlanner decompose→分步执行）；wait/verify 框架直执行；停滞检测；浏览器关闭异常防护；**Agent 失败重试**（机械 1 次 → Reflection 1 次 → 页面恢复 → 中止）；**LLM 可重试错误自动重试**（指数退避）；等待步骤框架兜底（action→wait 纠正）；**后退/刷新恢复** | ✅ 完成 |
| **V0.5** | Memory：历史操作与上下文记忆 | `HistoryMemory` 增量式记忆（滚动摘要：窗口外旧条目按批折叠，默认 window=5/batch=10，可注入异步 LLM 摘要器）；`summarize_entries()` 规则式摘要；`context_entries()` 上下文压缩（摘要 + 窗口）；`serialize_history` / `build_user_prompt` 摘要协议；Agent 记录与传参接线 | ✅ 完成 |
| **V1.0** | 完整 Agentic RPA | 多层 iframe 操作基座已完成（子计划 A）；登录/查询/下载/上传/Excel 长流程（子计划 D）| 🚧 子计划 A 完成，其余规划中 |

### 各版本关键关注点

- **V0.2（已完成）备注：** Snapshot 不可见元素过滤、selector CSS 转义与优先级（2.3）、element_id 生命周期重置已完成；`RuleBasedPlanner` 8 条规则已完成（含点击目标、等待条件）；click() page_changed 已含 DOM 指纹；Action 参数校验与 BrowserTool 异常边界已加固。遗留项：bbox 未启用
- **V0.3（已完成）备注：** `llm/`（base/mock/openai_client）与 `prompts/`（planner.py 序列化/提示词/解析）已实现；`LLMPlanner` 可替换 RuleBasedPlanner（两者可并存，便于离线回归与 fallback）；Agent `run()` 已通过 `plan_with_history()` 传入历史；`observe_simplified()` 暂由 `serialize_snapshot()` 取代（更结构化）。Provider 采用 OpenAI 兼容协议，覆盖国内主流厂商预设（DeepSeek/Kimi/智谱/通义/豆包/千帆/星火），连通性可用 `examples/check_llm_connectivity.py` 验证
- **V0.4（已完成）备注：** Agent 失败重试（机械 1 次 → Reflection 1 次 → 后退/刷新恢复 → 中止）已完成；LLM 可重试错误（超时/限流/网络）指数退避自动重试已完成；停滞检测已完成（自由模式）；后退/刷新恢复已完成（`_recover_page()`：优先 back、失败降级 refresh，每 run 默认最多恢复 2 次，恢复后重新观察规划当前步骤）
- **V0.5（已完成）备注：** `HistoryMemory`（`agent/core/memory.py`）增量式记忆已完成：超出窗口的旧条目按批折叠为摘要（默认 window=5/batch=10，每条目只摘要一次），`context_entries()` 输出「摘要 + 最近窗口」压缩上下文；`serialize_history` / `build_user_prompt` 支持摘要条目（渲染为 `{"summary": ...}` 且恒在头部、不占窗口）；Agent 每步经 `_record_step()` 记录并触发折叠，向 `plan_with_history` / `plan_step` / `reflect` 传压缩上下文，`Agent.history` 仍保留完整原始列表。摘要只含 action/target/成功与否，不含输入值/URL 等敏感内容。V1.0 可在此基础上做跨任务持久化记忆

---

## 七、测试策略

- **Schema 测试（现有）：** 纯数据类测试，无外部依赖
- **Executor 测试（现有）：** Mock BrowserTool 验证 dispatch 和参数传递
- **Planner 测试（现有）：** Mock Snapshot 验证规则匹配与状态推进
- **BrowserTool 测试（现有）：** AsyncMock Page 对象，覆盖 click() page_changed DOM 指纹、wait()/scroll() 非法参数防护与异常转换、新标签页轮询跟随
- **SnapshotGenerator 测试（现有）：** Mock Page 验证可见性过滤、selector 优先级与 CSS 转义、element_id 生命周期、页面关闭防御
- **Snapshot iframe 测试（现有）：** 真实 Playwright Chromium 加载三层 iframe fixture，验证递归提取、frame_path 生成、target_id 跨 frame 唯一、重复 id 位置消歧、主页面零回归
- **Agent 集成测试（现有）：** Mock Observer/Planner/BrowserTool 验证 run() 全链路（done / 执行失败 / 非法 Action / 最大步数 / LLM 驱动多步 / 停滞检测 / 步骤模式 wait-verify 框架直执行 / TaskPlanner 两阶段完整链路 / 观察异常防护）
- **LLMClient 测试（现有）：** Mock 客户端响应队列 / 异常注入 / 调用记录；错误分类（可重试 vs 不可重试）；OpenAI 客户端未配 Key 提示
- **序列化测试（现有）：** 上下文不含 selector/HTML/Cookie、URL 脱敏、元素/文本截断、序列化稳定、element_id 映射、历史窗口
- **LLMPlanner 测试（现有）：** 合法 Action、幻觉 ID、伪造 selector、非法 action、数组输出、一次修复恢复、连续失败停止、可重试错误不重试
- **Logging 测试（现有）：** setup_logging 控制台 / 文件 sink 行为
- **Memory 测试（现有）：** 规则式摘要格式化与敏感字段排除、HistoryMemory 折叠阈值 / 增量只摘要一次 / 窗口边界 / clear / 自定义摘要器、serialize_history / build_user_prompt 摘要协议与纯列表回归、Agent 长任务向 Planner 传摘要且 history 原始完整
- **TaskQueue 测试（现有）：** 队列消费顺序、拆解解析（wait 毫秒归一 / verify 参数 / 非法条目过滤）、TaskPlanner 拆解失败回退、plan_step 步骤上下文注入

### 运行测试

```bash
cd agent-rpa
pip install -e ".[dev]"    # 安装包含 pytest
pytest                     # 运行全部测试
```

---

## 八、AI 编程助手指引

### 修改代码前

1. 阅读 `AGENTS.md` 了解整体架构
2. 阅读 `待解决问题.md` 了解已知问题
3. 确认当前正在开发的版本（V0.1/V0.2/...）

### 常见操作模式

- **添加新 Action 类型：** `action.py` 增加 `ActionType` → `executor.py` 增加 handler → `playwright.py` 增加方法（如果需要）→ 测试
- **修改元素提取逻辑：** `snapshot.py` 的提取方法
- **修改选择器策略：** `snapshot.py` (`_build_selector`) 和 `executor.py` (`_resolve_selector`) 需要同步修改
- **增强 Agent 循环：** `agent.py` 的 `run()` 方法

### 关键契约

- **Snapshot 与 Executor 的选择器必须保持一致** — 修改任何一方的选择器逻辑时，必须同步修改另一方
- **所有 BrowserTool 方法必须返回 Observation** — 不要抛异常到上层
- **Observer 只读，不修改页面** — 所有写操作通过 Executor

---

## 九、快速索引

| 需要做的事 | 先读什么 |
|-----------|---------|
| 了解项目全局 | 本文件 |
| 了解要修什么 bug | `待解决问题.md` |
| 了解架构背景 | `Agentic_RPA_项目规划_V0.1.md` |
| 了解当前开发计划 | `V1.1开发计划.md`（问题分组映射见其第 2 节） |
| 增删改 Action 类型 | `agent/schema/action.py` + `agent/core/executor.py` |
| 修改页面元素抓取 | `agent/browser/snapshot.py` |
| 修改浏览器操作 | `agent/browser/playwright.py` |
| 修改 Agent 主逻辑 | `agent/core/agent.py` |
| 修改规划逻辑 | `agent/core/planner.py` |
| 修改记忆/上下文压缩 | `agent/core/memory.py` + `agent/prompts/planner.py` |
| 添加测试 | `tests/` 下对应文件 |

---

## 十、协作准则

### 关键约定

- **提交信息**：提交代码时，不要在日志中写入 AI 辅助信息（如 `Co-Authored-By` 等）
- **语言**：所有对话、注释、提交信息必须使用**简体中文**，技术术语附英文原文

### 行为准则

#### 1. 先思考再编码

**不要假设。不要隐藏困惑。呈现权衡。**

实现之前：

- 明确陈述你的假设。如果不确定，请提问。
- 如果存在多种解读，全部列出——不要 silently 选择其一。
- 如果存在更简单的方案，请指出来。必要时提出反对。
- 如果有任何不清楚的地方，停下来。说出困惑之处。提问。

#### 2. 简洁优先

**用最少的代码解决问题。不做推测性工作。**

- 不实现未经要求的功能。
- 不为一次性代码创建抽象。
- 不添加未经要求的"灵活性"或"可配置性"。
- 不为不可能发生的场景编写错误处理。
- 如果你写了 200 行而本可以用 50 行完成，重写它。

问问自己："资深工程师会觉得这过于复杂吗？"如果是，请简化。

#### 3. 精准修改

**只改动必须改的。只清理自己造成的混乱。**

编辑现有代码时：

- 不要"改进"相邻代码、注释或格式。
- 不要重构没有问题的东西。
- 匹配现有风格，即使你有不同的做法。
- 如果发现无关的死代码，提出来——但不要删除。

当你的修改造成孤立代码时：

- 移除因**你的**修改而不再使用的导入/变量/函数。
- 除非被要求，否则不要移除原有的死代码。

检验标准：每一行改动都应能直接追溯到用户的需求。

#### 4. 目标驱动执行

**定义成功标准。循环执行直到验证通过。**

将任务转化为可验证的目标：

- "添加校验" → "编写针对非法输入的测试，然后让测试通过"
- "修复 Bug" → "编写能复现问题的测试，然后让测试通过"
- "重构 X" → "确保重构前后测试均通过"

对于多步骤任务，给出简要计划：

```
1. [步骤] → 验证：[检查项]
2. [步骤] → 验证：[检查项]
3. [步骤] → 验证：[检查项]
```

明确的成功标准让你能独立循环推进。模糊的标准（"让它跑起来"）需要不断确认。

---

**这些指南生效的标志：** 差异中不必要的改动减少，因过度复杂导致的重写减少，澄清性问题出现在实现之前而非犯错之后。
