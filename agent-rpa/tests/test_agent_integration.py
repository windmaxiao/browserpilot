"""
Agent Mock 集成测试（V0.2 阶段 C2 + V0.3 阶段 D）

采用 Mock Observer / Planner / BrowserTool，验证 Agent.run() 完整链路：
Observe → Plan → Execute → Record，以及 done / 执行失败 / 非法 Action 分支；
V0.3 起 Agent 通过 plan_with_history 向 Planner 传入有限历史。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.planner import LLMPlanner
from agent.llm import MockLLMClient
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import ElementInfo, Snapshot


class _PlannerStub:
    """记录 plan_with_history 调用并依次返回预设 Action 的桩 Planner。"""

    def __init__(self, actions=None):
        self._actions = list(actions) if actions else []
        self.calls: list[dict] = []

    async def plan_with_history(self, snapshot, goal, history):
        self.calls.append({"goal": goal, "history_len": len(history)})
        return self._actions.pop(0) if self._actions else None


def make_mocks(actions=None):
    """构造 Observer/Planner/BrowserTool 的 Mock 组合，返回 (agent, planner, tool)"""
    snapshot = Snapshot(title="测试页", url="https://example.com")

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    planner = _PlannerStub(actions)

    tool = MagicMock()
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="测试页")

    executor = Executor(tool)
    agent = Agent(observer, planner, executor, max_steps=10)
    return agent, planner, tool


@pytest.mark.asyncio
async def test_three_step_success_flow():
    """输入框 → 提交按钮 → done 的三步成功流程"""
    agent, planner, tool = make_mocks([
        Action(action="input", value="北京时间",
               params={"selector": "#search-box"}),
        Action(action="click", params={"selector": "#search-btn"}),
        done("任务完成"),
    ])

    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    # done 不进入执行链，history 只记录两步
    assert len(agent.history) == 2
    assert agent.current_step == 3
    tool.input.assert_awaited_once_with(
        "#search-box", "北京时间", timeout=5000, clear_first=True
    )
    tool.click.assert_awaited_once_with("#search-btn", timeout=5000, force=False)


@pytest.mark.asyncio
async def test_planner_receives_growing_history():
    """plan_with_history 每步收到逐步增长的 history"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#a"}),
        done(),
    ])

    await agent.run("任务")

    assert [c["history_len"] for c in planner.calls] == [0, 1]


@pytest.mark.asyncio
async def test_selector_only_action_reaches_tool():
    """Planner 输出 selector-only Action 后由 Executor 正确调用 BrowserTool"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#submit"}),
        done(),
    ])

    obs = await agent.run("点击 提交")

    assert obs.success is True
    tool.click.assert_awaited_once_with("#submit", timeout=5000, force=False)


@pytest.mark.asyncio
async def test_invalid_action_blocked_before_tool():
    """非法 Action 被 validate 拦截，不会调用 BrowserTool"""
    agent, planner, tool = make_mocks([
        Action(action="click"),   # 无 target/target_id/selector → 非法
        done(),
    ])

    obs = await agent.run("随便")

    assert obs.is_error is True
    assert "验证失败" in obs.error
    tool.click.assert_not_awaited()
    assert len(agent.history) == 1


@pytest.mark.asyncio
async def test_tool_failure_stops_agent_and_records():
    """BrowserTool 返回失败 Observation 时 Agent 停止并记录 history"""
    agent, planner, tool = make_mocks([
        Action(action="input", value="x", params={"selector": "#kw"}),
        done(),  # 不应被执行
    ])
    tool.input = AsyncMock(return_value=Observation.fail("输入失败"))

    obs = await agent.run("查找 x")

    assert obs.is_error is True
    assert "输入失败" in obs.error
    assert agent.current_step == 1
    assert len(agent.history) == 1
    assert agent.history[0]["action"].action == "input"
    assert agent.history[0]["observation"].is_error is True


@pytest.mark.asyncio
async def test_max_steps_exceeded():
    """超过最大步数时返回失败 Observation"""
    agent, planner, tool = make_mocks(
        [Action(action="click", params={"selector": "#btn"})] * 12
    )  # max_steps=10，会超限

    obs = await agent.run("任务")

    assert obs.is_error is True
    assert "超出最大步数" in obs.error
    assert agent.current_step == 10
    assert len(agent.history) == 10


# ── V0.3 阶段 D：Mock LLM 驱动 Agent 多步流程 ─────────────────────────

def _search_snapshot() -> Snapshot:
    return Snapshot(
        title="搜索页",
        url="https://example.com/search",
        page_type="search",
        inputs=[ElementInfo(text="", element_id="e0", element_type="textbox",
                            selector="#kw", placeholder="搜索", aria_label="搜索")],
        buttons=[ElementInfo(text="搜索", element_id="e1", element_type="button",
                             selector="#btn")],
    )


@pytest.mark.asyncio
async def test_llm_planner_drives_agent_flow():
    """Mock LLM 驱动 Agent 完成 input → click → done 多步流程"""
    snapshot = _search_snapshot()
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    client = MockLLMClient([
        {"action": "input", "target_id": "e0", "value": "北京时间"},
        {"action": "click", "target_id": "e1"},
        {"action": "done"},
    ])
    planner = LLMPlanner(client, model="mock", timeout=1000)

    tool = MagicMock()
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.current_url = "https://example.com/search"
    tool.current_title = AsyncMock(return_value="搜索页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    tool.input.assert_awaited_once()
    tool.click.assert_awaited_once()
    # 恰好 3 次 LLM 调用，无多余请求
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_llm_planner_repair_recovers_inside_agent():
    """格式错误经一次修复恢复后，Agent 流程继续"""
    snapshot = _search_snapshot()
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    client = MockLLMClient([
        {"action": "click", "target_id": "e99"},   # 幻觉 ID → 修复
        {"action": "click", "target_id": "e1"},
        {"action": "done"},
    ])
    planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)

    tool = MagicMock()
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.current_url = "https://example.com/search"
    tool.current_title = AsyncMock(return_value="搜索页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("点击 搜索")

    assert obs.success is True
    tool.click.assert_awaited_once()
    assert client.call_count == 3
