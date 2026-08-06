"""
TaskStep / TaskQueue / 拆解解析 单元测试（V0.4 前瞻：两阶段规划）

覆盖：
- TaskQueue 消费顺序（peek / pop / remaining / bool）
- parse_decompose_response 安全转换（wait 毫秒归一、verify 参数、非法条目过滤）
- TaskPlanner.decompose 失败回退 None
- TaskPlanner.plan_step 提示词携带步骤上下文
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.planner import (
    TaskPlanner,
    TaskQueue,
    TaskStep,
    parse_decompose_response,
)
from agent.llm import MockLLMClient, LLMInvalidResponseError
from agent.schema.snapshot import Snapshot


def _el(element_id: str, text: str):
    """构造带 element_id 的 ElementInfo（供 LLM target_id 命中）"""
    from agent.schema.snapshot import ElementInfo

    return ElementInfo(
        text=text, element_id=element_id, element_type="button", selector="#x",
    )


# ═══════════════════════════════════════════════════════════════
# TaskQueue
# ═══════════════════════════════════════════════════════════════

def _steps(*descs):
    return [TaskStep(description=d) for d in descs]


def test_queue_peek_pop_order():
    """peek 不消费，pop 按序消费"""
    q = TaskQueue(_steps("打开百度", "输入关键词"))
    assert len(q) == 2
    assert q.index == 0

    assert q.peek().description == "打开百度"
    assert q.index == 0          # peek 不推进

    assert q.pop().description == "打开百度"
    assert q.index == 1
    assert q.remaining() == 1
    assert bool(q) is True

    assert q.pop().description == "输入关键词"
    assert q.index == 2
    assert q.remaining() == 0
    assert bool(q) is False
    assert q.peek() is None
    assert q.pop() is None


def test_queue_steps_returns_all():
    """steps() 返回全部步骤（含已消费）"""
    q = TaskQueue(_steps("A", "B"))
    q.pop()
    assert [s.description for s in q.steps()] == ["A", "B"]


# ═══════════════════════════════════════════════════════════════
# parse_decompose_response
# ═══════════════════════════════════════════════════════════════

def test_parse_basic_steps():
    """action / wait / verify 三种类型正确解析"""
    raw = {"steps": [
        {"description": "打开百度", "kind": "action", "params": {"url": "https://baidu.com"}},
        {"description": "等待五秒", "kind": "wait", "params": {"ms": 5000}},
        {"description": "页面出现北京时间", "kind": "verify", "params": {"type": "text", "value": "北京时间"}},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert steps is not None
    assert len(steps) == 3
    assert steps[0].kind == "action"
    assert steps[1].kind == "wait"
    assert steps[1].params == {"ms": 5000}
    assert steps[2].kind == "verify"
    assert steps[2].params == {"type": "text", "value": "北京时间"}


def test_parse_wait_ms_default_and_guard():
    """wait 步骤 ms 缺失/非法时归一到 1000，负值归零"""
    raw = {"steps": [
        {"description": "等", "kind": "wait"},
        {"description": "等", "kind": "wait", "params": {"ms": "abc"}},
        {"description": "等", "kind": "wait", "params": {"ms": -3}},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert [s.params["ms"] for s in steps] == [1000, 1000, 0]


def test_parse_filters_invalid_items():
    """空描述 / 非法 kind / 非 dict 条目被丢弃"""
    raw = {"steps": [
        {"description": "", "kind": "action"},
        {"description": "非法类型", "kind": "sleep"},
        "not-a-dict",
        {"description": "有效步骤", "kind": "action"},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert len(steps) == 1
    assert steps[0].description == "有效步骤"


def test_parse_returns_none_when_all_invalid():
    """全部无效 / 空数组 / None → 返回 None（上层回退单步骤）"""
    assert parse_decompose_response(None, "目标") is None
    assert parse_decompose_response({}, "目标") is None
    assert parse_decompose_response({"steps": []}, "目标") is None
    assert parse_decompose_response({"steps": [{"kind": "nope"}]}, "目标") is None
    assert parse_decompose_response("junk", "目标") is None


# ── 等待语义框架兜底（LLM 拆错 kind 时纠正为 wait） ──────────────

def test_wait_semantics_action_corrected_to_wait():
    """描述含等待语义但被拆成 action → 纠正为 wait 并提取毫秒（阿拉伯数字）"""
    raw = {"steps": [
        {"description": "等待 5 秒", "kind": "action"},
        {"description": "等待3s", "kind": "action"},
        {"description": "等待 2000ms", "kind": "action"},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert all(s.kind == "wait" for s in steps)
    assert [s.params["ms"] for s in steps] == [5000, 3000, 2000]


def test_wait_semantics_chinese_digits():
    """中文数字秒（等待五秒）正确提取毫秒"""
    raw = {"steps": [
        {"description": "等待五秒", "kind": "action"},
        {"description": "等待三秒后继续", "kind": "action"},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert all(s.kind == "wait" for s in steps)
    assert [s.params["ms"] for s in steps] == [5000, 3000]


def test_wait_semantics_without_number_defaults_1000():
    """无数字的等待（页面加载完成）默认 1000ms"""
    raw = {"steps": [
        {"description": "等待页面加载完成", "kind": "action"},
        {"description": "页面加载完成后等待", "kind": "action"},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert all(s.kind == "wait" for s in steps)
    assert [s.params["ms"] for s in steps] == [1000, 1000]


def test_explicit_wait_and_verify_steps_unchanged():
    """已正确拆为 wait / verify 的步骤不受纠正影响"""
    raw = {"steps": [
        {"description": "等待五秒", "kind": "wait", "params": {"ms": 5000}},
        {"description": "页面出现北京时间", "kind": "verify",
         "params": {"type": "text", "value": "北京时间"}},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert steps[0].kind == "wait"
    assert steps[0].params == {"ms": 5000}
    assert steps[1].kind == "verify"


def test_normal_action_steps_not_corrected():
    """真正的操作步骤（点击/输入）不受等待纠正影响"""
    raw = {"steps": [
        {"description": "点击搜索结果中的北京时间链接", "kind": "action"},
        {"description": "输入关键词", "kind": "action"},
    ]}
    steps = parse_decompose_response(raw, "目标")
    assert all(s.kind == "action" for s in steps)


# ═══════════════════════════════════════════════════════════════
# TaskPlanner
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_decompose_success():
    """拆解成功返回步骤列表"""
    client = MockLLMClient([{"steps": [
        {"description": "打开百度", "kind": "action"},
        {"description": "等待", "kind": "wait", "params": {"ms": 2000}},
    ]}])
    planner = TaskPlanner(client, model="mock")
    steps = await planner.decompose("打开百度并等待")
    assert steps is not None
    assert len(steps) == 2
    assert steps[1].params == {"ms": 2000}


@pytest.mark.asyncio
async def test_decompose_failure_falls_back_to_none():
    """LLM 报错 / 返回无效内容 → 返回 None（Agent 回退自由模式）"""
    client = MockLLMClient([LLMInvalidResponseError("bad json")])
    planner = TaskPlanner(client, model="mock")
    assert await planner.decompose("目标") is None

    client2 = MockLLMClient([{"steps": []}])
    planner2 = TaskPlanner(client2, model="mock")
    assert await planner2.decompose("目标") is None


@pytest.mark.asyncio
async def test_plan_step_includes_step_context():
    """plan_step 的用户提示词包含当前步骤与剩余步骤上下文"""
    client = MockLLMClient([{"action": "click", "target_id": "e1"}])
    planner = TaskPlanner(client, model="mock")
    snapshot = Snapshot(
        title="页",
        url="https://example.com",
        buttons=[_el("e1", "百度百科链接")],
    )

    steps = [
        TaskStep(description="输入关键词", kind="action"),
        TaskStep(description="点击百科链接", kind="action"),
    ]
    action = await planner.plan_step(
        snapshot, "目标", [], steps=steps, current_index=1,
    )
    assert action is not None
    assert action.action == "click"
    user_prompt = client.calls[0].user_prompt
    assert "第 2/2 步" in user_prompt
    assert "点击百科链接" in user_prompt


@pytest.mark.asyncio
async def test_plan_step_without_context_still_works():
    """未传步骤上下文时退化为普通规划（兼容自由模式）"""
    client = MockLLMClient([{"action": "done"}])
    planner = TaskPlanner(client, model="mock")
    snapshot = Snapshot(title="页", url="https://example.com")
    action = await planner.plan_step(snapshot, "目标", [])
    assert action is not None
    assert action.action == "done"
