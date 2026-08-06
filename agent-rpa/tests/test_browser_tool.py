"""
BrowserTool 单元测试（V0.2）

覆盖 click() 的 page_changed 判定增强（Issue 10）：
- URL 不变、标题不变但 DOM 变化 → page_changed=True（SPA 场景）
- URL/标题/DOM 均不变 → page_changed=False
- URL 变化仍能正确识别

使用 AsyncMock Page 对象，不启动真实浏览器。
"""

import sys
sys.path.insert(0, ".")

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent.browser.playwright import BrowserTool


def make_page_mock():
    """构造带 locator / url / title / evaluate 的 Page Mock"""
    page = AsyncMock()
    locator = AsyncMock()
    locator.wait_for = AsyncMock()
    locator.click = AsyncMock()
    page.locator = MagicMock(return_value=locator)
    page.title = AsyncMock(return_value="页面标题")
    page.wait_for_load_state = AsyncMock()
    page.url = "https://example.com"
    return page, locator


def make_new_tab_page_mock():
    """构造点击后出现新标签页的 Page Mock（模拟 target=_blank 链接）"""
    page, locator = make_page_mock()
    new_page = AsyncMock()
    new_page.url = "https://baike.baidu.com/item/北京时间"
    new_page.title = AsyncMock(return_value="北京时间 - 百度百科")
    new_page.wait_for_load_state = AsyncMock()
    new_page.is_closed = MagicMock(return_value=False)
    context = MagicMock(pages=[page])
    page.context = context
    # 点击后新标签页出现
    locator.click = AsyncMock(
        side_effect=lambda **kw: setattr(context, "pages", [page, new_page])
    )
    return page, new_page, locator


class TestClickPageChanged:

    @pytest.mark.asyncio
    async def test_click_detects_dom_change(self):
        """URL/标题不变但 DOM 变化 → page_changed=True（SPA 场景）"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},   # 点击前
            {"elements": 15, "text_len": 200},   # 点击后（结果渲染）
        ])
        tool = BrowserTool(page)
        obs = await tool.click("#btn")
        assert obs.success is True
        assert obs.page_changed is True

    @pytest.mark.asyncio
    async def test_click_no_change_returns_false(self):
        """URL/标题/DOM 均不变 → page_changed=False"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},
            {"elements": 10, "text_len": 100},
        ])
        tool = BrowserTool(page)
        obs = await tool.click("#btn")
        assert obs.success is True
        assert obs.page_changed is False

    @pytest.mark.asyncio
    async def test_click_url_change_still_detected(self):
        """URL 变化时 page_changed=True（即使 DOM 指纹相同）"""
        page, locator = make_page_mock()
        locator.click = AsyncMock(
            side_effect=lambda **kw: setattr(page, "url", "https://example.com/page2")
        )
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},
            {"elements": 10, "text_len": 100},
        ])
        tool = BrowserTool(page)
        obs = await tool.click("#btn")
        assert obs.success is True
        assert obs.page_changed is True
        assert obs.url == "https://example.com/page2"

    @pytest.mark.asyncio
    async def test_fingerprint_failure_does_not_crash(self):
        """evaluate 失败时指纹返回空 dict，不影响 click 结果"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock(side_effect=Exception("page closed"))
        tool = BrowserTool(page)
        fp = await tool._page_fingerprint()
        assert fp == {}


class TestClickFollowNewTab:
    """点击 target=_blank 链接后自动跟随新标签页（新能力）"""

    @pytest.mark.asyncio
    async def test_click_follows_new_tab_and_notifies(self):
        """打开新标签页 → 自动切换 page、通知订阅者、page_changed=True"""
        page, new_page, _ = make_new_tab_page_mock()
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},
            {"elements": 10, "text_len": 100},
        ])
        switched = []
        tool = BrowserTool(page, on_page_changed=lambda p: switched.append(p))
        obs = await tool.click("#baike-link")
        assert obs.success is True
        assert obs.page_changed is True
        assert obs.url == new_page.url
        assert tool.page is new_page
        assert switched == [new_page]

    @pytest.mark.asyncio
    async def test_click_without_new_tab_keeps_page(self):
        """未打开新标签页 → 页面不切换，走原有 page_changed 判定"""
        page, locator = make_page_mock()
        page.context = MagicMock(pages=[page])  # 只有当前页
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},
            {"elements": 10, "text_len": 100},
        ])
        switched = []
        tool = BrowserTool(page, on_page_changed=lambda p: switched.append(p))
        obs = await tool.click("#btn")
        assert obs.success is True
        assert obs.page_changed is False
        assert tool.page is page
        assert switched == []

    @pytest.mark.asyncio
    async def test_click_when_context_unavailable_still_works(self):
        """context 不可访问（如 Mock 未配置）时不影响原有逻辑"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock(side_effect=[
            {"elements": 10, "text_len": 100},
            {"elements": 15, "text_len": 200},
        ])
        tool = BrowserTool(page)
        obs = await tool.click("#btn")
        assert obs.success is True
        assert obs.page_changed is True


class TestPageFingerprint:

    @pytest.mark.asyncio
    async def test_fingerprint_returns_dict(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={"elements": 5, "text_len": 10}
        )
        tool = BrowserTool(page)
        fp = await tool._page_fingerprint()
        assert fp == {"elements": 5, "text_len": 10}


class TestWaitAndScrollDefensive:
    """V0.2 A2: 非法参数转换为 Observation.fail，杜绝 JS 注入"""

    @pytest.mark.asyncio
    async def test_wait_valid_ms(self):
        page, _ = make_page_mock()
        tool = BrowserTool(page)
        obs = await tool.wait(0)
        assert obs.success is True

    @pytest.mark.asyncio
    async def test_wait_negative_ms_returns_fail(self):
        page, _ = make_page_mock()
        tool = BrowserTool(page)
        obs = await tool.wait(-100)
        assert obs.is_error is True
        assert "ms" in obs.error

    @pytest.mark.asyncio
    async def test_wait_string_ms_returns_fail(self):
        page, _ = make_page_mock()
        tool = BrowserTool(page)
        obs = await tool.wait("fast")
        assert obs.is_error is True

    @pytest.mark.asyncio
    async def test_wait_page_exception_returns_fail(self):
        """page 异常（如 title 失败）不向上泄漏"""
        page, _ = make_page_mock()
        page.title = AsyncMock(side_effect=Exception("page closed"))
        tool = BrowserTool(page)
        obs = await tool.wait(10)
        assert obs.is_error is True
        assert "等待失败" in obs.error

    @pytest.mark.asyncio
    async def test_scroll_valid_amount_parameterized(self):
        """amount 通过 evaluate 参数传递，而非字符串拼接（防 JS 注入）"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock()
        tool = BrowserTool(page)
        obs = await tool.scroll("down", 300)
        assert obs.success is True
        page.evaluate.assert_awaited_once()
        args, _ = page.evaluate.call_args
        assert isinstance(args[0], str)
        assert args[1] == 300

    @pytest.mark.asyncio
    async def test_scroll_up_uses_negative_amount(self):
        page, _ = make_page_mock()
        page.evaluate = AsyncMock()
        tool = BrowserTool(page)
        await tool.scroll("up", 100)
        args, _ = page.evaluate.call_args
        assert args[1] == -100

    @pytest.mark.asyncio
    async def test_scroll_invalid_amount_returns_fail(self):
        """'300px' 等非法 amount 返回失败，且不执行 evaluate"""
        page, _ = make_page_mock()
        page.evaluate = AsyncMock()
        tool = BrowserTool(page)
        obs = await tool.scroll("down", "300px")
        assert obs.is_error is True
        page.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scroll_invalid_direction_returns_fail(self):
        page, _ = make_page_mock()
        page.evaluate = AsyncMock()
        tool = BrowserTool(page)
        obs = await tool.scroll("left", 300)
        assert obs.is_error is True
        assert "direction" in obs.error or "方向" in obs.error

    @pytest.mark.asyncio
    async def test_select_calls_smart_wait(self):
        """select() 后调用 _smart_wait，保持与 click() 一致（Issue 19）"""
        page, locator = make_page_mock()
        locator.select_option = AsyncMock()
        tool = BrowserTool(page)
        obs = await tool.select("#city", "北京")
        assert obs.success is True
        page.wait_for_load_state.assert_awaited()
