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
    assert a.target_id is None  # 默认无 target_id


def test_action_with_target_id():
    """测试带 target_id 的 Action 创建"""
    a = Action(
        action="click",
        target="登录按钮",
        target_id="e0",
        params={"selector": "button:has-text(\"登录\")"},
    )
    assert a.target_id == "e0"
    assert a.params["selector"] == 'button:has-text("登录")'
    assert a.is_valid()

    # 非法 target_id 格式
    a = Action(action="click", target_id="invalid")
    assert not a.is_valid()
    assert "target_id 格式无效" in a.validate()[0]


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


def test_actions_require_target_or_target_id():
    """需要目标元素的动作必须提供 target / target_id / params['selector']"""
    # 每种动作的最小合法形态（select/upload 额外要求 value）
    min_extra = {
        "click": {},
        "select": {"value": "北京"},
        "download": {},
        "upload": {"value": "/tmp/a.pdf"},
    }

    for action_type, extra in min_extra.items():
        # 没有 target / target_id / selector → 非法
        a = Action(action=action_type, **extra)
        assert not a.is_valid()
        assert "target" in a.validate()[0].lower()

        # 只提供 target_id → 合法
        a = Action(action=action_type, target_id="e0", **extra)
        assert a.is_valid(), f"{action_type} + target_id 应合法, 错误: {a.validate()}"

    # selector-only 定位（A4 契约）→ 合法
    a = Action(action="click", params={"selector": "#submit"})
    assert a.is_valid(), f"selector-only click 应合法, 错误: {a.validate()}"

    # input: value 优先校验，无 value 时报 value 错误而非 target 错误
    a = Action(action="input")
    assert not a.is_valid()
    assert "value" in a.validate()[0].lower()

    # input + target_id + value → 合法
    a = Action(action="input", target_id="e0", value="test")
    assert a.is_valid(), f"input + target_id + value 应合法, 错误: {a.validate()}"

    # goto 不受 target/target_id 影响
    a = Action(action="goto", target_id="e0")
    assert not a.is_valid()  # goto 仍需要 value


# ── 参数类型 / 范围校验（V0.2 计划 2.2 契约）───────────────────────

def test_click_param_validation():
    """click: timeout 正整数, force bool"""
    assert Action(action="click", target="x", params={"timeout": 5000}).is_valid()
    assert not Action(action="click", target="x", params={"timeout": 0}).is_valid()
    assert not Action(action="click", target="x", params={"timeout": -1}).is_valid()
    assert not Action(action="click", target="x", params={"timeout": "5s"}).is_valid()
    assert Action(action="click", target="x", params={"force": True}).is_valid()
    assert not Action(action="click", target="x", params={"force": "yes"}).is_valid()


def test_input_value_can_be_empty():
    """input.value 允许空字符串（用于清空输入框）"""
    assert Action(action="input", target="x", value="").is_valid()
    assert not Action(action="input", target="x").is_valid()


def test_select_requires_value():
    """select: value 必填"""
    assert not Action(action="select", target="x").is_valid()
    assert Action(action="select", target="x", value="北京").is_valid()


def test_upload_requires_value():
    """upload: value（文件路径）必填"""
    assert not Action(action="upload", target="x").is_valid()
    assert Action(action="upload", target="x", value="/tmp/a.pdf").is_valid()


def test_scroll_param_validation():
    """scroll: direction 枚举, amount 非负整数"""
    for d in ("down", "up", "top", "bottom"):
        assert Action(action="scroll", params={"direction": d}).is_valid(), d
    assert not Action(action="scroll", params={"direction": "left"}).is_valid()
    assert Action(action="scroll", params={"amount": 300}).is_valid()
    assert not Action(action="scroll", params={"amount": -1}).is_valid()
    assert not Action(action="scroll", params={"amount": "300px"}).is_valid()


def test_wait_param_validation():
    """wait: ms 非负整数"""
    assert Action(action="wait", params={"ms": 0}).is_valid()
    assert Action(action="wait", params={"ms": 1000}).is_valid()
    assert not Action(action="wait", params={"ms": -100}).is_valid()
    assert not Action(action="wait", params={"ms": "fast"}).is_valid()


def test_download_save_path_type():
    """download: save_path 必须为字符串或 Path"""
    from pathlib import Path
    assert Action(action="download", target="x",
                  params={"save_path": "/tmp/a.pdf"}).is_valid()
    assert Action(action="download", target="x",
                  params={"save_path": Path("/tmp/a.pdf")}).is_valid()
    assert not Action(action="download", target="x",
                      params={"save_path": 12345}).is_valid()


def test_screenshot_full_page_bool():
    """screenshot: full_page bool"""
    assert Action(action="screenshot", params={"full_page": True}).is_valid()
    assert not Action(action="screenshot", params={"full_page": "yes"}).is_valid()


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
