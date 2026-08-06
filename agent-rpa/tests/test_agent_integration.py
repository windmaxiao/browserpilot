"""
Agent Mock 集成测试（V0.2 阶段 C2）

采用 Mock Observer / Planner / BrowserTool，验证 Agent.run() 完整链路：
Observe → Plan → Execute → Record，以及 done / 执行失败 / 非法 Action 分支。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot


def make_mocks():
    """构造 Observer/Planner/BrowserTool 的 Mock 组合，返回 (agent, planner, tool)"""
    snapshot = Snapshot(title="测试页", url="https://example.com")

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    planner = MagicMock()
    planner.plan = AsyncMock()

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
    agent, planner, tool = make_mocks()
    planner.plan.side_effect = [
        Action(action="input", value="北京时间",
               params={"selector": "#search-box"}),
        Action(action="click", params={"selector": "#search-btn"}),
        done("任务完成"),
    ]

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
async def test_selector_only_action_reaches_tool():
    """Planner 输出 selector-only Action 后由 Executor 正确调用 BrowserTool"""
    agent, planner, tool = make_mocks()
    planner.plan.side_effect = [
        Action(action="click", params={"selector": "#submit"}),
        done(),
    ]

    obs = await agent.run("点击 提交")

    assert obs.success is True
    tool.click.assert_awaited_once_with("#submit", timeout=5000, force=False)


@pytest.mark.asyncio
async def test_invalid_action_blocked_before_tool():
    """非法 Action 被 validate 拦截，不会调用 BrowserTool"""
    agent, planner, tool = make_mocks()
    planner.plan.side_effect = [
        Action(action="click"),   # 无 target/target_id/selector → 非法
        done(),
    ]

    obs = await agent.run("随便")

    assert obs.is_error is True
    assert "验证失败" in obs.error
    tool.click.assert_not_awaited()
    assert len(agent.history) == 1


@pytest.mark.asyncio
async def test_tool_failure_stops_agent_and_records():
    """BrowserTool 返回失败 Observation 时 Agent 停止并记录 history"""
    agent, planner, tool = make_mocks()
    tool.input = AsyncMock(return_value=Observation.fail("输入失败"))
    planner.plan.side_effect = [
        Action(action="input", value="x", params={"selector": "#kw"}),
        done(),  # 不应被执行
    ]

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
    agent, planner, tool = make_mocks()
    planner.plan.side_effect = [
        Action(action="click", params={"selector": "#btn"})
    ] * 12  # max_steps=10，会超限

    obs = await agent.run("任务")

    assert obs.is_error is True
    assert "超出最大步数" in obs.error
    assert agent.current_step == 10
    assert len(agent.history) == 10
