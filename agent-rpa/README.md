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
│   ├── llm/                # LLM 客户端（V0.3）
│   │   ├── base.py         #   LLMClient 协议 + 错误分类
│   │   ├── mock.py         #   确定性 Mock 客户端（无网络测试）
│   │   └── openai_client.py#   OpenAI 兼容 Provider 适配器
│   ├── prompts/
│   │   └── planner.py      # 系统/用户提示词 + Snapshot 序列化 + Action 解析
│   └── tools/              # (预留) 辅助工具
│
├── examples/
│   ├── manual_demo.py      # 手动模式 Demo —— 完整 RPA 流程（百度搜索+结果保存）
│   ├── agent_demo.py       # 规则 Agent 模式 Demo —— 本地搜索页
│   ├── baidu_demo.py       # 规则 Agent 模式 Demo —— 真实百度
│   ├── llm_agent_demo.py   # LLM Agent 模式 Demo —— 本地搜索页
│   └── search_page.html    # 本地确定性搜索页（agent_demo 使用）
│
└── tests/                  # 12 个文件，243 个用例
    ├── test_action.py                    # Action Schema + 参数校验
    ├── test_observation.py               # Observation Schema
    ├── test_snapshot.py                  # Snapshot Schema + Generator 基础
    ├── test_executor.py                  # Executor 调度 / target_id 定位
    ├── test_planner.py                   # RuleBasedPlanner 规则引擎（8 条规则）
    ├── test_browser_tool.py              # BrowserTool（click 指纹 / wait / scroll 防护）
    ├── test_snapshot_generator.py        # SnapshotGenerator（selector 转义 / ID 生命周期）
    ├── test_regression_fixed_issues.py   # 已修复问题回归
    ├── test_agent_integration.py         # Agent 主循环 Mock 集成（含 LLM 驱动）
    ├── test_llm_client.py                # LLMClient 协议 / Mock / 错误分类
    ├── test_prompt_serialization.py      # Snapshot 序列化 / URL 脱敏 / 历史窗口
    └── test_llm_planner.py               # LLMPlanner 解析 / 一次修复 / 完整链路
```

## V0.2 规则驱动 Agent Loop

V0.2 在不接入 LLM 的前提下，打通「Snapshot → RuleBasedPlanner → Action → Executor → BrowserTool」闭环：

- **RuleBasedPlanner** — `parse_goal()` 解析自然语言目标（URL / 搜索词 / 点击目标 / 等待条件），配合 **8 条内置规则** 决策；`add_rule()` 支持注册自定义规则并优先执行。
- **定位协议** — Snapshot 元素带全局唯一 `element_id`；Action 通过 `target_id` 引用元素，或直接用 `params["selector"]` 精确定位（优先级：`params.selector` > `target_id` > `target` 语义回退）。
- **Action 参数校验** — `validate()` 覆盖每类动作的必填字段与参数类型（timeout 正整数、bool 参数、scroll direction/amount、wait ms、save_path 类型等），非法 Action 在到达浏览器前被拦截。
- **执行契约加固** — BrowserTool 所有方法统一返回 `Observation`；`wait()`/`scroll()` 非法参数防护、杜绝 JS 注入；Snapshot selector 经 CSS 转义、`element_id` 每次生成重置。

### 选择器优先级（`_build_selector`）

```text
data-testid > id > role > aria-label > 标签:has-text() > 标签名
```

ID 与属性值均经 CSS 转义，`e.g. #a\2e b\3a c`（原始 id 为 `a.b:c`）。

## V0.3 LLM 驱动 Agent Loop

V0.3 将 LLM 放在 Planner 位置：模型只能看到脱敏后的 Snapshot 摘要，并只能输出项目定义的 Action；真实浏览器操作仍由 Executor / BrowserTool 完成。

```text
Agent Loop
    ├─ Observer  → Snapshot → 序列化（脱敏/截断）
    ├─ LLMPlanner → Action（结构化、校验通过）
    └─ Executor  → BrowserTool → Playwright
```

- **LLMClient 抽象**（`agent/llm/base.py`）— 最小异步协议 `complete_json()`；错误分类：可重试（超时/网络/限流）与不可重试（内容错误）。
- **MockLLMClient**（`agent/llm/mock.py`）— 预设响应队列返回 dict、支持注入异常，测试与离线回归无需网络。
- **OpenAILLMClient**（`agent/llm/openai_client.py`）— OpenAI 兼容 Chat Completions 适配器；API Key 只从环境变量或显式参数读取。内置 **国内大模型预设**（见下表），也可用 `base_url` 指向任意 OpenAI 兼容端点（中转/自部署）。
- **LLMPlanner**（`agent/core/planner.py`）— Snapshot → 模型视图 → 提示词（含最近 5 条历史窗口）→ 模型输出 → 安全转换为已验证 Action；格式错误最多发起一次修复请求。
- **安全边界** — 模型上下文不包含 selector / HTML / Cookie / 截图；URL 中 token/session/code 等参数被掩码；模型伪造的 selector 一律忽略，selector 只由本地 Snapshot 映射注入；`target_id` 必须命中当前 Snapshot 元素，幻觉 ID 不会到达 Executor。
- **Agent 历史** — `run()` 通过 `plan_with_history()` 向 Planner 传入有限历史（RuleBasedPlanner 不受影响）。

### 国内大模型适配

国内主流大模型均提供 OpenAI 兼容端点，通过 `LLM_PROVIDER` 预设一键接入（也可用 `OPENAI_BASE_URL` 指向任意兼容地址）：

| provider | 厂商/模型 | base_url | API Key 环境变量 |
| :--- | :--- | :--- | :--- |
| `deepseek` | DeepSeek（deepseek-v4-flash） | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `moonshot` | Moonshot Kimi（kimi-k3） | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| `zhipu` | 智谱 GLM（glm-4.7-flash） | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| `qwen` | 阿里云百炼 通义千问（qwen3.8-max） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `doubao` | 火山方舟 豆包（推理接入点 ID） | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` |
| `ernie` | 百度千帆 文心（ernie-5.0） | `https://qianfan.baidubce.com/v2` | `QIANFAN_API_KEY` |
| `spark` | 讯飞星火（4.0Ultra） | `https://spark-api-open.xf-yun.com/v1` | `SPARK_API_KEY` |

参数优先级（高 → 低）：**显式参数 > provider 预设 > 通用环境变量（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`）> 内置默认**。厂商专属 Key 缺失时回退 `OPENAI_API_KEY`。

**本地 .env 配置**（可选）：不想每次设置环境变量时，可写 `.env` 文件（当前目录 `.env` 或 `~/.browserpilot/.env`，先项目级后用户级）。`KEY=VALUE` 格式，`#` 为注释，值两侧引号自动去除；已存在的环境变量优先，不会被文件覆盖；两个文件均不存在时行为与不配置完全一致（零依赖实现）。

```python
from agent.llm import OpenAILLMClient

# 一键接入 DeepSeek（模型/端点自动填充，Key 读 DEEPSEEK_API_KEY 或回退 OPENAI_API_KEY）
client = OpenAILLMClient(provider="deepseek")

# 中转/自部署：显式 base_url 覆盖预设，模型名可用 OPENAI_MODEL 覆盖
client = OpenAILLMClient(provider="deepseek", base_url="https://my-proxy/v1")
```

> 豆包（`doubao`）的 model 参数是方舟控制台的**推理接入点 ID**，需先在火山方舟创建，再通过 `OPENAI_MODEL` 指定。

### LLM Demo

```bash
pip install -e ".[llm]"                 # 安装 openai 依赖

# 方式一：OpenAI 或任意 OpenAI 兼容端点
$env:OPENAI_API_KEY = "sk-xxx"          # 配置 API Key（PowerShell）

# 方式二：国内大模型（以 DeepSeek 为例）
$env:LLM_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "sk-xxx"

python examples/llm_agent_demo.py "打开 <本地搜索页> 查找 北京时间"
```

Demo 运行在本地受控搜索页（`search_page.html`），不涉及外网导航等高危操作；未配置 API Key 时给出明确提示，不发起任何请求。

## 快速开始

```bash
# 安装依赖（含 dev 依赖以运行测试）
pip install -e ".[dev]"

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

| 版本 | 目标 | 状态 |
| :--- | :--- | :--- |
| V0.1 | 执行层：Browser Tool + Snapshot + Observation + Schema | ✅ 完成 |
| V0.2 | Agent Loop：规则驱动 Planner + 执行契约加固 | ✅ 完成 |
| V0.3 | 接入 LLM：LLM Planner（LLMClient 抽象 + LLMPlanner + 安全序列化 + OpenAI Provider） | ✅ 完成 |
| V0.4 | Reflection：错误恢复与重试 | 📋 待开始 |
| V0.5 | Memory：历史操作与上下文记忆 | 📋 待开始 |
| V1.0 | 完整 Agentic RPA：登录/查询/下载/上传/Excel 处理 | 🎯 规划中 |
