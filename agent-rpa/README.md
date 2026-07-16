# Agentic RPA

基于 Playwright 的自主网页操作 Agent 框架。

## 架构设计

```text
User Goal
     │
     ▼
Agent Loop
     │
  ┌──┴──┐
  │     │
Observe Execute
  │     │
  ▼     ▼
Browser Snapshot → Browser Tool
  │     │
  └──┬──┘
     ▼
Playwright
```

### 核心原则

1. **LLM 不知道 Playwright** — LLM 输出高层级 Action，Playwright 只是执行器
2. **Browser Tool 与 Snapshot 分离** — Snapshot 描述页面，Tool 执行操作
3. **高内聚、低耦合** — 便于未来替换 Selenium、Appium 等执行器

## V0.1 执行层

第一阶段只做四件事：

1. **Browser Tool** — 封装 Playwright 操作（goto/click/input/select/scroll...）
2. **Snapshot** — 页面结构化认知（按钮/输入框/链接/文本...）
3. **Observation** — 执行结果统一返回格式
4. **Action Schema** — Agent 输出的标准动作格式

### BrowserManager 配置

```python
from agent.browser.playwright import BrowserManager

# 有头模式（默认，适合调试和需要反检测的场景）
manager = BrowserManager(headless=False, slow_mo=50)

# 无头模式（适合服务器部署）
manager = BrowserManager(headless=True)

# 使用系统安装的 Chrome（而非 Playwright 内置 Chromium）
manager = BrowserManager(channel="chrome")

# 指定浏览器可执行文件路径
manager = BrowserManager(executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe")
```

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `headless` | `False` | 是否无头模式。`False` 时自动启用最大化窗口 + `no_viewport` |
| `slow_mo` | `50` | 操作间延迟（毫秒），模拟人类操作速度；`0` 可关闭 |
| `channel` | — | 使用系统安装的浏览器：`"chrome"`、`"msedge"`、`"chrome-beta"` 等 |
| `executable_path` | — | 指定浏览器可执行文件路径（优先级高于 `channel`） |
| `proxy` | — | 代理配置，如 `{"server": "http://proxy:8080"}` |
| `**kwargs` | — | 其余参数透传给 `playwright.chromium.launch()` |

三种浏览器选择策略（优先级从高到低）：

1. **`executable_path`** — 指定路径的浏览器
2. **`channel`** — 系统安装的 Chrome/Edge 等
3. **不指定** — 使用 Playwright 内置 Chromium（版本完全匹配，最稳定）

#### 反检测

`BrowserManager.start()` 自动应用以下 Chromium 启动参数：

| 参数 | 作用 |
| :--- | :--- |
| `--disable-blink-features=AutomationControlled` | 隐藏 `navigator.webdriver` 等自动化标记 |
| `--disable-dev-shm-usage` | 避免 Linux `/dev/shm` 不足导致崩溃 |
| `--no-sandbox` | 沙箱兼容性 |
| `--disable-gpu` | 减少 GPU 指纹特征 |
| `--start-maximized` | 非 headless 时窗口最大化 |

非 headless 模式下自动设置 `no_viewport=True`，使用真实屏幕尺寸而非固定视口。

### 目录结构

```text
agent-rpa/
├── agent/
│   ├── core/
│   │   ├── agent.py        # Agent 主循环
│   │   ├── planner.py      # 规划器（V0.2 规则实现）
│   │   ├── executor.py     # 动作执行器
│   │   └── observer.py     # 观察者
│   │
│   ├── browser/
│   │   ├── playwright.py   # Browser Tool + BrowserManager
│   │   └── snapshot.py     # Snapshot 生成器
│   │
│   ├── schema/
│   │   ├── action.py       # Action 数据模型
│   │   ├── observation.py  # Observation 数据模型
│   │   └── snapshot.py     # Snapshot 数据模型
│   │
│   ├── llm/                # (预留) LLM 模块
│   ├── prompts/            # (预留) 提示词模板
│   └── tools/              # (预留) 辅助工具
│
├── examples/
│   ├── manual_demo.py      # 手动模式 Demo —— 完整 RPA 流程（百度搜索+结果保存）
│   └── agent_demo.py       # Agent 模式 Demo
│
└── tests/
    ├── test_action.py
    ├── test_observation.py
    ├── test_snapshot.py
    └── test_executor.py
```

## 快速开始

```bash
# 安装依赖
pip install -e .

# 安装 Playwright 浏览器
playwright install chromium

# 运行 Demo（手动模式：百度搜索"北京时间"，保存结果页面）
python examples/manual_demo.py

# 运行测试
pytest
```

## demo 功能说明

### manual_demo.py — 百度搜索 RPA 流程

演示 Browser Tool 的完整 RPA 流程：

1. 打开百度首页
2. 自动检测页面版本（AI 版 `#chat-textarea` / 经典版 `#kw`）
3. 输入搜索词"北京时间"并搜索
4. 获取搜索结果第一条链接的文本
5. 点击链接（自动识别新标签页并切换）
6. 获取新页面的 URL、标题、HTML 内容
7. 将 HTML 保存到 `结果/{时间戳}/正文.html`
8. 关闭浏览器

## 开发路线

| 版本 | 目标 |
| :--- | :--- |
| V0.1 | 执行层：Browser Tool + Snapshot + Observation + Schema |
| V0.2 | Agent Loop：规则驱动 Planner |
| V0.3 | 接入 LLM：LLM Planner |
| V0.4 | Reflection：错误恢复与重试 |
| V0.5 | Memory：历史操作与上下文记忆 |
| V1.0 | 完整 Agentic RPA：登录/查询/下载/上传/Excel 处理 |
