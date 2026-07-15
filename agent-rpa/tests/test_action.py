"""
Action Schema 单元测试
"""

from agent.schema.action import (
    Action,
    click,
    input_text,
    goto,
    select,
    scroll,
    wait,
    done,
)


def test_action_creation():
    """测试 Action 创建"""
    a = Action(action="click", target="登录按钮")
    assert a.action == "click"
    assert a.target == "登录按钮"
    assert a.value is None


def test_action_validation():
    """测试 Action 验证"""
    # 合法动作
    a = Action(action="click", target="登录按钮")
    assert a.is_valid()

    # 非法动作
    a = Action(action="invalid_action")
    assert not a.is_valid()
    assert "未知动作类型" in a.validate()[0]

    # goto 缺少 value
    a = Action(action="goto")
    assert not a.is_valid()

    # input 缺少 value
    a = Action(action="input", target="搜索框")
    assert not a.is_valid()


def test_factory_functions():
    """测试工厂函数"""
    a = click("登录", params={"timeout": 3000})
    assert a.action == "click"
    assert a.target == "登录"
    assert a.params["timeout"] == 3000

    a = input_text("搜索框", "hello")
    assert a.action == "input"
    assert a.value == "hello"

    a = goto("https://example.com")
    assert a.action == "goto"
    assert a.value == "https://example.com"

    a = select("城市", "北京")
    assert a.action == "select"
    assert a.value == "北京"

    a = scroll("down", 500)
    assert a.action == "scroll"
    assert a.params["direction"] == "down"
    assert a.params["amount"] == 500

    a = wait(2000)
    assert a.action == "wait"
    assert a.params["ms"] == 2000

    a = done("任务完成")
    assert a.action == "done"
    assert a.value == "任务完成"
