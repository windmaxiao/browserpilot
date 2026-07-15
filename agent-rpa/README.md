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

### 目录结构

```
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
│   ├── manual_demo.py      # 手动模式 Demo
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

# 运行 Demo
python examples/manual_demo.py

# 运行测试
pytest
```

## 开发路线

| 版本 | 目标 |
|------|------|
| V0.1 | 执行层：Browser Tool + Snapshot + Observation + Schema |
| V0.2 | Agent Loop：规则驱动 Planner |
| V0.3 | 接入 LLM：LLM Planner |
| V0.4 | Reflection：错误恢复与重试 |
| V0.5 | Memory：历史操作与上下文记忆 |
| V1.0 | 完整 Agentic RPA：登录/查询/下载/上传/Excel 处理 |
