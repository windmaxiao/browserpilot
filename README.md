# BrowserPilot

基于 Playwright 的自主网页操作 Agent 框架。

> 让大模型像人一样操作浏览器 — 理解网页、规划操作、执行任务。

## 项目结构

```
browserpilot/
├── agent-rpa/              # Agentic RPA 核心实现 ← 所有代码在此
│   ├── agent/              #   Agent 框架
│   │   ├── core/           #     主循环 / 执行器 / 观察者 / 规划器
│   │   ├── browser/        #     Playwright 封装 + Snapshot 生成
│   │   ├── schema/         #     数据模型层
│   │   ├── llm/            #     (预留) LLM 模块
│   │   ├── prompts/        #     (预留) 提示词模板
│   │   └── tools/          #     (预留) 辅助工具
│   ├── examples/           #     示例 Demo
│   └── tests/              #     单元测试
│
├── Agentic_RPA_项目规划_V0.1.md  # 项目规划与架构文档
├── LICENSE
└── README.md               # ← 当前文件（项目入口）
```

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
Snapshot → Browser Tool
  │     │
  └──┬──┘
     ▼
Playwright
```

### 核心原则

1. **LLM 不知道 Playwright** — LLM 输出高层级 Action，Playwright 只是执行器
2. **Browser Tool 与 Snapshot 分离** — Snapshot 描述页面，Tool 执行操作
3. **高内聚、低耦合** — 便于未来替换 Selenium、Appium 等执行器

## 快速开始

```bash
cd agent-rpa

# 安装依赖
pip install -e .

# 安装 Playwright 浏览器
playwright install chromium

# 运行 Demo
python examples/manual_demo.py

# 运行测试
pytest
```

> 详细文档见 [agent-rpa/README.md](agent-rpa/README.md)

## 开发路线

| 版本 | 目标 | 状态 |
|------|------|------|
| V0.1 | 执行层：Browser Tool + Snapshot + Observation + Schema | ✅ 完成 |
| V0.2 | Agent Loop：规则驱动 Planner | ⏳ 进行中 |
| V0.3 | 接入 LLM：LLM Planner | 📋 待开始 |
| V0.4 | Reflection：错误恢复与重试 | 📋 待开始 |
| V0.5 | Memory：历史操作与上下文记忆 | 📋 待开始 |
| V1.0 | 完整 Agentic RPA：登录/查询/下载/上传/Excel 处理 | 🎯 规划中 |

## 授权

[Apache License 2.0](LICENSE)
