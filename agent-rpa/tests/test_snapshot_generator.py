"""
SnapshotGenerator 单元测试（V0.2 阶段 A3）

覆盖：
- element_id 生命周期：每次 generate() 重置（Issue 17）
- selector 优先级与 CSS 转义（Issue 16 / V0.2 计划 2.3）

使用 Mock Page，不启动真实浏览器。
"""

from unittest.mock import AsyncMock

import pytest

from agent.browser.snapshot import SnapshotGenerator


def make_el(tag="button", text="", attributes=None):
    """构造带指定属性的 Mock 元素"""
    el = AsyncMock()
    el.evaluate = AsyncMock(return_value=tag)
    el.is_visible = AsyncMock(return_value=True)
    el.inner_text = AsyncMock(return_value=text)
    attrs = attributes or {}
    el.get_attribute = AsyncMock(side_effect=lambda attr: attrs.get(attr, ""))
    el.bounding_box = AsyncMock(return_value=None)
    return el


def make_page(*element_groups):
    """构造 query_selector_all 按序返回各分组元素的 Page Mock"""
    page = AsyncMock()
    page.title = AsyncMock(return_value="测试页")
    page.url = "https://example.com"
    page.query_selector_all = AsyncMock(side_effect=list(element_groups))
    return page


# ═══════════════════════════════════════════════════════════════
# element_id 生命周期（Issue 17）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_element_id_resets_between_generations():
    """连续两次 generate() 的 element_id 都从 e1 重新开始，不再持续累加"""
    empty = [], [], [], []
    page = make_page(
        [make_el(tag="button", text="登录")], *empty,
        [make_el(tag="button", text="登录")], *empty,
    )
    sg = SnapshotGenerator(page)
    snap1 = await sg.generate()
    snap2 = await sg.generate()

    assert snap1.buttons[0].element_id == "e1"
    assert snap2.buttons[0].element_id == "e1"


# ═══════════════════════════════════════════════════════════════
# selector 优先级（V0.2 计划 2.3）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_selector_priority_testid_over_id():
    """data-testid 优先级最高"""
    el = make_el(tag="button", text="登录", attributes={
        "id": "btn-id",
        "data-testid": "login-btn",
        "role": "button",
        "aria-label": "登录按钮",
    })
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == '[data-testid="login-btn"]'


@pytest.mark.asyncio
async def test_selector_priority_id_over_aria():
    """id 优先级高于 aria-label"""
    el = make_el(tag="button", text="登录", attributes={
        "id": "btn-id",
        "aria-label": "登录按钮",
    })
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == "#btn-id"


@pytest.mark.asyncio
async def test_selector_priority_aria_over_text():
    """aria-label 优先级高于 has-text"""
    el = make_el(tag="button", text="登录", attributes={"aria-label": "登录按钮"})
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == '[aria-label="登录按钮"]'


@pytest.mark.asyncio
async def test_selector_has_text_fallback():
    """无属性时回退到 tag:has-text()"""
    el = make_el(tag="button", text="登录")
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == 'button:has-text("登录")'


@pytest.mark.asyncio
async def test_selector_tag_fallback():
    """无属性无文本时回退到标签名"""
    el = make_el(tag="input", text="")
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "input", "")
    assert selector == "input"


# ═══════════════════════════════════════════════════════════════
# selector CSS 转义（Issue 16）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_id_with_special_chars_escaped():
    """id 含 . : 等特殊字符时正确转义，原始字符不直接出现"""
    el = make_el(tag="div", text="内容", attributes={"id": "a.b:c"})
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "div", "内容")
    assert "a.b:c" not in selector
    assert selector == "#a\\2e b\\3a c"


@pytest.mark.asyncio
async def test_id_starting_with_digit_escaped():
    """首字符为数字的 id 必须转义，否则是非法 CSS 标识符"""
    el = make_el(tag="div", text="内容", attributes={"id": "123"})
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "div", "内容")
    assert selector == "#\\31 23"


@pytest.mark.asyncio
async def test_testid_with_quote_and_backslash_escaped():
    """data-testid 含引号与反斜杠时正确转义"""
    el = make_el(tag="button", text="登录", attributes={"data-testid": 'a"b\\c'})
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == '[data-testid="a\\"b\\\\c"]'


@pytest.mark.asyncio
async def test_aria_with_quote_escaped():
    """aria-label 含引号时正确转义"""
    el = make_el(tag="button", text="登录", attributes={"aria-label": '点"击"'})
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", "登录")
    assert selector == '[aria-label="点\\"击\\""]'


@pytest.mark.asyncio
async def test_text_selector_quote_escaped():
    """has-text 文本含引号时正确转义"""
    el = make_el(tag="button", text='点击"确定"')
    sg = SnapshotGenerator(AsyncMock())
    selector = await sg._build_selector(el, "button", '点击"确定"')
    assert selector == 'button:has-text("点击\\"确定\\"")'
