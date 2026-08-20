"""
LLM Planner 解析测试（V0.3 阶段 C）

覆盖：
- build_action_schema 与 Action.validate() 动作集合同源
- 合法 click / input / select / done JSON 均能产生有效 Action
- 幻觉 ID、伪造 selector、非法 action、数组输出等不会产生可用 Action
- 不需要元素的动作无需 target_id
- 系统 / 用户提示词构建
- 批量规划（V1.0）：一次输出多个连续动作，逐条安全转换
"""

import pytest

from agent.core.planner import LLMPlanner, Planner
from agent.llm import LLMRateLimitError, LLMTimeoutError, MockLLMClient
from agent.prompts.planner import (
    SYSTEM_PROMPT,
    ActionParseError,
    build_action_schema,
    build_hybrid_schema,
    build_user_prompt,
    parse_action_dict,
    parse_action_list,
)
from agent.schema.action import VALID_ACTIONS, Action
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
        """程序与大模型的对话以 INFO 级别记录（请求与响应可观测）"""
        import io

        from loguru import logger

        sink = io.StringIO()
        logger_id = logger.add(sink, level="INFO")
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

    async def test_retryable_error_gives_up_after_max_attempts(self):
        """可重试错误（超时/限流/网络）指数退避重试到上限后返回 None"""
        client = MockLLMClient([
            LLMTimeoutError("timeout-1"),
            LLMTimeoutError("timeout-2"),
            LLMTimeoutError("timeout-3"),
        ])
        planner = LLMPlanner(
            client, model="mock", max_repair_attempts=1, timeout=1000,
            llm_retries=3, llm_retry_delay=0,
        )
        assert await planner.plan(_snapshot(), "点击") is None
        assert client.call_count == 3   # 重试次数全部耗尽

    async def test_retryable_error_auto_retries_then_succeeds(self):
        """可重试错误自动重试后成功（V0.4 补的自动重试）"""
        client = MockLLMClient([
            LLMTimeoutError("timeout"),
            LLMTimeoutError("timeout"),
            {"action": "click", "target_id": "e1"},
        ])
        planner = LLMPlanner(client, model="mock", timeout=1000, llm_retry_delay=0)
        action = await planner.plan(_snapshot(), "点击 登录")
        assert action is not None
        assert action.action == "click"
        assert action.params["selector"] == "#btn-login"
        assert client.call_count == 3

    async def test_llm_error_logs_http_status_code(self):
        """LLM 调用错误在日志中展示 HTTP 状态码（可重试/不可重试均覆盖）"""
        import io

        from loguru import logger

        from agent.llm.base import LLMError

        sink = io.StringIO()
        logger_id = logger.add(sink, level="WARNING")
        try:
            # 可重试：限流 429 → 日志含 HTTP 429
            client = MockLLMClient([
                LLMRateLimitError("限流", status_code=429),
                {"action": "done"},
            ])
            planner = LLMPlanner(client, model="mock", timeout=1000, llm_retry_delay=0)
            await planner.plan(_snapshot(), "点击")
            # 不可重试：401 → 日志含 HTTP 401
            client2 = MockLLMClient([LLMError("认证失败", status_code=401)])
            planner2 = LLMPlanner(client2, model="mock", timeout=1000)
            await planner2.plan(_snapshot(), "点击")
        finally:
            logger.remove(logger_id)
        text = sink.getvalue()
        assert "HTTP 429" in text
        assert "HTTP 401" in text

    async def test_reflect_returns_alternative_action(self):
        """Reflection 分析失败原因并给出替代动作（安全转换）"""
        client = MockLLMClient([{"action": "click", "target_id": "e1"}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        failed = Action(action="click", target_id="e1",
                        params={"selector": "#btn-login"})
        alt = await planner.reflect(_snapshot(), "点击 登录", [], failed, "元素不可见")
        assert alt is not None
        assert alt.action == "click"
        assert alt.params["selector"] == "#btn-login"
        # Reflection 提示词包含失败动作与失败原因（selector 不出现在上下文）
        prompt = client.calls[0].user_prompt
        assert "元素不可见" in prompt
        assert '"target_id": "e1"' in prompt
        assert "#btn-login" not in prompt

    async def test_reflect_parse_failure_returns_none(self):
        """Reflection 输出非法（幻觉 ID）→ 返回 None，不再修复"""
        client = MockLLMClient([{"action": "click", "target_id": "e99"}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        failed = Action(action="click", target_id="e1",
                        params={"selector": "#btn-login"})
        assert await planner.reflect(_snapshot(), "点击", [], failed, "boom") is None
        assert client.call_count == 1

    async def test_reflect_without_support_returns_none(self):
        """不支持 Reflection 的规划器（基类默认实现）返回 None，不发起 LLM 调用"""
        failed = Action(action="click", target_id="e1",
                        params={"selector": "#btn-login"})
        p = Planner()
        assert await p.reflect(_snapshot(), "点击", [], failed, "x") is None

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


class TestPlanBatch:
    """批量规划（V1.0 批量增强 hybrid）：一次 LLM 调用返回多个连续动作"""

    def test_build_hybrid_schema_shape(self):
        schema = build_hybrid_schema()
        assert "anyOf" in schema and len(schema["anyOf"]) == 2
        single, batch = schema["anyOf"]
        assert single["required"] == ["action"]
        assert batch["required"] == ["actions"]
        assert batch["properties"]["actions"]["type"] == "array"
        assert batch["properties"]["actions"]["maxItems"] == 10

    def test_parse_action_list_batch(self):
        actions = parse_action_list(
            {"actions": [
                {"action": "input", "target_id": "e0", "value": "admin"},
                {"action": "click", "target_id": "e1"},
            ]},
            _snapshot(),
        )
        assert len(actions) == 2
        assert actions[0].action == "input"
        assert actions[0].value == "admin"
        assert actions[1].action == "click"
        assert actions[1].params["selector"] == "#btn-login"

    def test_parse_action_list_single(self):
        """anyOf 单动作分支：直接输出 action dict 也能解析"""
        actions = parse_action_list(
            {"action": "click", "target_id": "e1"}, _snapshot(),
        )
        assert len(actions) == 1
        assert actions[0].action == "click"
        assert actions[0].params["selector"] == "#btn-login"

    def test_parse_action_list_skips_invalid_item(self):
        """批量中单条非法（幻觉 ID）跳过，保留合法条目"""
        actions = parse_action_list(
            {"actions": [
                {"action": "click", "target_id": "e99"},
                {"action": "click", "target_id": "e1"},
            ]},
            _snapshot(),
        )
        assert len(actions) == 1
        assert actions[0].target_id == "e1"

    def test_parse_action_list_all_invalid_raises(self):
        with pytest.raises(ActionParseError):
            parse_action_list(
                {"actions": [{"action": "click", "target_id": "e99"}]}, _snapshot(),
            )

    def test_parse_action_list_single_invalid_raises(self):
        with pytest.raises(ActionParseError):
            parse_action_list({"action": "click", "target_id": "e99"}, _snapshot())

    async def test_plan_batch_returns_multiple_actions(self):
        client = MockLLMClient([{"actions": [
            {"action": "input", "target_id": "e0", "value": "admin"},
            {"action": "click", "target_id": "e1"},
        ]}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        actions = await planner.plan_batch(_snapshot(), "登录", [])
        assert actions is not None and len(actions) == 2
        assert actions[0].action == "input" and actions[0].value == "admin"
        assert actions[1].action == "click"
        assert actions[1].params["selector"] == "#btn-login"
        # 批量提示词包含批量说明
        assert "批量优化" in client.calls[0].user_prompt

    async def test_plan_batch_single_action_output(self):
        """模型选择单动作输出（导航场景）也能正常解析"""
        client = MockLLMClient([{"action": "click", "target_id": "e1"}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        actions = await planner.plan_batch(_snapshot(), "点击", [])
        assert actions is not None and len(actions) == 1
        assert actions[0].action == "click"
        assert actions[0].params["selector"] == "#btn-login"

    async def test_plan_batch_single_done(self):
        client = MockLLMClient([{"actions": [{"action": "done"}]}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        actions = await planner.plan_batch(_snapshot(), "结束", [])
        assert actions is not None and len(actions) == 1
        assert actions[0].action == "done"

    async def test_plan_batch_all_invalid_returns_none(self):
        client = MockLLMClient([{"actions": [{"action": "click", "target_id": "e99"}]}])
        planner = LLMPlanner(client, model="mock", timeout=1000)
        assert await planner.plan_batch(_snapshot(), "点击", []) is None

    async def test_base_planner_batch_falls_back_to_single(self):
        """基类 plan_batch 退化为单动作，兼容规则规划器等"""
        class _P(Planner):
            async def plan(self, snapshot, goal):
                return Action(action="click", target_id="e1",
                              params={"selector": "#btn-login"})
        p = _P()
        actions = await p.plan_batch(_snapshot(), "点击", [])
        assert actions is not None and len(actions) == 1
        assert actions[0].action == "click"
