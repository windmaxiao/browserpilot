"""
Memory 测试（V0.5）

覆盖：
- summarize_entries 规则式摘要（Action/Observation 对象与 dict 兼容、敏感字段排除）
- HistoryMemory 折叠阈值 / 增量只摘要一次 / 窗口边界 / clear / 自定义摘要器
- serialize_history / build_user_prompt 摘要协议（摘要不占窗口、纯列表回归）
- Agent 集成：长任务 Planner 收到摘要、history 原始完整
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.memory import HistoryMemory, summarize_entries
from agent.prompts.planner import build_user_prompt, serialize_history
from agent.schema.action import Action
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot


def _entry(
    step: int,
    action: str = "click",
    target: str = "",
    success: bool = True,
    value: str = "敏感值",
) -> dict:
    """构造一条与 Agent 一致的历史条目（value 仅验证摘要排除用）。"""
    return {
        "step": step,
        "action": Action(action=action, target=target, value=value),
        "observation": (
            Observation.ok() if success else Observation.fail(error="执行失败")
        ),
    }


class TestSummarizeEntries:
    def test_formats_action_observation_objects(self):
        entries = [
            _entry(1, action="goto"),
            _entry(2, action="click", target="登录"),
            _entry(3, action="input", target="搜索框"),
        ]
        text = summarize_entries(entries)
        assert text.splitlines() == [
            "goto 成功",
            "click「登录」 成功",
            "input「搜索框」 成功",
        ]

    def test_marks_failure(self):
        text = summarize_entries([_entry(1, action="click", target="登录", success=False)])
        assert text == "click「登录」 失败"

    def test_handles_dict_entries(self):
        entries = [{"action": {"action": "click", "target": "提交"}, "observation": None}]
        assert summarize_entries(entries) == "click「提交」 未知"

    def test_excludes_sensitive_value(self):
        """摘要不得包含输入值等敏感内容（与 serialize_history 白名单一致）"""
        text = summarize_entries([_entry(1, action="input", target="密码框", value="secret123")])
        assert "secret123" not in text
        assert text == "input「密码框」 成功"

    def test_empty_input(self):
        assert summarize_entries([]) == ""


class TestHistoryMemory:
    def _memory(self, **kwargs) -> HistoryMemory:
        return HistoryMemory(window=5, summarize_batch=10, **kwargs)

    @pytest.mark.asyncio
    async def test_no_fold_below_threshold(self):
        mem = self._memory()
        for i in range(1, 15):  # 14 < window(5) + batch(10)，未触发折叠
            mem.add(_entry(i))
        await mem.maybe_summarize()
        assert mem.summary == ""
        assert mem.count == 14
        # 上下文始终有界：只有最近窗口（无摘要时窗口即全部上下文）
        ctx = mem.context_entries()
        assert len(ctx) == 5
        assert [e["step"] for e in ctx] == [10, 11, 12, 13, 14]

    @pytest.mark.asyncio
    async def test_fold_at_threshold(self):
        mem = self._memory()
        for i in range(1, 16):  # 15 >= 5 + 10 → 折叠第一批 10 条
            mem.add(_entry(i))
        await mem.maybe_summarize()
        assert mem.count == 15
        # 摘要 1 批（10 行），窗口 5 条
        assert mem.summary.splitlines() == ["click 成功"] * 10
        ctx = mem.context_entries()
        assert ctx[0] == {"kind": "summary", "text": mem.summary}
        assert len(ctx) == 6  # 1 摘要 + 5 窗口

    @pytest.mark.asyncio
    async def test_recent_only_unsummarized(self):
        mem = self._memory()
        for i in range(1, 16):
            mem.add(_entry(i))
        await mem.maybe_summarize()
        assert len(mem.recent) == 5
        assert [e["step"] for e in mem.recent] == [11, 12, 13, 14, 15]

    @pytest.mark.asyncio
    async def test_incremental_folding_folds_each_entry_once(self):
        mem = self._memory()
        for i in range(1, 26):  # 第二次折叠：达到 25 条 → 两批各 10 条
            mem.add(_entry(i))
        await mem.maybe_summarize()
        assert mem.count == 25
        assert len(mem.summary.splitlines()) == 20  # 10 + 10，各被摘要一次
        assert len(mem.context_entries()) == 6

    @pytest.mark.asyncio
    async def test_custom_summarizer_called_once_per_batch(self):
        batches: list[list] = []

        async def fake_summarizer(batch):
            batches.append([e["step"] for e in batch])
            return "LLM 摘要"

        mem = self._memory(summarizer=fake_summarizer)
        for i in range(1, 26):
            mem.add(_entry(i))
            await mem.maybe_summarize()
        # 只对折叠批次调用：两批，每批 10 条，条目不重复
        assert batches == [list(range(1, 11)), list(range(11, 21))]
        assert mem.summary == "LLM 摘要\nLLM 摘要"

    @pytest.mark.asyncio
    async def test_empty_summary_from_custom_summarizer_skipped(self):
        async def fake_summarizer(batch):
            return "   "

        mem = self._memory(summarizer=fake_summarizer)
        for i in range(1, 16):
            mem.add(_entry(i))
            await mem.maybe_summarize()
        assert mem.summary == ""

    def test_clear(self):
        mem = self._memory()
        for i in range(1, 16):
            mem.add(_entry(i))
        mem.clear()
        assert mem.count == 0
        assert mem.summary == ""
        assert mem.context_entries() == []

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            HistoryMemory(window=0)


class TestSerializeHistorySummary:
    def _raw_entries(self, n: int):
        return [_entry(i) for i in range(1, n + 1)]

    def test_summary_rendered_at_head_and_not_in_window(self):
        history = [{"kind": "summary", "text": "早期摘要"}] + self._raw_entries(8)
        out = serialize_history(history, max_items=3)
        assert out[0] == {"summary": "早期摘要"}
        assert len(out) == 4  # 1 摘要 + 3 窗口（摘要不占名额）
        assert [e["action"] for e in out[1:]] == ["click"] * 3

    def test_plain_list_regression(self):
        """无摘要条目时行为与 V0.3 完全一致"""
        out = serialize_history(self._raw_entries(6), max_items=3)
        assert len(out) == 3
        assert all("summary" not in e for e in out)
        assert [e["action"] for e in out] == ["click"] * 3


class TestBuildUserPromptSummary:
    def test_summary_stays_at_head_despite_window(self):
        history = [{"summary": "早期摘要"}] + [
            {"action": f"a{i}", "success": True, "url": "u"} for i in range(8)
        ]
        prompt = build_user_prompt("g", {"title": "t"}, history, max_history=3)
        rendered = json.loads(prompt.split("最近历史:\n")[1].split("\n\n")[0])
        assert rendered[0] == {"summary": "早期摘要"}
        assert len(rendered) == 4  # 1 摘要 + 3 窗口


class TestAgentMemoryIntegration:
    def _make_agent(self, actions: list[Action]):
        snapshot = Snapshot(title="测试页", url="https://example.com")

        observer = MagicMock()
        observer.observe = AsyncMock(return_value=snapshot)

        class _RecordingPlanner:
            def __init__(self, actions):
                self._actions = list(actions)
                self.histories: list[list] = []

            async def plan_with_history(self, snapshot, goal, history):
                self.histories.append(list(history))
                return self._actions.pop(0) if self._actions else None

        planner = _RecordingPlanner(actions)

        tool = MagicMock()
        tool.click = AsyncMock(return_value=Observation.ok(page_changed=True))

        executor = Executor(tool)
        agent = Agent(observer, planner, executor, max_steps=20)
        return agent, planner

    @pytest.mark.asyncio
    async def test_long_run_feeds_summary_to_planner(self):
        actions = [
            Action(action="click", params={"selector": "#a"}) for _ in range(16)
        ] + [Action(action="done")]
        agent, planner = self._make_agent(actions)

        obs = await agent.run("长任务")

        assert obs.success is True
        # done 不进入执行链，history 保留 16 条原始记录
        assert len(agent.history) == 16
        # 15 条记录后触发折叠：摘要非空
        assert agent.memory.summary
        # 最后一次规划收到的上下文 = 摘要 + 窗口
        last = planner.histories[-1]
        assert last[0]["kind"] == "summary"
        assert len(last) == 1 + 5

    @pytest.mark.asyncio
    async def test_short_run_no_summary(self):
        actions = [
            Action(action="click", params={"selector": "#a"}) for _ in range(3)
        ] + [Action(action="done")]
        agent, planner = self._make_agent(actions)

        await agent.run("短任务")

        assert agent.memory.summary == ""
        assert all(not h or h[0].get("kind") != "summary" for h in planner.histories)
