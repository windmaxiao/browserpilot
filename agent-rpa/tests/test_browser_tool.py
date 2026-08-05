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
