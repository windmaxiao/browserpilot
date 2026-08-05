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
├── 待解决问题.md                       # 已知问题和修复追踪
├── Agentic_RPA_项目规划_V0.1.md        # 原始项目规划文档
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
    │   │
    │   ├── schema/                    # 【数据模型层】— 无外部依赖，纯 dataclass
    │   │   ├── __init__.py
    │   │   ├── action.py              #   Action 数据模型 + 工厂函数
    │   │   ├── observation.py         #   Observation 数据模型
    │   │   └── snapshot.py            #   Snapshot + ElementInfo 数据模型
    │   │
    │   ├── browser/                   # 【浏览器执行层】— Playwright 封装
    │   │   ├── __init__.py
    │   │   ├── playwright.py          #   BrowserTool + BrowserManager
    │   │   └── snapshot.py            #   SnapshotGenerator（页面→Snapshot）
    │   │
    │   ├── core/                      # 【Agent 循环核心】— 编排层
    │   │   ├── __init__.py
    │   │   ├── agent.py               #   Agent 主循环
    │   │   ├── executor.py            #   Action → BrowserTool 翻译层
    │   │   ├── observer.py            #   SnapshotGenerator 的 Agent 包装
    │   │   └── planner.py             #   Planner 基类 + RuleBasedPlanner
    │   │
    │   ├── llm/                       # 【预留】LLM 模块
    │   │   └── __init__.py
    │   ├── prompts/                   # 【预留】提示词模板
    │   │   └── __init__.py
    │   └── tools/                     # 【预留】辅助工具
    │       └── __init__.py
    │
    ├── examples/
    │   ├── manual_demo.py             # 手动模式 Demo（直接调用工具）
    │   └── agent_demo.py              # Agent 模式 Demo（Agent 主循环）
    │
    └── tests/
        ├── __init__.py
        ├── test_action.py             # Action Schema 测试
        ├── test_observation.py        # Observation Schema 测试
        ├── test_snapshot.py           # Snapshot Schema 测试
        └── test_executor.py           # Executor 测试（Mock BrowserTool）
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
    tag: str                # HTML 标签名
    element_type: str       # 语义类型: button/link/textbox/dropdown/text/image
    selector: str           # Playwright 选择器（供内部执行使用）
    bbox: Optional[dict]    # bounding box（V0.2+启用）
    aria_label: str         # aria-label 属性
    placeholder: str        # input placeholder
    attributes: dict        # 其他重要属性
    index: int              # 同类元素中的序号

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
| `download(selector, save_path, timeout)` | str, str/Path, int | 下载文件 |
| `upload(selector, file_path, timeout)` | str, str/Path, int | 上传文件 |
| `back()` | — | 浏览器后退 |
| `refresh()` | — | 刷新页面 |
| `screenshot(full_page)` | bool | 截图返回 base64 |

**BrowserManager** — 浏览器生命周期管理：

| 方法 | 说明 |
|------|------|
| `start()` | 启动浏览器（默认 1280×720, zh-CN） |
| `stop()` | 关闭浏览器及所有资源 |
| `page` | 获取当前 Page 对象 |
| `create_tool()` | 创建 BrowserTool 实例 |

#### `snapshot.py` — SnapshotGenerator

从 Playwright Page 提取语义信息生成 Snapshot。

| 方法 | 说明 |
|------|------|
| `generate()` | 生成完整 Snapshot（并行提取各类元素） |
| `detect_page_type()` | 通过 URL/Title 推断页面类型 |
| `_extract_buttons()` | button, [role=button], input[submit], a.btn, *.button |
| `_extract_inputs()` | input(非hidden), textarea, contenteditable, [role=textbox] |
| `_extract_links()` | a[href] |
| `_extract_texts()` | h1-h6, p, span, label, li, td, th, strong, em |
| `_extract_selects()` | select |
| `_build_selector()` | 生成选择器：has-text > id > data-testid > role > aria-label > tag |

---

### 4.3 Core 层 (`agent/core/`)

#### `agent.py` — Agent 主循环

```python
class Agent:
    def __init__(self, observer, planner, executor, max_steps=50)
```

| 方法 | 说明 |
|------|------|
| `run(goal)` | 完整 Agent 循环（Observe→Plan→Execute→Record） |
| `step(action)` | 单步执行（手动/调试模式） |
| `observe()` | 获取当前页面 Snapshot |

**run() 循环流程：**
1. Observe — 调用 Observer 获取 Snapshot
2. Plan — 调用 Planner 生成下一步 Action
3. Check done — action == "done" 则结束
4. Execute — 调用 Executor 执行 Action
5. Record — 记录 (step, action, observation) 到 history
6. Check failure — 失败则返回错误 Observation
7. 循环至 max_steps 或 done

**关键设计决策：**
- Step 索引从 1 开始
- 失败即返回（V0.4 才实现 Reflection 重试）
- Planner 返回 None 表示无法规划

#### `executor.py` — Executor（Action → BrowserTool 翻译层）

```python
class Executor:
    def __init__(self, browser_tool: BrowserTool)
```

- `execute(action)` — 分发表：action.type → handler
- `_resolve_selector(target, params)` — 优先级：
  1. `params["selector"]` 显式指定
  2. CSS 选择器风格（以 `#`, `.`, `[`, `:` 开头）
  3. 默认 `:has-text("...")` 子串匹配（与 SnapshotGenerator 统一）

#### `observer.py` — Observer

```python
class Observer:
    def __init__(self, snapshot_generator: SnapshotGenerator)
```

| 方法 | 说明 |
|------|------|
| `observe()` | 生成 Snapshot + 检测页面类型 |
| `observe_simplified()` | 返回简化版 dict（供 LLM 提示词使用） |

#### `planner.py` — Planner（V0.2 规则驱动）

| 类/函数 | 说明 |
|----|------|
| `parse_goal(goal)` | 从目标提取 URL / 搜索词 / 点击目标 / 等待条件 → TaskSpec |
| `Planner` | 基类，`plan()` 抛出 NotImplementedError（供 V0.3 LLM Planner 继承） |
| `RuleBasedPlanner` | 规则引擎（V0.2 完成）：8 条内置规则 + `add_rule()` 自定义规则优先 |

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

---

## 五、当前状态 (V0.2)

### 已完成（V0.1 + V0.2）

- ✅ 12 种 Action 类型定义 + 工厂函数 + 参数验证
- ✅ Observation 统一返回格式
- ✅ Snapshot + ElementInfo 数据结构（含 element_id 全局定位）
- ✅ BrowserTool 11 个 Playwright 操作封装
- ✅ SnapshotGenerator 从页面提取语义信息（已过滤不可见元素）
- ✅ Executor Action→BrowserTool 翻译层（支持 target_id 精确定位）
- ✅ Observer SnapshotGenerator 包装
- ✅ Agent 主循环框架 (Observe→Plan→Execute→Record)
- ✅ **RuleBasedPlanner 规则引擎（V0.2）**：parse_goal 目标解析（URL/搜索词/点击目标/等待条件）+ 8 条内置规则
- ✅ **click() page_changed 增强（V0.2）**：URL + 标题 + DOM 指纹三重判定
- ✅ 6 个测试文件，144 个用例（Schema / Executor / Planner / BrowserTool）
- ✅ 2 个 Demo（手动模式 + 规则 Agent 模式，端到端跑通）

### 已知问题（详见 [待解决问题.md](待解决问题.md)，下表为摘要）

| # | 问题 | 优先级 | 状态 |
|---|------|--------|------|
| 1 | Snapshot 与 Executor 选择器不一致 | 🔴 | ✅ 已修复 |
| 2 | click/page_changed 始终为 True | 🔴 | ✅ 已修复 |
| 3 | texts 含 span 噪音 | 🟡 | 待解决 |
| 4 | BrowserTool/SnapshotGenerator 无测试 | 🟡 | 部分完成（BrowserTool click 已有测试；SnapshotGenerator 仍缺） |
| 5 | _smart_wait 每次等 8 秒 | 🟡 | ✅ 已修复（wait_for_load_state 3s） |
| 6 | ElementInfo 不保留 data-testid | 🟢 | 待解决 |
| 7 | Observation.ok() data 参数风险 | 🟢 | ✅ 已修复 |
| 8 | 定位契约不完整 | 🔴 | ✅ 已修复 |
| 9 | data 嵌套已影响功能 | 🔴 | ✅ 已修复 |
| 10 | page_changed 仅比较 URL | 🟡 | ✅ 已修复（URL + 标题 + DOM 指纹） |
| 11 | Snapshot 未过滤不可见元素 | 🟡 | ✅ 已修复 |
| 12 | Action.validate() 验证不完整 | 🟡 | 待解决 |
| 13 | 测试命令与安装方式不匹配 | 🟡 | ✅ 已修复 |

---

## 六、开发路线

| 版本 | 目标 | 关键变更 | 状态 |
|------|------|----------|------|
| **V0.1** | 执行层：Browser Tool + Snapshot + Observation + Schema | 核心执行框架 | ✅ 完成 |
| **V0.2** | Agent Loop：规则驱动 Planner | `RuleBasedPlanner` 规则引擎（目标解析：URL/搜索词/点击目标/等待条件 + 8 条内置规则）；Snapshot 不可见元素过滤；click() page_changed DOM 指纹检测 | ✅ 完成 |
| **V0.3** | 接入 LLM：LLM Planner | `LLMPlanner` 类；prompt 模板；Snapshot→LLM→Action 管线 | 📋 待开始 |
| **V0.4** | Reflection：错误恢复与重试 | Agent 失败重试；循环检测；后退/刷新恢复 | 📋 待开始 |
| **V0.5** | Memory：历史操作与上下文记忆 | 摘要式记忆；滑动窗口；上下文压缩 | 📋 待开始 |
| **V1.0** | 完整 Agentic RPA | 登录/查询/下载/上传/Excel 长流程 | 🎯 规划中 |

### 各版本关键关注点

- **V0.2（已完成）备注：** Snapshot 不可见元素过滤与 `_build_selector` 已完成；`RuleBasedPlanner` 8 条规则已完成（含点击目标、等待条件）；click() page_changed 已含 DOM 指纹。遗留项：bbox 未启用、SnapshotGenerator 无独立测试
- **V0.3 重点：** `llm/` 和 `prompts/` 目录的实现；Agent 的 `run()` 需要切换到 LLM Planner；`observe_simplified()` 需要实际被调用
- **V0.4 重点：** Agent 的 run() 循环需要增加重试逻辑和循环检测
- **V0.5 重点：** Agent 的 history 管理需要压缩和摘要策略

---

## 七、测试策略

- **Schema 测试（现有）：** 纯数据类测试，无外部依赖
- **Executor 测试（现有）：** Mock BrowserTool 验证 dispatch 和参数传递
- **Planner 测试（现有）：** Mock Snapshot 验证规则匹配与状态推进
- **BrowserTool 测试（部分）：** AsyncMock Page 对象，已覆盖 click() page_changed DOM 指纹检测
- **SnapshotGenerator 测试（缺失）：** Mock Page 返回模拟 DOM，验证提取逻辑
- **Agent 集成测试（未来）：** 端到端流程验证（当前以 Demo 代替）

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
| 增删改 Action 类型 | `agent/schema/action.py` + `agent/core/executor.py` |
| 修改页面元素抓取 | `agent/browser/snapshot.py` |
| 修改浏览器操作 | `agent/browser/playwright.py` |
| 修改 Agent 主逻辑 | `agent/core/agent.py` |
| 修改规划逻辑 | `agent/core/planner.py` |
| 添加测试 | `tests/` 下对应文件 |
