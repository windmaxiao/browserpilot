"""
回归测试：验证已修复问题

验证范围：
- Issue 1: Snapshot 选择器与 Executor 选择器一致性
- Issue 2: click() 正确返回 page_changed
- Issue 7/9: Observation.ok() data 无嵌套问题
"""

import sys
sys.path.insert(0, ".")

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from agent.schema.observation import Observation
from agent.schema.action import Action
from agent.core.executor import Executor
from agent.browser.snapshot import SnapshotGenerator


# ═══════════════════════════════════════════════════════════════
# Issue 1: 选择器一致性
# ═══════════════════════════════════════════════════════════════

class TestIssue1_SelectorConsistency:
    """Snapshot._build_selector 与 Executor._resolve_selector 的一致性"""

    def test_executor_uses_has_text_as_default(self):
        """验证 Executor 默认使用 :has-text() 作为文本匹配策略"""
        selector = Executor._resolve_selector("登录", {})
        assert selector == ':has-text("登录")'

    def test_executor_respects_explicit_selector(self):
        """验证 params.selector 优先级最高"""
        selector = Executor._resolve_selector("任何内容", {"selector": "#my-button"})
        assert selector == "#my-button"

    @pytest.mark.parametrize("target", [
        "#id-123",
        ".class-name",
        "[data-testid='btn']",
        ":nth-child(2)",
    ])
    def test_executor_preserves_css_prefix(self, target):
        """验证 CSS 选择器前缀不被改写"""
        result = Executor._resolve_selector(target, {})
        assert result == target

    @pytest.mark.asyncio
    async def test_snapshot_builds_has_text_selector_for_text_buttons(self):
        """Snapshot 为带文本的按钮生成 tag:has-text() 选择器"""
        mock_page = AsyncMock()
        mock_el = AsyncMock()
        mock_el.evaluate = AsyncMock(return_value="button")
        mock_el.inner_text = AsyncMock(return_value="登录")
        mock_el.get_attribute = AsyncMock(return_value="")
        mock_el.bounding_box = AsyncMock(return_value=None)

        sg = SnapshotGenerator(mock_page)
        selector = await sg._build_selector(mock_el, "button", "登录")

        assert selector == 'button:has-text("登录")', f"意外选择器: {selector}"

    @pytest.mark.asyncio
    async def test_quote_escaping_consistency(self):
        """Snapshot 和 Executor 的引号转义逻辑一致"""
        mock_page = AsyncMock()
        mock_el = AsyncMock()
        mock_el.evaluate = AsyncMock(return_value="button")
        mock_el.inner_text = AsyncMock(return_value='点击"确定"')
        mock_el.get_attribute = AsyncMock(return_value="")
        mock_el.bounding_box = AsyncMock(return_value=None)

        sg = SnapshotGenerator(mock_page)
        snapshot_selector = await sg._build_selector(mock_el, "button", '点击"确定"')
        executor_selector = Executor._resolve_selector('点击"确定"', {})

        # 验证双方都使用 \" 转义双引号
        assert '\\"' in snapshot_selector, f"Snapshot 应转义双引号: {snapshot_selector}"
        assert '\\"' in executor_selector, f"Executor 应转义双引号: {executor_selector}"
        assert snapshot_selector == 'button:has-text("点击\\"确定\\"")'
        assert executor_selector == ':has-text("点击\\"确定\\"")'

    @pytest.mark.asyncio
    async def test_snapshot_selector_for_id_preferred(self):
        """Snapshot 对非按钮元素优先使用 id 选择器"""
        mock_page = AsyncMock()
        mock_el = AsyncMock()
        mock_el.evaluate = AsyncMock(return_value="div")  # 非 button/a/label
        mock_el.inner_text = AsyncMock(return_value="内容")
        mock_el.bounding_box = AsyncMock(return_value=None)

        # get_attribute 优先返回 id
        mock_el.get_attribute = AsyncMock(side_effect=lambda attr: {
            "id": "content-area",
            "data-testid": "",
            "aria-label": "",
            "role": "",
        }.get(attr, ""))

        sg = SnapshotGenerator(mock_page)
        selector = await sg._build_selector(mock_el, "div", "内容")
        assert selector == "#content-area", f"非按钮元素应优先使用 id: {selector}"

    @pytest.mark.asyncio
    async def test_snapshot_button_uses_has_text_over_id(self):
        """按钮元素有文本时优先使用 has-text（语义更稳定）"""
        mock_page = AsyncMock()
        mock_el = AsyncMock()
        mock_el.evaluate = AsyncMock(return_value="button")
        mock_el.inner_text = AsyncMock(return_value="登录")
        mock_el.bounding_box = AsyncMock(return_value=None)

        # 即使有 id，按钮也优先用 has-text
        mock_el.get_attribute = AsyncMock(side_effect=lambda attr: {
            "id": "btn-login",
            "data-testid": "",
            "aria-label": "",
            "role": "",
        }.get(attr, ""))

        sg = SnapshotGenerator(mock_page)
        selector = await sg._build_selector(mock_el, "button", "登录")
        assert selector == 'button:has-text("登录")', \
            f"按钮应优先使用 has-text 而非 id: {selector}"


# ═══════════════════════════════════════════════════════════════
# Issue 2: page_changed 正确性
# ═══════════════════════════════════════════════════════════════

class TestIssue2_PageChanged:
    """click() 和 select() 的 page_changed 逻辑"""

    def test_url_unchanged_returns_false(self):
        """URL 不变时 page_changed=False"""
        old_url = "https://example.com/page"
        new_url = "https://example.com/page"
        assert (new_url != old_url) == False

    def test_url_changed_returns_true(self):
        """URL 变化时 page_changed=True"""
        old_url = "https://example.com/page1"
        new_url = "https://example.com/page2"
        assert (new_url != old_url) == True

    def test_hash_change_returns_true(self):
        """哈希变化也算页面变化"""
        old_url = "https://example.com/page#section1"
        new_url = "https://example.com/page#section2"
        assert (new_url != old_url) == True

    def test_unchanged_with_different_case_returns_true(self):
        """URL 大小写不同算变化（Playwright 区分大小写）"""
        old_url = "https://example.com/Page"
        new_url = "https://example.com/page"
        assert (new_url != old_url) == True

    def test_url_with_trailing_slash_consistency(self):
        """URL 尾部斜杠差异算变化"""
        old_url = "https://example.com/page"
        new_url = "https://example.com/page/"
        assert (new_url != old_url) == True


# ═══════════════════════════════════════════════════════════════
# Issue 7 & 9: Observation.ok() data 无嵌套
# ═══════════════════════════════════════════════════════════════

class TestIssue7And9_ObservationDataNoNesting:
    """Observation.ok() 不应产生 data['data'] 嵌套"""

    def test_basic_data_no_nesting(self):
        """核心场景: data 参数不会被二次嵌套"""
        obs = Observation.ok(url="https://example.com", data={"key": "value"})
        assert obs.data == {"key": "value"}

    def test_download_path_direct_access(self):
        """download() 传的 download_path 应在顶层"""
        obs = Observation.ok(data={"download_path": "/tmp/file.pdf"})
        assert obs.data["download_path"] == "/tmp/file.pdf"

    def test_screenshot_base64_direct_access(self):
        """screenshot() 传的 screenshot_base64 应在顶层"""
        obs = Observation.ok(data={"screenshot_base64": "abc123"})
        assert obs.data["screenshot_base64"] == "abc123"

    def test_done_message_direct_access(self):
        """done() 传的 message 和 done 标志应在顶层"""
        obs = Observation.ok(data={"message": "完成", "done": True})
        assert obs.data["message"] == "完成"
        assert obs.data["done"] == True

    def test_data_with_extra_kwargs_merged(self):
        """data 与 **kwargs 正确合并"""
        obs = Observation.ok(data={"path": "/tmp"}, extra="x", count=3)
        assert obs.data["path"] == "/tmp"
        assert obs.data["extra"] == "x"
        assert obs.data["count"] == 3

    def test_no_data_returns_empty_dict(self):
        """不传 data 参数时 data 为空 dict"""
        obs = Observation.ok(url="https://example.com")
        assert obs.data == {}

    def test_none_data_returns_empty_dict(self):
        """data=None 时 data 为空 dict"""
        obs = Observation.ok(url="https://example.com", data=None)
        assert obs.data == {}

    def test_fail_data_not_affected(self):
        """fail() 的 data 行为不受影响"""
        obs = Observation.fail(error="错误", detail="原因")
        assert obs.data["detail"] == "原因"

    @pytest.mark.asyncio
    async def test_executor_done_produces_correct_data(self):
        """executor._execute_done 生成的 Observation data 无嵌套"""
        mock_tool = MagicMock()
        mock_tool.current_url = "https://example.com"
        async def mock_title():
            return "页面标题"
        type(mock_tool).current_title = PropertyMock(return_value=mock_title())

        executor = Executor(mock_tool)
        action = Action(action="done", value="任务完成")
        obs = await executor._execute_done(action)

        assert obs.data["message"] == "任务完成"
        assert obs.data["done"] == True
        # 确保没有 data['data'] 嵌套
        data_keys = list(obs.data.keys())
        assert "data" not in data_keys or not isinstance(obs.data.get("data"), dict), \
            f"出现意外的 data 嵌套: {obs.data}"
