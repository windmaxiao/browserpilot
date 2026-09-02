"""
Agent Mock 集成测试（V0.2 阶段 C2 + V0.3 阶段 D）

采用 Mock Observer / Planner / BrowserTool，验证 Agent.run() 完整链路：
Observe → Plan → Execute → Record，以及 done / 执行失败 / 非法 Action 分支；
V0.3 起 Agent 通过 plan_with_history 向 Planner 传入有限历史。
"""

import asyncio

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.planner import LLMPlanner
from agent.llm import MockLLMClient
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import ElementInfo, Snapshot


class _PlannerStub:
    """记录 plan_batch 调用并依次返回预设 Action 的桩 Planner。"""

    def __init__(self, actions=None):
        self._actions = list(actions) if actions else []
        self.calls: list[dict] = []
        self.reset_calls = 0

    def reset(self):
        """Agent.run() 开头调用（待解决问题 #11）。"""
        self.reset_calls += 1

    async def plan_batch(self, snapshot, goal, history):
        self.calls.append({"goal": goal, "history_len": len(history)})
        action = self._actions.pop(0) if self._actions else None
        return [action] if action is not None else None


def make_mocks(actions=None):
    """构造 Observer/Planner/BrowserTool 的 Mock 组合，返回 (agent, planner, tool)"""
    snapshot = Snapshot(title="测试页", url="https://example.com")

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    planner = _PlannerStub(actions)

    tool = MagicMock()
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="测试页")

    executor = Executor(tool)
    agent = Agent(observer, planner, executor, max_steps=10)
    return agent, planner, tool


@pytest.mark.asyncio
async def test_three_step_success_flow():
    """输入框 → 提交按钮 → done 的三步成功流程"""
    agent, planner, tool = make_mocks([
        Action(action="input", value="北京时间",
               params={"selector": "#search-box"}),
        Action(action="click", params={"selector": "#search-btn"}),
        done("任务完成"),
    ])

    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    # done 不进入执行链，history 只记录两步
    assert len(agent.history) == 2
    assert agent.current_step == 3
    tool.input.assert_awaited_once_with(
            "#search-box", "北京时间", timeout=5000, clear_first=True,
            frame_path=(),
        )
    tool.click.assert_awaited_once_with(
            "#search-btn", timeout=5000, force=False, frame_path=()
        )


@pytest.mark.asyncio
async def test_run_resets_planner_state():
    """run() 开头调用 planner.reset()（#11）：复用 Agent 同 goal 二次 run 无状态残留"""
    agent, planner, tool = make_mocks([
        Action(action="input", value="北京时间",
               params={"selector": "#search-box"}),
        done("任务完成"),
        Action(action="input", value="上海天气",
               params={"selector": "#search-box"}),
        done("任务完成"),
    ])

    await agent.run("查找 北京时间")
    await agent.run("查找 北京时间")   # 同 goal 二次 run，Planner 状态已清空

    assert planner.reset_calls == 2


@pytest.mark.asyncio
async def test_run_timeout_returns_failure():
    """run(timeout_seconds=...) 超时返回失败 Observation（#22）"""
    snapshot = Snapshot(title="测试页", url="https://example.com")
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    async def _slow_plan_batch(snapshot, goal, history):
        await asyncio.sleep(5)   # 模拟慢 LLM
        return [done("完成")]

    planner = _PlannerStub()
    planner.plan_batch = _slow_plan_batch
    tool = MagicMock()
    executor = Executor(tool)
    agent = Agent(observer, planner, executor, max_steps=10)

    obs = await agent.run("目标", timeout_seconds=0.1)
    assert obs.is_error
    assert "超时" in obs.error


@pytest.mark.asyncio
async def test_planner_receives_growing_history():
    """plan_with_history 每步收到逐步增长的 history"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#a"}),
        done(),
    ])

    await agent.run("任务")

    assert [c["history_len"] for c in planner.calls] == [0, 1]


@pytest.mark.asyncio
async def test_selector_only_action_reaches_tool():
    """Planner 输出 selector-only Action 后由 Executor 正确调用 BrowserTool"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#submit"}),
        done(),
    ])

    obs = await agent.run("点击 提交")

    assert obs.success is True
    tool.click.assert_awaited_once_with(
        "#submit", timeout=5000, force=False, frame_path=()
    )


@pytest.mark.asyncio
async def test_invalid_action_blocked_before_tool():
    """非法 Action 被 validate 拦截，不会调用 BrowserTool"""
    agent, planner, tool = make_mocks([
        Action(action="click"),   # 无 target/target_id/selector → 非法
        done(),
    ])

    obs = await agent.run("随便")

    assert obs.is_error is True
    assert "验证失败" in obs.error
    tool.click.assert_not_awaited()
    assert len(agent.history) == 1


@pytest.mark.asyncio
async def test_tool_failure_stops_agent_and_records():
    """BrowserTool 返回失败 Observation 时 Agent 停止并记录 history"""
    agent, planner, tool = make_mocks([
        Action(action="input", value="x", params={"selector": "#kw"}),
        done(),  # 不应被执行
    ])
    tool.input = AsyncMock(return_value=Observation.fail("输入失败"))

    obs = await agent.run("查找 x")

    assert obs.is_error is True
    assert "输入失败" in obs.error
    assert agent.current_step == 1
    assert len(agent.history) == 1
    assert agent.history[0]["action"].action == "input"
    assert agent.history[0]["observation"].is_error is True


@pytest.mark.asyncio
async def test_max_steps_exceeded():
    """超过最大步数时返回失败 Observation"""
    agent, planner, tool = make_mocks(
        [Action(action="click", params={"selector": "#btn"})] * 12
    )  # max_steps=10，会超限

    obs = await agent.run("任务")

    assert obs.is_error is True
    assert "超出最大步数" in obs.error
    assert agent.current_step == 10
    assert len(agent.history) == 10


# ── V0.3 阶段 D：Mock LLM 驱动 Agent 多步流程 ─────────────────────────

def _search_snapshot() -> Snapshot:
    return Snapshot(
        title="搜索页",
        url="https://example.com/search",
        page_type="search",
        inputs=[ElementInfo(text="", element_id="e0", element_type="textbox",
                            selector="#kw", placeholder="搜索", aria_label="搜索")],
        buttons=[ElementInfo(text="搜索", element_id="e1", element_type="button",
                             selector="#btn")],
    )


@pytest.mark.asyncio
async def test_llm_planner_drives_agent_flow():
    """Mock LLM 驱动 Agent 完成 input → click → done 多步流程"""
    snapshot = _search_snapshot()
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    client = MockLLMClient([
        {"action": "input", "target_id": "e0", "value": "北京时间"},
        {"action": "click", "target_id": "e1"},
        {"action": "done"},
    ])
    planner = LLMPlanner(client, model="mock", timeout=1000)

    tool = MagicMock()
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.current_url = "https://example.com/search"
    tool.current_title = AsyncMock(return_value="搜索页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    tool.input.assert_awaited_once()
    tool.click.assert_awaited_once()
    # 恰好 3 次 LLM 调用，无多余请求
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_llm_planner_repair_recovers_inside_agent():
    """格式错误经一次修复恢复后，Agent 流程继续"""
    snapshot = _search_snapshot()
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    client = MockLLMClient([
        {"action": "click", "target_id": "e99"},   # 幻觉 ID → 修复
        {"action": "click", "target_id": "e1"},
        {"action": "done"},
    ])
    planner = LLMPlanner(client, model="mock", max_repair_attempts=1, timeout=1000)

    tool = MagicMock()
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.current_url = "https://example.com/search"
    tool.current_title = AsyncMock(return_value="搜索页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("点击 搜索")

    assert obs.success is True
    tool.click.assert_awaited_once()
    assert client.call_count == 3


# ── 循环检测：连续等待无效果 → 任务停滞 ─────────────────────────────

@pytest.mark.asyncio
async def test_consecutive_waits_without_page_change_terminates():
    """连续 2 次 wait 且页面无变化 → 判定任务停滞并提前终止（Terminal#146-766）"""
    agent, planner, tool = make_mocks([
        Action(action="wait", value="5000"),
        Action(action="wait", value="5000"),
        done(),  # 不应被执行
    ])
    tool.wait = AsyncMock(return_value=Observation.ok(page_changed=False))

    obs = await agent.run("查找 北京时间")

    assert obs.is_error is True
    assert "任务停滞" in obs.error
    assert agent.current_step == 2
    assert len(agent.history) == 2
    tool.wait.assert_awaited_with(ms=5000)
    assert tool.wait.await_count == 2


@pytest.mark.asyncio
async def test_wait_after_page_change_resets_stagnation_counter():
    """wait 后页面有变化时不触发停滞，继续正常流程"""
    agent, planner, tool = make_mocks([
        Action(action="wait", value="3000"),     # 页面变化 → 计数器清零
        Action(action="wait", value="3000"),     # 第二次等待仍不触发停滞
        done(),
    ])
    tool.wait = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    assert agent.current_step == 3
    tool.wait.assert_awaited_with(ms=3000)
    assert tool.wait.await_count == 2


# ── M4：接口契约漂移 ──────────────────────────────────────────────

class _LegacyDuckPlanner:
    """V0.3 鸭子类型：仅实现 plan（无基类 / 无 plan_batch / 无 plan_with_history）"""

    def __init__(self, action):
        self._action = action
        self.calls = 0

    async def plan(self, snapshot, goal):
        self.calls += 1
        return self._action


@pytest.mark.asyncio
async def test_legacy_duck_planner_without_plan_batch_falls_back():
    """M4：V0.3 鸭子类型 Planner（仅 plan）在自由模式逐级回退，不崩溃"""
    snapshot = Snapshot(title="测试页", url="https://example.com")
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)
    planner = _LegacyDuckPlanner(done())
    tool = MagicMock()
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="测试页")

    agent = Agent(observer, planner, Executor(tool), max_steps=5)
    obs = await agent.run("完成任务")

    assert obs.success is True
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_rule_planner_waits_exempt_from_stagnation():
    """M4：stagnation_detection=False 的规则型规划器，连续 wait 不被停滞检测截断"""
    agent, planner, tool = make_mocks([
        Action(action="wait", value="5000"),
        Action(action="wait", value="5000"),
        Action(action="wait", value="5000"),
        done(),
    ])
    tool.wait = AsyncMock(return_value=Observation.ok(page_changed=False))
    planner.stagnation_detection = False  # 规则型：自身用 _WAIT_MAX_TRIES 控制等待

    obs = await agent.run("查找 北京时间")

    assert obs.success is True
    assert agent.current_step == 4
    assert tool.wait.await_count == 3


# ── 步骤模式（V0.4 前瞻）：任务队列 ─────────────────────────────────

class _StepPlannerStub:
    """实现 decompose + plan_step 的桩规划器（模拟 TaskPlanner）。"""

    def __init__(self, steps, actions_by_index=None):
        self._steps = steps
        self._actions = actions_by_index or {}
        self.plan_calls: list[int] = []

    async def decompose(self, goal):
        return self._steps

    async def plan_step(self, snapshot, goal, history, *, steps=None, current_index=None):
        self.plan_calls.append(current_index)
        actions = self._actions.get(current_index, [])
        return actions.pop(0) if actions else None


def make_step_agent(steps, actions_by_index=None, snapshot=None):
    """构造步骤模式的 Agent，返回 (agent, planner, tool)"""
    from agent.core.planner import TaskStep

    snap = snapshot or Snapshot(title="测试页", url="https://example.com")

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snap)

    planner = _StepPlannerStub(steps, actions_by_index)

    tool = MagicMock()
    tool.wait = AsyncMock(return_value=Observation.ok())
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="测试页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    return agent, planner, tool


@pytest.mark.asyncio
async def test_step_mode_waits_executed_by_framework():
    """wait 步骤由框架直接执行，不调用 planner（LLM 零开销）"""
    from agent.core.planner import TaskStep

    agent, planner, tool = make_step_agent([
        TaskStep(description="等待两秒", kind="wait", params={"ms": 2000}),
        TaskStep(description="点击按钮", kind="action"),
    ], actions_by_index={1: [Action(action="click", params={"selector": "#btn"})]})

    obs = await agent.run("任务")

    assert obs.success is True
    assert obs.data.get("done") is True
    # wait 步骤没有调用 planner
    assert planner.plan_calls == [1]
    tool.wait.assert_awaited_once_with(ms=2000)
    tool.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_step_mode_queue_exhausted_returns_done():
    """队列耗尽（无需 LLM 输出 done）即任务完成"""
    from agent.core.planner import TaskStep

    agent, planner, tool = make_step_agent([
        TaskStep(description="点击提交", kind="action"),
    ], actions_by_index={0: [Action(action="click", params={"selector": "#go"})]})

    obs = await agent.run("任务")

    assert obs.success is True
    assert obs.data.get("done") is True
    assert len(agent.history) == 1
    tool.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_step_mode_verify_pass_and_fail():
    """verify 步骤由框架校验：通过则推进，未通过则失败"""
    from agent.core.planner import TaskStep

    # 通过：快照含"北京时间"
    snap_ok = Snapshot(
        title="北京时间 - 百度百科",
        url="https://baike.baidu.com",
    )
    agent, planner, tool = make_step_agent(
        [TaskStep(description="验收", kind="verify",
                  params={"type": "text", "value": "北京时间"})],
        snapshot=snap_ok,
    )
    obs = await agent.run("任务")
    assert obs.success is True
    assert planner.plan_calls == []      # verify 步骤不经过 planner

    # 未通过：快照不含验收文本
    snap_bad = Snapshot(title="别的页面", url="https://example.com")
    agent2, planner2, tool2 = make_step_agent(
        [TaskStep(description="验收", kind="verify",
                  params={"type": "text", "value": "不存在"})],
        snapshot=snap_bad,
    )
    obs2 = await agent2.run("任务")
    assert obs2.is_error is True
    assert "步骤验收未通过" in obs2.error


@pytest.mark.asyncio
async def test_step_mode_verify_matches_body_text():
    """验收文本仅出现在正文（snapshot.texts）时也能通过（与 _page_contains 对齐）"""
    from agent.core.planner import TaskStep

    snap = Snapshot(
        title="结果页",
        url="https://example.com/result",
        texts=[ElementInfo(text="已找到关于 北京时间 的结果", element_id="e9",
                           tag="p", element_type="text")],
    )
    agent, planner, tool = make_step_agent(
        [TaskStep(description="验收", kind="verify",
                  params={"type": "text", "value": "北京时间"})],
        snapshot=snap,
    )

    obs = await agent.run("任务")

    assert obs.success is True
    assert planner.plan_calls == []      # verify 步骤不经过 planner


@pytest.mark.asyncio
async def test_observer_exception_returns_graceful_fail():
    """观察页面抛异常（浏览器被关闭）→ Agent 优雅失败而非崩溃"""
    observer = MagicMock()
    observer.observe = AsyncMock(side_effect=Exception("Target closed"))
    planner = _PlannerStub([done()])
    tool = MagicMock()
    tool.current_url = "https://example.com"

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("任务")

    assert obs.is_error is True
    assert "观察页面失败" in obs.error


@pytest.mark.asyncio
async def test_task_planner_drives_two_phase_flow():
    """TaskPlanner 完整链路：拆解 → goto/input/wait/click 分步执行 → 队列耗尽完成"""
    from agent.core.planner import TaskPlanner

    snapshot = _search_snapshot()
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snapshot)

    client = MockLLMClient([
        # 1. 拆解
        {"steps": [
            {"description": "打开百度", "kind": "action"},
            {"description": "输入关键词", "kind": "action"},
            {"description": "等待页面加载", "kind": "wait", "params": {"ms": 1000}},
            {"description": "点击结果链接", "kind": "action"},
        ]},
        # 2-4. 各 action 步骤的决策
        {"action": "goto", "value": "https://www.baidu.com"},
        {"action": "input", "target_id": "e0", "value": "北京时间"},
        {"action": "click", "target_id": "e1"},
    ])
    planner = TaskPlanner(client, model="mock", timeout=1000)

    tool = MagicMock()
    tool.goto = AsyncMock(return_value=Observation.ok(url="https://www.baidu.com", title="百度", page_changed=True))
    tool.input = AsyncMock(return_value=Observation.ok())
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.wait = AsyncMock(return_value=Observation.ok())
    tool.current_url = "https://example.com/search"
    tool.current_title = AsyncMock(return_value="搜索页")

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    obs = await agent.run("打开百度搜索北京时间")

    assert obs.success is True
    assert obs.data.get("done") is True
    assert len(agent.history) == 4           # goto/input/wait/click 各记一条
    tool.goto.assert_awaited_once()
    tool.input.assert_awaited_once()
    tool.wait.assert_awaited_once_with(ms=1000)
    tool.click.assert_awaited_once()
    assert client.call_count == 4            # 1 次拆解 + 3 次分步决策


# ── V0.4 前瞻：失败重试（机械 1 次 → Reflection 1 次 → 中止） ────────

@pytest.mark.asyncio
async def test_frame_retry_pins_locator_without_reobserve():
    """待解决问题 #2：iframe Action 失败后机械重试不重新 Observe，
    以原始 Snapshot 的显式 selector 定位原元素 —— 新 Snapshot 的
    element_id 会重新编号，沿用旧 target_id 可能命中错位元素"""
    def snap(frame_path, selector):
        s = Snapshot(title="t", url="https://example.com")
        s.buttons = [ElementInfo(
            text="btn", tag="button", element_type="button",
            selector=selector, element_id="e1", frame_path=frame_path,
        )]
        return s

    snap_before = snap(("iframe >> nth=0",), "#frame-btn")
    # 诱饵快照：若错误地重新 Observe 后沿用旧 target_id，将命中
    # 序号错位的其他元素（不同 selector / 不同 frame）
    snap_decoy = snap(("iframe >> nth=1",), "#evil-btn")

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snap_decoy)

    planner = _PlannerStub([])
    tool = MagicMock()
    tool.click = AsyncMock(side_effect=[
        Observation.fail("元素未就绪"),     # 首次失败
        Observation.ok(page_changed=True),  # 固定定位后重试成功
    ])
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="t")

    agent = Agent(observer, planner, Executor(tool), max_steps=5)
    action = Action(action="click", target_id="e1")

    obs, final_action = await agent._execute_action_with_retry(action, snap_before, step=1)

    assert obs.success is True
    assert tool.click.await_count == 2
    # 重试使用原始 Snapshot 中该元素的显式 selector 与原 frame_path，
    # 而非诱饵快照中同序号元素的定位信息
    assert tool.click.call_args_list[1].args[0] == "#frame-btn"
    assert tool.click.call_args_list[1].kwargs["frame_path"] == ("iframe >> nth=0",)
    assert observer.observe.await_count == 0       # 全程未重新 Observe
    # 定位改写只作用于重试副本，不污染原 Action
    assert not action.params.get("selector")
    assert final_action is action


@pytest.mark.asyncio
async def test_frame_action_retry_without_observe_stays_snapshot():
    """V1.0 #5：非 iframe Action 失败重试不触发重新 Observe，保持原行为"""
    snap_plain = Snapshot(title="t", url="https://example.com")
    snap_plain.buttons = [ElementInfo(
        text="btn", tag="button", element_type="button",
        selector="#plain-btn", element_id="e2",
    )]  # frame_path 默认 () 主页面

    observer = MagicMock()
    observer.observe = AsyncMock(return_value=snap_plain)
    planner = _PlannerStub([])
    tool = MagicMock()
    tool.click = AsyncMock(side_effect=[
        Observation.fail("瞬时失败"),
        Observation.ok(page_changed=True),
    ])
    tool.current_url = "https://example.com"
    tool.current_title = AsyncMock(return_value="t")

    agent = Agent(observer, planner, Executor(tool), max_steps=5)
    action = Action(action="click", target_id="e2")

    obs, _ = await agent._execute_action_with_retry(action, snap_plain, step=1)

    assert obs.success is True
    assert tool.click.await_count == 2
    # 主页面 action 复用原 snapshot，不额外观察
    assert observer.observe.await_count == 0


@pytest.mark.asyncio
async def test_mechanical_retry_recovers():
    """执行失败后机械重试 1 次成功 → 任务继续并完成"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(side_effect=[
        Observation.fail("元素暂时不可见"),
        Observation.ok(page_changed=True),
    ])

    obs = await agent.run("点击按钮")

    assert obs.success is True
    assert tool.click.await_count == 2   # 首次失败 + 机械重试成功
    assert len(agent.history) == 1       # 成功动作只记录一次


@pytest.mark.asyncio
async def test_reflection_recovers_after_mechanical_failure():
    """机械重试失败后，Reflection 给出替代动作并成功 → 任务继续"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(side_effect=[
        Observation.fail("元素不存在"),      # 首次
        Observation.fail("元素不存在"),      # 机械重试
        Observation.ok(page_changed=True),   # Reflection 替代动作
    ])
    planner.reflect = AsyncMock(return_value=Action(
        action="click", params={"selector": "#alt-btn"}
    ))

    obs = await agent.run("点击按钮")

    assert obs.success is True
    assert tool.click.await_count == 3   # 首次 + 机械重试 + Reflection 替代动作
    planner.reflect.assert_awaited_once()
    assert planner.reflect.call_args.args[3].params["selector"] == "#btn"


@pytest.mark.asyncio
async def test_all_retries_fail_aborts_task():
    """重试与 Reflection 均失败 → 中止任务并返回失败 Observation"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),  # 不应被执行
    ])
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    planner.reflect = AsyncMock(return_value=None)

    obs = await agent.run("点击按钮")

    assert obs.is_error is True
    assert "元素不存在" in obs.error
    assert tool.click.await_count == 2   # 首次 + 机械重试，Reflection 放弃后停止
    planner.reflect.assert_awaited_once()
    assert len(agent.history) == 1       # 最终失败动作记录一次
    assert agent.history[0]["observation"].is_error is True


@pytest.mark.asyncio
async def test_planner_without_reflect_only_mechanical_retry():
    """Planner 不支持 Reflection（基类默认）→ 仅机械重试后中止"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(return_value=Observation.fail("失败"))
    # planner 是 _PlannerStub，没有 reflect 属性 → 走基类默认（无 reflect 调用）

    obs = await agent.run("点击按钮")

    assert obs.is_error is True
    assert tool.click.await_count == 2
    assert len(agent.history) == 1


# ── V0.4：后退/刷新恢复（失败后重置页面再重新规划） ──────────────────

@pytest.mark.asyncio
async def test_recover_via_back_then_retry_succeeds():
    """失败后后退恢复成功 → 重新规划 → 任务最终完成"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    tool.back = AsyncMock(return_value=Observation.ok(page_changed=True))
    tool.refresh = AsyncMock(return_value=Observation.ok())

    obs = await agent.run("点击按钮")

    assert obs.success is True
    tool.back.assert_awaited_once()        # 后退恢复 1 次
    tool.refresh.assert_not_awaited()      # back 成功则不再 refresh
    assert tool.click.await_count == 2     # 首次 + 机械重试（恢复后 done 不再点击）
    assert len(agent.history) == 1         # 失败的 click 记录一次


@pytest.mark.asyncio
async def test_recover_falls_back_to_refresh():
    """后退不可用 → 刷新恢复成功 → 任务继续"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    tool.back = AsyncMock(return_value=Observation.fail("无可后退页面"))
    tool.refresh = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs = await agent.run("点击按钮")

    assert obs.success is True
    tool.back.assert_awaited_once()
    tool.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_exhausted_aborts():
    """恢复次数达到上限后再次失败 → 中止任务"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        Action(action="click", params={"selector": "#btn"}),
        done(),  # 不应被执行
    ])
    agent._max_recoveries = 1
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    tool.back = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs = await agent.run("点击按钮")

    assert obs.is_error is True
    assert "元素不存在" in obs.error
    assert tool.back.await_count == 1      # 恢复 1 次后达到上限
    assert len(agent.history) == 2         # 两次失败的 click 各记录一次


@pytest.mark.asyncio
async def test_recovery_unavailable_aborts():
    """back/refresh 均不可用 → 恢复失败直接中止"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    tool.back = AsyncMock(return_value=Observation.fail("back 失败"))
    tool.refresh = AsyncMock(return_value=Observation.fail("refresh 失败"))

    obs = await agent.run("点击按钮")

    assert obs.is_error is True
    tool.back.assert_awaited_once()
    tool.refresh.assert_awaited_once()
    assert len(agent.history) == 1


# ── V0.6：动作结果反馈 Planner（on_action_result，待解决问题 #1）─────

class _NotifyingPlannerStub(_PlannerStub):
    """记录 on_action_result 调用与参数（含最终执行的动作）。"""

    def __init__(self, actions=None):
        super().__init__(actions)
        self.results: list[tuple] = []

    def on_action_result(self, action, observation):
        self.results.append((action, observation))


@pytest.mark.asyncio
async def test_agent_notifies_planner_result_on_success_and_failure():
    """Agent 每次动作执行后都调用 planner.on_action_result（成功与失败均通知）"""
    # 失败场景：click 始终失败 → Agent 中止 → 通知一次 (click, fail)
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),  # 不应被执行
    ])
    planner.__class__ = _NotifyingPlannerStub  # 替换为带通知实现的桩
    planner.results = []                        # 切换类后补初始化
    agent._max_recoveries = 0
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))

    obs = await agent.run("点击按钮")

    assert obs.is_error is True
    assert len(planner.results) == 1
    failed_action, failed_obs = planner.results[0]
    assert failed_action.action == "click"
    assert failed_obs.is_error is True      # 失败结果原样反馈

    # 成功场景：click 成功 → done → 通知一次 (click, ok)
    agent2, planner2, tool2 = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),
    ])
    planner2.__class__ = _NotifyingPlannerStub
    planner2.results = []                       # 切换类后补初始化
    tool2.click = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs2 = await agent2.run("点击按钮")

    assert obs2.success is True
    assert len(planner2.results) == 1
    ok_action, ok_obs = planner2.results[0]
    assert ok_action.action == "click"
    assert ok_obs.is_error is False


@pytest.mark.asyncio
async def test_agent_notifies_planner_on_final_reflect_action():
    """Reflection 替代动作执行后，通知的是最终执行的动作"""
    agent, planner, tool = make_mocks([
        Action(action="click", params={"selector": "#btn"}),
        done(),  # 不应被执行
    ])
    planner.__class__ = _NotifyingPlannerStub
    planner.results = []                        # 切换类后补初始化
    # 机械重试失败 → Reflection 返回 refresh 替代动作 → 成功
    tool.click = AsyncMock(return_value=Observation.fail("元素不存在"))
    tool.refresh = AsyncMock(return_value=Observation.ok(page_changed=True))
    agent._max_recoveries = 0  # 避免干扰 Reflection 路径

    async def fake_reflect(snapshot, goal, history, action, error):
        return Action(action="refresh")

    planner.reflect = fake_reflect

    obs = await agent.run("点击按钮")

    assert obs.success is True
    assert len(planner.results) == 1
    final_action, final_obs = planner.results[0]
    assert final_action.action == "refresh"  # 最终执行的是替代动作
    assert final_obs.success is True


# ── V1.0 批量增强：重复动作检测（同 target 相同动作 + 页面无变化 → 跳过） ──

@pytest.mark.asyncio
async def test_repeat_action_detection_skips_duplicate():
    """LLM 连续对同一 target 输出相同动作且页面无变化 → 第二次被跳过不再执行"""
    agent, planner, tool = make_mocks([
        Action(action="input", target_id="e0", value="admin",
               params={"selector": "#user"}),
        Action(action="input", target_id="e0", value="admin",
               params={"selector": "#user"}),
        done(),
    ])
    tool.input = AsyncMock(return_value=Observation.ok())  # 页面无变化

    obs = await agent.run("登录")

    assert obs.success is True
    assert tool.input.await_count == 1   # 重复动作被跳过，只真正执行一次
    skipped = [e for e in agent.history
               if e["observation"].is_error and "重复" in (e["observation"].error or "")]
    assert len(skipped) == 1             # 跳过记录写入 history 供 LLM 感知


@pytest.mark.asyncio
async def test_repeat_action_detection_resets_after_page_change():
    """页面变化后重复基准清空 → 相同动作不再误判为重复"""
    agent, planner, tool = make_mocks([
        Action(action="click", target_id="e1", params={"selector": "#a"}),
        Action(action="click", target_id="e1", params={"selector": "#a"}),
        done(),
    ])
    tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs = await agent.run("点击")

    assert obs.success is True
    assert tool.click.await_count == 2   # 每次点击都改变页面，不触发重复检测


# ── #7 / #8：步骤模式 wait 失败重试 / 手动 API 异常防护 ──────────────

@pytest.mark.asyncio
async def test_step_mode_wait_failure_mechanical_retry_succeeds():
    """#7 计划内 wait 失败 → 机械重试 1 次成功 → 任务继续完成"""
    from agent.core.planner import TaskStep

    agent, planner, tool = make_step_agent([
        TaskStep(description="等待加载", kind="wait", params={"ms": 1000}),
        TaskStep(description="点击按钮", kind="action"),
    ], actions_by_index={1: [Action(action="click", params={"selector": "#btn"})]})
    tool.wait = AsyncMock(side_effect=[
        Observation.fail("浏览器暂不可用"),   # 首次失败
        Observation.ok(page_changed=True),    # 机械重试成功
    ])

    obs = await agent.run("任务")

    assert obs.success is True
    assert tool.wait.await_count == 2   # 首次失败 + 机械重试成功
    tool.click.assert_awaited_once()
    # 待解决问题 #41：wait 失败→机械重试成功的中间失败不入史，只记最终成功一次。
    # 步骤 1 = wait、步骤 2 = click，各记一条 → 历史共 2 条（而非 3 条）。
    assert len(agent.history) == 2, f"wait 重试不应重复入史: {len(agent.history)}"


@pytest.mark.asyncio
async def test_step_mode_wait_failure_recovers_page_then_succeeds():
    """#7 计划内 wait 失败且机械重试也失败 → 页面恢复后重新执行 → 任务完成"""
    from agent.core.planner import TaskStep

    agent, planner, tool = make_step_agent([
        TaskStep(description="等待加载", kind="wait", params={"ms": 1000}),
    ])
    tool.wait = AsyncMock(side_effect=[
        Observation.fail("失败 1"),           # 首次
        Observation.fail("失败 2"),           # 机械重试
        Observation.ok(page_changed=True),    # 恢复后重新执行成功
    ])
    tool.back = AsyncMock(return_value=Observation.ok(page_changed=True))

    obs = await agent.run("任务")

    assert obs.success is True
    assert tool.wait.await_count == 3
    tool.back.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_step_returns_fail_observation_on_browser_closed():
    """#8 手动 step()：浏览器关闭（执行器抛异常）→ 返回失败 Observation 而非崩溃"""
    observer = MagicMock()
    observer.observe = AsyncMock(return_value=Snapshot(title="t", url="https://example.com"))
    planner = _PlannerStub([])
    tool = MagicMock()
    tool.click = AsyncMock(side_effect=Exception("Target closed"))
    tool.current_url = "https://example.com"

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    result = await agent.step(Action(action="click", params={"selector": "#btn"}))

    assert isinstance(result, Observation)
    assert result.is_error is True
    assert "浏览器可能已关闭" in result.error


@pytest.mark.asyncio
async def test_manual_observe_returns_none_on_browser_closed():
    """#8 手动 observe()：浏览器关闭（观察器抛异常）→ 返回 None 而非崩溃"""
    observer = MagicMock()
    observer.observe = AsyncMock(side_effect=Exception("Target closed"))
    planner = _PlannerStub([])
    tool = MagicMock()
    tool.current_url = "https://example.com"

    agent = Agent(observer, planner, Executor(tool), max_steps=10)
    result = await agent.observe()

    assert result is None
