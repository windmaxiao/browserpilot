"""
Executor 单元测试

测试 Action 到 Observation 的转换逻辑，
不依赖实际浏览器（Mock BrowserTool）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.executor import Executor, _HTML_TAGS
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


# ── _resolve_selector 标签名识别 ──────────────────────────────────

class TestResolveSelector:
    """Executor._resolve_selector 的标签名识别逻辑"""

    @pytest.mark.parametrize("tag", sorted(_HTML_TAGS))
    def test_tag_name_passthrough(self, tag):
        """已知 HTML 标签名应直接作为 CSS 选择器透传"""
        result = Executor._resolve_selector(tag, {})
        assert result == tag, f"标签 {tag} 被误包装为: {result}"

    def test_none_tag_not_passthrough(self):
        """非标签名 target 仍走 :has-text() fallback"""
        result = Executor._resolve_selector("登录按钮", {})
        assert result == ':has-text("登录按钮")'

    def test_input_tag_not_wrapped(self):
        """核心场景: input 不应变成 :has-text('input')"""
        result = Executor._resolve_selector("input", {})
        assert result == "input"
        assert "has-text" not in result

    def test_selector_param_still_highest_priority(self):
        """params.selector 优先级仍高于标签名识别"""
        result = Executor._resolve_selector("input", {"selector": "#search-box"})
        assert result == "#search-box"

    def test_css_prefix_still_higher_than_tag(self):
        """CSS 前缀 (# . [ :) 优先级仍高于标签名识别"""
        # 就算 target 是标签名，有 CSS 前缀也应透传
        result = Executor._resolve_selector("#input", {})
        assert result == "#input"

    def test_target_id_with_params_selector(self):
        """target_id + params.selector 联合使用场景"""
        # 模拟 Planner 从 Snapshot 获取元素后构造 Action
        action = Action(
            action="click",
            target="登录",
            target_id="e0",
            params={"selector": "button:has-text(\"登录\")"},
        )
        selector = Executor._resolve_selector(action.target, action.params)
        assert selector == 'button:has-text("登录")'
        assert action.target_id == "e0"
        assert action.is_valid()
