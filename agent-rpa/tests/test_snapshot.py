"""
Snapshot Schema 单元测试
"""

from agent.schema.snapshot import ElementInfo, Snapshot


def test_element_info():
    el = ElementInfo(text="登录", tag="button", element_type="button")
    assert el.text == "登录"
    assert el.tag == "button"


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
