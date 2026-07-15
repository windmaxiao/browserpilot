# Agentic RPA 项目规划（V0.1）

## 项目目标

基于已有的 Playwright RPA
框架，从零开始构建一个能够自主理解网页、规划操作、执行任务的 Agent。

设计原则：

- 保留成熟的 Playwright 执行层
- Agent 负责决策，不直接操作 Playwright
- 高内聚、低耦合，便于未来替换 Selenium、Appium 等执行器

---

# 整体架构

```text
User Goal
              │
              ▼
         Agent Loop
              │
   ┌──────────┴──────────┐
   │                     │
Observe               Execute
   │                     │
   ▼                     ▼
Browser Snapshot    Browser Tool
   │                     │
   └──────────┬──────────┘
              ▼
         Playwright
```

Agent Loop：

Goal → Snapshot → Planner → Action → Executor → Observation → Loop

---

# 核心原则

## 1. LLM 不知道 Playwright

LLM 输出：

```json
{
  "action":"click",
  "target":"登录"
}
```

不要输出：

```python
page.locator(...)
```

Playwright 只是执行器。

---

## 2. Browser Tool 与 Snapshot 分离

Browser Tool：

- goto()
- click()
- input()
- select()
- scroll()
- wait()
- download()
- upload()

Snapshot：

负责描述当前网页，而不是执行网页。

---

# Snapshot

Snapshot 是 Agent 对网页的认知，而不是 HTML。

推荐 V1：

```python
Snapshot = {
    "title": "",
    "url": "",
    "inputs": [],
    "buttons": [],
    "texts": []
}
```

未来增加：

- dialogs
- tables
- loading
- toast
- alerts
- page_type

---

# Observation

所有 Tool 都返回 Observation。

例如：

```python
{
    "success": True,
    "url": "...",
    "title": "...",
    "page_changed": True,
    "error": None
}
```

---

# 推荐目录

```text
agent-rpa/

├── agent/
│   ├── core/
│   │   ├── agent.py
│   │   ├── planner.py
│   │   ├── executor.py
│   │   └── observer.py
│   │
│   ├── browser/
│   │   ├── playwright.py
│   │   └── snapshot.py
│   │
│   ├── schema/
│   │   ├── action.py
│   │   ├── observation.py
│   │   └── snapshot.py
│   │
│   ├── llm/
│   ├── prompts/
│   └── tools/
│
├── examples/
└── tests/
```

---

# 开发路线

## V0.1 执行层

目标：

- Browser Tool
- Snapshot
- Observation
- Schema

暂不接入 LLM。

---

## V0.2 Agent Loop

实现：

```
while True:
    snapshot = observer.snapshot()
    action = planner(snapshot)
    result = executor(action)
```

Planner 可以先使用规则实现。

---

## V0.3 接入 LLM

Planner 替换为 GPT。

LLM：

Snapshot → Action

Executor：

Action → Playwright

---

## V0.4 Reflection

失败后：

Observation → Planner → 新 Action

实现自动恢复能力。

---

## V0.5 Memory

增加：

- 历史操作
- 页面上下文
- 任务上下文

---

## V1.0 Agentic RPA

完成：

- 登录
- 查询
- 下载
- 上传
- Excel 自动处理
- 长流程任务

---

## 潜在挑战与应对策略

虽然当前架构设计稳健，但在从 V0.1 向 V1.0 演进的过程中，预计会面临以下核心技术挑战。建议在开发相应版本时重点关注。

### 1. Snapshot 的语义鸿沟（定位准确性）

**挑战描述：**
V0.1 阶段的 Snapshot 采用了扁平化的列表结构（如 `buttons: ["登录", "提交"]`）。当页面复杂度提升时，可能出现以下情况导致 Agent 误操作：

- **元素重复**：页面上存在多个文本相同的按钮（如列表中的每一行都有“删除”按钮）。
- **视觉特征缺失**：关键操作按钮是 Icon 图标而非文字，或者依赖颜色、位置来区分。
- **层级丢失**：简单的列表无法表达元素之间的父子或兄弟关系。

**应对策略：**

- **引入空间坐标**：在 V0.2/V0.3 阶段，Snapshot 结构应增加 `bbox` (bounding box) 字段，让 Agent 理解元素的相对位置（例如“点击右侧的按钮”）。
- **增强语义描述**：不仅仅是提取 `text`，还应提取 `aria-label`、`placeholder` 或周边的关联文本作为辅助定位依据。
- **DOM 树简化版**：未来考虑提供轻量级的 DOM 树结构，帮助 Agent 理解页面布局逻辑。

### 2. 异步执行与同步决策的冲突

**挑战描述：**
Playwright 本质上是异步的，而 Agent 的决策循环通常是同步的。如果 Executor 封装不当，可能导致：

- Action 发出后，未等待页面稳定就返回了 Observation。
- 页面跳转过程中产生竞态条件，导致 Agent 操作了过时的页面元素。

**应对策略：**

- **智能等待机制**：在 Browser Tool 层面强制封装智能等待（如 `wait_for_load_state` 或 `wait_for_selector`），确保 Action 返回时，页面已处于可操作状态。
- **状态校验**：在 Observation 中明确返回 `page_changed` 字段，帮助 Agent 判断是否发生了跳转，从而重置内部上下文。

### 3. 上下文窗口与记忆管理

**挑战描述：**
在 V1.0 的长流程任务中，随着交互轮次增加，历史 Snapshot 和 Action 会迅速消耗 LLM 的 Token 限制。

- 上下文过长会导致 LLM “遗忘”初始目标。
- 输入/输出延迟增加，影响 RPA 执行效率。

**应对策略：**

- **摘要式记忆**：在 V0.5 Memory 模块中，实现一个“总结器”。每过 N 轮，将之前的 Observation 和 Action 压缩成一句简短的描述（例如：“已完成登录并导航到报表页”）。
- **滑动窗口**：只保留最近 K 轮的完整 Snapshot，更早的信息仅保留摘要。

### 4. 错误恢复与循环陷阱

**挑战描述：**
在 V0.4 引入 Reflection 机制时，Agent 可能陷入死循环：

- Agent 执行 Action A 失败。
- Planner 误判认为需要重试，再次规划 Action A。
- 无限循环，直至任务超时。

**应对策略：**

- **重复操作检测**：在 Executor 或 Planner 层记录历史 Action。如果连续多次（如 3 次）执行相同的 Action 且均失败，强制触发“高级错误处理”或终止任务。
- **回退机制**：当连续失败时，Agent 应具备“后退”能力（如点击浏览器返回键，或刷新页面），而不是在死胡同里硬试。

### 5. 非预期弹窗与动态内容

**挑战描述：**
Web 页面充满动态性，广告弹窗、Toast 提示、加载动画等可能会遮挡目标元素，导致 Playwright 报错 `ElementClickIntercepted`。

**应对策略：**

- **Snapshot 过滤**：在生成 Snapshot 时，利用 Playwright 的能力过滤掉显眼的广告或遮罩层（如 class 包含 `ad`、`modal` 的元素）。
- **通用清理工具**：为 Agent 增加一个 `try_close_dialogs` 的通用工具，在执行关键 Action 前自动尝试关闭可能存在的弹窗。
  

# 当前里程碑

第一阶段只做四件事：

1. Browser Tool
2. Snapshot
3. Observation
4. Action Schema

完成后再引入 LLM。

这样整个架构会稳定、可测试、可扩展。

