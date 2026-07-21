"""
Snapshot Schema 单元测试
"""

from unittest.mock import AsyncMock

import pytest

from agent.schema.snapshot import ElementInfo, Snapshot
from agent.browser.snapshot import SnapshotGenerator


def test_element_info():
    el = ElementInfo(text="登录", tag="button", element_type="button")
    assert el.text == "登录"
    assert el.tag == "button"
    assert el.element_id == ""  # 默认为空，由 SnapshotGenerator 分配


def test_snapshot_empty():
    snap = Snapshot()
    assert snap.is_empty() is True


def test_snapshot_with_elements():
    snap = Snapshot(
        title="测试页面",
        url="https://example.com",
        buttons=[ElementInfo(text="提交", tag="button")],
        inputs=[ElementInfo(text="用户名", tag="input")],
    )
    assert snap.is_empty() is False
    assert len(snap.get_interactive_elements()) == 2


def test_snapshot_find():
    snap = Snapshot(
        buttons=[
            ElementInfo(text="登录", tag="button"),
            ElementInfo(text="注册", tag="button"),
        ],
        inputs=[ElementInfo(text="搜索", tag="input")],
    )
    results = snap.find("登录")
    assert len(results) == 1
    assert results[0].text == "登录"


def test_snapshot_summary():
    snap = Snapshot(
        title="首页",
        url="https://example.com",
        buttons=[ElementInfo(text="登录", tag="button")],
        inputs=[ElementInfo(text="搜索", tag="input")],
    )
    summary = snap.summary()
    assert "首页" in summary
    assert "登录" in summary


@pytest.mark.asyncio
async def test_snapshot_generator_element_id():
    """验证 SnapshotGenerator 为每个元素分配全局唯一 element_id"""
    mock_page = AsyncMock()

    mock_btn = AsyncMock()
    mock_btn.evaluate = AsyncMock(return_value="button")
    mock_btn.is_visible = AsyncMock(return_value=True)
    mock_btn.inner_text = AsyncMock(return_value="登录")
    mock_btn.get_attribute = AsyncMock(return_value="")
    mock_btn.bounding_box = AsyncMock(return_value=None)

    mock_input = AsyncMock()
    mock_input.evaluate = AsyncMock(return_value="input")
    mock_input.is_visible = AsyncMock(return_value=True)
    mock_input.inner_text = AsyncMock(return_value="")
    mock_input.get_attribute = AsyncMock(side_effect=lambda attr: {
        "placeholder": "请输入用户名",
        "aria-label": "用户名",
    }.get(attr, ""))
    mock_input.bounding_box = AsyncMock(return_value=None)

    # 多次 query_selector_all 调用返回不同元素
    mock_page.query_selector_all = AsyncMock(side_effect=[
        [mock_btn],       # 第一次: buttons
        [mock_input],     # 第二次: inputs
        [],               # 第三次: links
        [],               # 第四次: texts
        [],               # 第五次: selects
    ])

    sg = SnapshotGenerator(mock_page)
    snapshot = await sg.generate()

    assert len(snapshot.buttons) == 1
    assert len(snapshot.inputs) == 1

    btn = snapshot.buttons[0]
    inp = snapshot.inputs[0]

    # 验证 element_id 格式
    assert btn.element_id.startswith("e")
    assert btn.element_id[1:].isdigit()
    assert inp.element_id.startswith("e")
    assert inp.element_id[1:].isdigit()

    # 验证全局唯一（不同类型的元素也不同 ID）
    assert btn.element_id != inp.element_id


@pytest.mark.asyncio
async def test_snapshot_generator_filters_invisible():
    """验证 SnapshotGenerator 过滤不可见元素"""
    mock_page = AsyncMock()

    visible_btn = AsyncMock()
    visible_btn.evaluate = AsyncMock(return_value="button")
    visible_btn.is_visible = AsyncMock(return_value=True)
    visible_btn.inner_text = AsyncMock(return_value="可见按钮")
    visible_btn.get_attribute = AsyncMock(return_value="")
    visible_btn.bounding_box = AsyncMock(return_value=None)

    invisible_btn = AsyncMock()
    invisible_btn.evaluate = AsyncMock(return_value="button")
    invisible_btn.is_visible = AsyncMock(return_value=False)
    invisible_btn.inner_text = AsyncMock(return_value="隐藏按钮")
    invisible_btn.get_attribute = AsyncMock(return_value="")
    invisible_btn.bounding_box = AsyncMock(return_value=None)

    mock_page.query_selector_all = AsyncMock(side_effect=[
        [visible_btn, invisible_btn],  # buttons: 一个可见一个隐藏
        [],  # inputs
        [],  # links
        [],  # texts
        [],  # selects
    ])

    sg = SnapshotGenerator(mock_page)
    snapshot = await sg.generate()

    assert len(snapshot.buttons) == 1
    assert snapshot.buttons[0].text == "可见按钮"
    assert not any(b.text == "隐藏按钮" for b in snapshot.buttons)
