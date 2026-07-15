"""
Executor 单元测试

测试 Action 到 Observation 的转换逻辑，
不依赖实际浏览器（Mock BrowserTool）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.executor import Executor
from agent.schema.action import Action, click, goto, input_text
from agent.schema.observation import Observation


@pytest.fixture
def mock_tool():
    """创建 Mock BrowserTool"""
    tool = MagicMock()
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.goto = AsyncMock(
        return_value=Observation.ok(url="https://example.com", title="Example")
    )
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="Example")
    return tool


@pytest.mark.asyncio
async def test_execute_click(mock_tool):
    executor = Executor(mock_tool)
    obs = await executor.execute(click("登录按钮"))
    assert obs.success is True
    mock_tool.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_goto(mock_tool):
    executor = Executor(mock_tool)
    obs = await executor.execute(goto("https://example.com"))
    assert obs.success is True
    mock_tool.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_input(mock_tool):
    executor = Executor(mock_tool)
    obs = await executor.execute(input_text("搜索框", "hello"))
    assert obs.success is True
    mock_tool.input.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_action(mock_tool):
    executor = Executor(mock_tool)
    obs = await executor.execute(Action(action="unknown"))
    assert obs.is_error is True
    assert "未知动作" in obs.error
