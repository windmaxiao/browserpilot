"""
Executor 单元测试

测试 Action 到 Observation 的转换逻辑，
不依赖实际浏览器（Mock BrowserTool）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.executor import Executor, _HTML_TAGS
from agent.schema.action import Action, click, goto, input_text, scroll
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
    tool.select = AsyncMock(return_value=Observation.ok())
    tool.scroll = AsyncMock(return_value=Observation.ok())
    tool.download = AsyncMock(
        return_value=Observation.ok(data={"download_path": "out.csv"})
    )
    tool.upload = AsyncMock(return_value=Observation.ok())
    tool.back = AsyncMock(return_value=Observation.ok())
    tool.refresh = AsyncMock(return_value=Observation.ok())
    tool.screenshot = AsyncMock(
        return_value=Observation.ok(data={"screenshot_base64": "fake"})
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
    async def test_execute_frame_path_kept_with_injected_selector(self):
        """回归 #1：parse_action_dict 注入的 params.selector 不得把 iframe 元素 frame_path 降级为主页面"""
        tool = MagicMock()
        tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
        tool.current_url = "https://example.com"
        tool.current_title = AsyncMock(return_value="Example")

        exec = Executor(tool)
        snap = MagicMock()
        # iframe 内元素：frame_path 非空
        el = MagicMock(
            element_id="e9",
            selector="#l2-btn",
            frame_path=("#frame-level1", "#frame-level2"),
        )
        snap.get_interactive_elements.return_value = [el]

        # 模拟 LLM 链路（parse_action_dict）：target_id 命中 + 注入本地 selector
        action = Action(
            action="click",
            target_id="e9",
            params={"selector": "#l2-btn"},
        )
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.click.assert_awaited_once_with(
            "#l2-btn",
            timeout=5000,
            force=False,
            frame_path=("#frame-level1", "#frame-level2"),
        )

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


# ── C4: 补 7 个未测试处理器（select/scroll/download/upload/back/refresh/screenshot）──

class TestExtraHandlers:
    """select/scroll/download/upload/back/refresh/screenshot 处理器"""

    @pytest.mark.asyncio
    async def test_execute_select(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="select", target="城市", value="北京"))
        assert obs.success is True
        mock_tool.select.assert_awaited_once_with(
            ':has-text("城市")', "北京", timeout=5000, frame_path=()
        )

    @pytest.mark.asyncio
    async def test_execute_select_missing_value_fails(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="select", target="城市"))
        assert obs.is_error is True
        assert "value" in obs.error
        mock_tool.select.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_scroll(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(scroll("bottom", 0))
        assert obs.success is True
        mock_tool.scroll.assert_awaited_once_with(direction="bottom", amount=0)

    @pytest.mark.asyncio
    async def test_execute_scroll_defaults(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="scroll"))
        assert obs.success is True
        mock_tool.scroll.assert_awaited_once_with(direction="down", amount=300)

    @pytest.mark.asyncio
    async def test_execute_download_strips_save_path(self, mock_tool):
        """待解决问题 #3：save_path 无条件剥离——即便未配置 download_dir，
        手动构造 Action 携带的 save_path 也不得透传（落盘位置只由调用方决定）"""
        executor = Executor(mock_tool)
        action = Action(action="download", target="导出",
                        params={"save_path": "C:/evil/out.csv"})
        obs = await executor.execute(action)
        assert obs.success is True
        mock_tool.download.assert_awaited_once_with(
            ':has-text("导出")',
            save_path=None,
            download_dir=None,
            timeout=30000,
            frame_path=(),
        )

    @pytest.mark.asyncio
    async def test_execute_download_uses_configured_download_dir(self, mock_tool):
        """#3/#5：配置 download_dir 时作为目录传给 BrowserTool，且忽略 action.save_path"""
        executor = Executor(mock_tool, download_dir="C:/downloads")
        # 即便手动构造 Action 携带 save_path，download_dir 也应优先（调用方策略）
        action = Action(action="download", target="导出", params={"save_path": "evil.csv"})
        obs = await executor.execute(action)
        assert obs.success is True
        mock_tool.download.assert_awaited_once_with(
            ':has-text("导出")', save_path=None, download_dir="C:/downloads",
            timeout=30000, frame_path=(),
        )

    @pytest.mark.asyncio
    async def test_execute_download_missing_target_fails(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="download"))
        assert obs.is_error is True
        mock_tool.download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_upload(self, mock_tool):
        """M5：配置允许上传目录后，目录内路径正常透传"""
        executor = Executor(mock_tool, allowed_upload_dirs=["C:/tmp"])
        action = Action(action="upload", target="上传", value="C:/tmp/a.txt")
        obs = await executor.execute(action)
        assert obs.success is True
        mock_tool.upload.assert_awaited_once_with(
            ':has-text("上传")', "C:/tmp/a.txt", timeout=10000, frame_path=()
        )

    @pytest.mark.asyncio
    async def test_execute_upload_rejected_without_allowed_dirs(self, mock_tool):
        """M5：未配置允许上传目录 → upload 一律拒绝"""
        executor = Executor(mock_tool)
        action = Action(action="upload", target="上传", value="C:/tmp/a.txt")
        obs = await executor.execute(action)
        assert obs.is_error is True
        assert "越权" in obs.error
        mock_tool.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_upload_path_outside_allowed_fails(self, mock_tool):
        """M5：value 位于允许目录之外 → 拒绝"""
        executor = Executor(mock_tool, allowed_upload_dirs=["D:/safe"])
        action = Action(action="upload", target="上传", value="C:/tmp/a.txt")
        obs = await executor.execute(action)
        assert obs.is_error is True
        assert "越权" in obs.error
        mock_tool.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_upload_missing_value_fails(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="upload", target="上传"))
        assert obs.is_error is True
        assert "value" in obs.error
        mock_tool.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_back(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="back"))
        assert obs.success is True
        mock_tool.back.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_refresh(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="refresh"))
        assert obs.success is True
        mock_tool.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_screenshot_default_full_page(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(Action(action="screenshot"))
        assert obs.success is True
        mock_tool.screenshot.assert_awaited_once_with(full_page=True)

    @pytest.mark.asyncio
    async def test_execute_screenshot_full_page_false(self, mock_tool):
        executor = Executor(mock_tool)
        obs = await executor.execute(
            Action(action="screenshot", params={"full_page": False})
        )
        assert obs.success is True
        mock_tool.screenshot.assert_awaited_once_with(full_page=False)


class TestExtraHandlersFramePath:
    """select/download/upload 处理器对 iframe frame_path 的透传（target_id 命中）"""

    @pytest.mark.asyncio
    async def test_execute_select_with_frame_path(self):
        tool = MagicMock()
        tool.select = AsyncMock(return_value=Observation.ok())
        exec = Executor(tool)
        snap = MagicMock()
        el = MagicMock(
            element_id="e5", selector="select#province", frame_path=("#outer",)
        )
        snap.get_interactive_elements.return_value = [el]

        action = Action(action="select", target_id="e5", value="广东")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.select.assert_awaited_once_with(
            "select#province", "广东", timeout=5000, frame_path=("#outer",)
        )

    @pytest.mark.asyncio
    async def test_execute_upload_with_frame_path(self):
        tool = MagicMock()
        tool.upload = AsyncMock(return_value=Observation.ok())
        exec = Executor(tool, allowed_upload_dirs=["C:/tmp"])
        snap = MagicMock()
        el = MagicMock(
            element_id="e6", selector="#file-input", frame_path=("#outer",)
        )
        snap.get_interactive_elements.return_value = [el]

        action = Action(action="upload", target_id="e6", value="C:/tmp/a.txt")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.upload.assert_awaited_once_with(
            "#file-input", "C:/tmp/a.txt", timeout=10000, frame_path=("#outer",)
        )

    @pytest.mark.asyncio
    async def test_execute_download_with_frame_path(self):
        tool = MagicMock()
        tool.download = AsyncMock(
            return_value=Observation.ok(data={"download_path": "out.csv"})
        )
        exec = Executor(tool)
        snap = MagicMock()
        el = MagicMock(
            element_id="e7", selector="#dl-btn", frame_path=("#outer", "#inner")
        )
        snap.get_interactive_elements.return_value = [el]

        action = Action(action="download", target_id="e7")
        obs = await exec.execute(action, snapshot=snap)

        assert obs.success is True
        tool.download.assert_awaited_once_with(
            "#dl-btn",
            save_path=None,
            download_dir=None,
            timeout=30000,
            frame_path=("#outer", "#inner"),
        )
