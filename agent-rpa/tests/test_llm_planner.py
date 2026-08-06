"""
LLM Planner 解析测试（V0.3 阶段 C）

覆盖：
- build_action_schema 与 Action.validate() 动作集合同源
- 合法 click / input / select / done JSON 均能产生有效 Action
- 幻觉 ID、伪造 selector、非法 action、数组输出等不会产生可用 Action
- 不需要元素的动作无需 target_id
- 系统 / 用户提示词构建
"""

import pytest

from agent.core.planner import LLMPlanner
from agent.llm import LLMTimeoutError, MockLLMClient
from agent.prompts.planner import (
    SYSTEM_PROMPT,
    ActionParseError,
    build_action_schema,
    build_user_prompt,
    parse_action_dict,
)
from agent.schema.action import VALID_ACTIONS
from agent.schema.snapshot import ElementInfo, Snapshot


def _el(element_id: str, *, text="", element_type="button", selector="",
        placeholder="", aria_label="") -> ElementInfo:
    return ElementInfo(
        text=text, element_id=element_id, element_type=element_type,
        selector=selector, placeholder=placeholder, aria_label=aria_label,
    )


def _snapshot() -> Snapshot:
    return Snapshot(
        title="登录页",
        url="https://example.com/login",
        inputs=[_el("e0", element_type="textbox", selector="#user",
                     placeholder="用户名", aria_label="用户名")],
        buttons=[_el("e1", element_type="button", selector="#btn-login", text="登录")],
    )


class TestActionSchema:
    def test_enum_comes_from_valid_actions(self):
        enum = build_action_schema()["properties"]["action"]["enum"]
        assert set(enum) == set(VALID_ACTIONS)

    def test_requires_action_field(self):
        assert build_action_schema()["required"] == ["action"]


class TestParseActionDict:
    def test_click_with_target_id(self):
        action = parse_action_dict(
            {"action": "click", "target_id": "e1", "reason": "提交登录"},
            _snapshot(),
        )
        assert action.action == "click"
        assert action.target_id == "e1"
        # selector 由本地 Snapshot 注入
        assert action.params["selector"] == "#btn-login"
        assert action.target == "登录"
        assert action.is_valid()

    def test_input_with_target_id(self):
        action = parse_action_dict(
            {"action": "input", "target_id": "e0", "value": "alice"},
            _snapshot(),
        )
        assert action.action == "input"
        assert action.value == "alice"
        assert action.params["selector"] == "#user"
        assert action.is_valid()

    def test_select_with_target_id(self):
        snap = Snapshot(title="t", url="u",
                        selects=[_el("e2", element_type="dropdown", selector="#sel")])
        action = parse_action_dict(
            {"action": "select", "target_id": "e2", "value": "CN"}, snap
        )
        assert action.action == "select"
        assert action.value == "CN"
        assert action.is_valid()

    def test_done_without_target(self):
        action = parse_action_dict({"action": "done"}, _snapshot())
        assert action.action == "done"
        assert action.is_valid()

    def test_goto_without_target(self):
        action = parse_action_dict({"action": "goto", "value": "https://x.com"}, _snapshot())
        assert action.action == "goto"
        assert action.is_valid()

    def test_wait_without_target(self):
        action = parse_action_dict({"action": "wait", "params": {"ms": 500}}, _snapshot())
        assert action.is_valid()

    def test_hallucinated_id_rejected(self):
        with pytest.raises(ActionParseError, match="e99"):
            parse_action_dict({"action": "click", "target_id": "e99"}, _snapshot())

    def test_forged_selector_ignored_and_replaced(self):
        action = parse_action_dict(
            {"action": "click", "target_id": "e1", "params": {"selector": "#evil"}},
            _snapshot(),
        )
        assert action.params["selector"] == "#btn-login"

    def test_unknown_action_rejected(self):
        with pytest.raises(ActionParseError, match="未知或缺失 action"):
            parse_action_dict({"action": "hack"}, _snapshot())

    def test_array_output_rejected(self):
        with pytest.raises(ActionParseError, match="必须是 JSON 对象"):
            parse_action_dict([{"action": "click"}], _snapshot())

    def test_string_output_rejected(self):
        with pytest.raises(ActionParseError, match="必须是 JSON 对象"):
            parse_action_dict("{\"action\": \"click\"}", _snapshot())

    def test_params_non_object_rejected(self):
        with pytest.raises(ActionParseError, match="params"):
            parse_action_dict({"action": "wait", "params": [1, 2]}, _snapshot())

    def test_unknown_fields_ignored(self):
        action = parse_action_dict(
            {"action": "click", "target_id": "e1", "reason": "r", "bogus": 1},
            _snapshot(),
        )
        assert not hasattr(action, "bogus")
        assert action.is_valid()

    def test_input_missing_value_rejected(self):
        with pytest.raises(ActionParseError, match="校验失败"):
            parse_action_dict({"action": "input", "target_id": "e0"}, _snapshot())

    def test_action_requires_target_id_for_elements(self):
        with pytest.raises(ActionParseError, match="target_id"):
            parse_action_dict({"action": "click", "target": "登录"}, _snapshot())


class TestPrompts:
    def test_system_prompt_has_key_constraints(self):
        for keyword in ("规划器", "不要添加 Markdown", "元素 ID", "done"):
            assert keyword in SYSTEM_PROMPT

    def test_system_prompt_lists_all_actions(self):
        """提示词必须包含全部 action 枚举，防止模型输出未知 action（与 schema 同源）。"""
        for action in VALID_ACTIONS:
            assert action in SYSTEM_PROMPT

    def test_user_prompt_contains_goal_snapshot_history(self):
        snap_view = {"title": "登录页", "elements": []}
        history = [{"action": "goto", "target": None, "target_id": None,
                    "success": True, "url": "https://example.com"}]
        prompt = build_user_prompt("登录系统", snap_view, history)
        assert "登录系统" in prompt
        assert "登录页" in prompt
        assert "goto" in prompt

    def test_user_prompt_respects_history_window(self):
        snap_view = {"title": "t", "elements": []}
        history = [{"action": f"a{i}", "success": True, "url": "u"} for i in range(8)]
        prompt = build_user_prompt("g", snap_view, history, max_history=3)
        # 只出现最近 3 条
        assert prompt.count("a5") == 1 and prompt.count("a7") == 1
        assert "a0" not in prompt


# ── V0.3 阶段 D：LLMPlanner 完整调用链与有限修复 ─────────────────────

class TestLLMPlanner:
    async def test_valid_action_returned(self):
        client = MockLLMClient([{"action": "click", "target_id": "e1"}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        action = await planner.plan(_snapshot(), "点击 登录")
        assert action is not None
        assert action.action == "click"
        assert action.params["selector"] == "#btn-login"
        assert client.call_count == 1

    async def test_int_value_coerced_to_str(self):
        """模型输出 int value（如 wait 5000）→ 强转 str，防止后续切片崩溃"""
        client = MockLLMClient([{"action": "wait", "value": 5000}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        action = await planner.plan(_snapshot(), "等待五秒")
        assert action is not None
        assert action.action == "wait"
        assert action.value == "5000"

    async def test_prompt_and_response_are_logged(self):
        """程序与大模型的对话以 DEBUG 级别记录（请求与响应可观测）"""
        import io

        from loguru import logger

        sink = io.StringIO()
        logger_id = logger.add(sink, level="DEBUG")
        try:
            client = MockLLMClient([{"action": "done"}])
            planner = LLMPlanner(client, model="mock", timeout=1000)
            await planner.plan(_snapshot(), "测试目标")
        finally:
            logger.remove(logger_id)
        text = sink.getvalue()
        assert "LLM 请求" in text
        assert "LLM 响应" in text
        assert '"action": "done"' in text

    async def test_repair_recovers_within_limit(self):
        client = MockLLMClient([
            {"action": "input", "target_id": "e0"},              # 缺 value → 校验失败
            {"action": "input", "target_id": "e0", "value": "alice"},
        ])
        planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)
        action = await planner.plan(_snapshot(), "输入 用户名")
        assert action is not None
        assert action.value == "alice"
        assert client.call_count == 2   # 原请求 + 一次修复

    async def test_consecutive_failures_return_none(self):
        client = MockLLMClient([
            {"action": "click", "target_id": "e99"},
            {"action": "click", "target_id": "e98"},
        ])
        planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)
        assert await planner.plan(_snapshot(), "点击") is None
        assert client.call_count == 2   # 原请求 + 一次修复，之后停止

    async def test_retryable_error_returns_none_without_retry(self):
        client = MockLLMClient([LLMTimeoutError("timeout")])
        planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)
        assert await planner.plan(_snapshot(), "点击") is None
        assert client.call_count == 1   # 可重试错误不自动重试（留给 V0.4）

    async def test_repair_prompt_contains_original_and_error(self):
        client = MockLLMClient([
            {"action": "click", "target_id": "e99"},
            {"action": "click", "target_id": "e1"},
        ])
        planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)
        await planner.plan(_snapshot(), "点击")
        repair_prompt = client.calls[1].user_prompt
        assert "上次输出" in repair_prompt
        assert "e99" in repair_prompt
        assert "校验错误" in repair_prompt

    async def test_history_serialized_into_prompt(self):
        client = MockLLMClient([{"action": "done"}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        action = type("A", (), {"action": "click", "target": "登录", "target_id": "e1"})()
        obs = type("O", (), {"success": True, "url": "https://x.com/page"})()
        history = [{"action": action, "observation": obs}]
        await planner.plan_with_history(_snapshot(), "目标", history)
        prompt = client.calls[0].user_prompt
        assert "click" in prompt
        assert "https://x.com/page" in prompt
