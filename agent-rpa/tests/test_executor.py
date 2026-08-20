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
    tool.wait = AsyncMock(return_value=Observation.ok())
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


@pytest.mark.asyncio
async def test_execute_wait_value_as_ms(mock_tool):
    """LLM 常用 value 表达等待毫秒数（如 5000）→ 透传为 ms"""
    executor = Executor(mock_tool)
    obs = await executor.execute(Action(action="wait", value="5000"))
    assert obs.success is True
    mock_tool.wait.assert_awaited_once_with(ms=5000)


@pytest.mark.asyncio
async def test_execute_wait_defaults_to_1000ms(mock_tool):
    """无 value / params.ms → 默认 1000ms"""
    executor = Executor(mock_tool)
    obs = await executor.execute(Action(action="wait"))
    assert obs.success is True
    mock_tool.wait.assert_awaited_once_with(ms=1000)


@pytest.mark.asyncio
async def test_execute_wait_params_ms_wins(mock_tool):
    """params.ms 优先于 value"""
    executor = Executor(mock_tool)
    obs = await executor.execute(Action(action="wait", value="5000", params={"ms": 2000}))
    assert obs.success is True
    mock_tool.wait.assert_awaited_once_with(ms=2000)


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


# ── target_id 解析测试 ──────────────────────────────────────────

class TestTargetIdResolution:
    """Executor 通过 target_id 从 Snapshot 解析选择器的逻辑"""

    def _make_snapshot_with_elements(self):
        """创建一个包含已知 element_id 的 mock Snapshot"""
        from unittest.mock import MagicMock

        snap = MagicMock()
        el0 = MagicMock(element_id="e0", selector="button:has-text(\"登录\")")
        el1 = MagicMock(element_id="e1", selector="#username")
        el2 = MagicMock(element_id="e2", selector="select#city")
        snap.get_interactive_elements.return_value = [el0, el1, el2]
        return snap

    def test_build_element_map(self):
        """_build_element_map 正确构建 element_id → selector 映射"""
        exec = Executor(MagicMock())
        snap = self._make_snapshot_with_elements()
        mapping = exec._build_element_map(snap)

        assert len(mapping) == 3
        assert mapping["e0"] == 'button:has-text("登录")'
        assert mapping["e1"] == "#username"
        assert mapping["e2"] == "select#city"

    def test_resolve_target_by_id(self):
        """target_id 从 element_map 正确解析出 selector"""
        exec = Executor(MagicMock())
        exec._element_map = {"e0": 'button:has-text("登录")'}

        action = Action(action="click", target_id="e0")
        selector = exec._resolve_target(action)
        assert selector == 'button:has-text("登录")'

    def test_resolve_target_params_selector_highest_priority(self):
        """params.selector 优先级高于 target_id"""
        exec = Executor(MagicMock())
        exec._element_map = {"e0": 'button:has-text("旧按钮")'}

        action = Action(
            action="click",
            target_id="e0",
            params={"selector": 'button:has-text("新按钮")'},
        )
        selector = exec._resolve_target(action)
        # 应返回 params.selector 而非从 element_map 解析
        assert selector == 'button:has-text("新按钮")'

    def test_resolve_target_id_priority_over_target(self):
        """target_id 优先级高于 target"""
        exec = Executor(MagicMock())
        exec._element_map = {"e0": "#exact-btn"}

        action = Action(
            action="click",
            target="模糊描述",
            target_id="e0",
        )
        selector = exec._resolve_target(action)
        # 应返回 target_id 解析结果
        assert selector == "#exact-btn"

    def test_resolve_target_target_fallback(self):
        """无 target_id 时使用 target 语义解析"""
        exec = Executor(MagicMock())
        exec._element_map = {}

        action = Action(action="click", target="登录按钮")
        selector = exec._resolve_target(action)
        assert selector == ':has-text("登录按钮")'

    def test_resolve_target_not_found_returns_none(self):
        """target_id 在 element_map 中不存在时返回 None"""
        exec = Executor(MagicMock())
        exec._element_map = {"e0": "#btn"}

        action = Action(action="click", target_id="e999")
        selector = exec._resolve_target(action)
        assert selector is None

    def test_resolve_target_no_target_no_target_id(self):
        """既无 target 也无 target_id 时返回 None"""
        exec = Executor(MagicMock())
        action = Action(action="click")
        selector = exec._resolve_target(action)
        assert selector is None

    @pytest.mark.asyncio
    async def test_execute_click_with_target_id(self):
        """通过 target_id 执行 click"""
        tool = MagicMock()
        tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
        tool.current_url = "https://example.com"
        tool.current_title = AsyncMock(return_value="Example")

        exec = Executor(tool)
        snap = self._make_snapshot_with_elements()

        action = Action(action="click", target_id="e0")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.click.assert_awaited_once_with(
            'button:has-text("登录")',
            timeout=5000,
            force=False,
            frame_path=(),
        )

    @pytest.mark.asyncio
    async def test_execute_input_with_target_id(self):
        """通过 target_id 执行 input"""
        tool = MagicMock()
        tool.input = AsyncMock(return_value=Observation.ok())
        tool.current_url = "https://example.com"
        tool.current_title = AsyncMock(return_value="Example")

        exec = Executor(tool)
        snap = self._make_snapshot_with_elements()

        action = Action(action="input", target_id="e1", value="hello")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.input.assert_awaited_once_with(
            "#username",
            "hello",
            timeout=5000,
            clear_first=True,
            frame_path=(),
        )

    @pytest.mark.asyncio
    async def test_execute_with_stale_target_id(self):
        """target_id 在 Snapshot 中不存在时返回 fail"""
        tool = MagicMock()
        exec = Executor(tool)
        snap = self._make_snapshot_with_elements()

        action = Action(action="click", target_id="e999")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.is_error is True
        assert "未找到" in obs.error
        assert "e999" in obs.error

    @pytest.mark.asyncio
    async def test_execute_without_snapshot_uses_target_fallback(self):
        """不传 snapshot 时仍能通过 target 正常执行"""
        tool = MagicMock()
        tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
        exec = Executor(tool)

        action = Action(action="click", target="登录按钮")
        obs = await exec.execute(action)  # 不传 snapshot

        assert obs.success is True
        tool.click.assert_awaited_once()
        # 验证使用了 :has-text() fallback
        args, _ = tool.click.call_args
        assert 'has-text' in args[0] or ':' in args[0]
