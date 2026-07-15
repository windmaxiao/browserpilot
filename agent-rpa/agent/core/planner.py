"""
Planner —— 规划器

根据 Snapshot 规划下一个 Action。
V0.1 中使用规则实现（Rule-based），V0.3 后替换为 LLM。

设计原则：
- 输入：Snapshot（页面认知）
- 输出：Action（下一步操作）
- 不直接操作浏览器
"""

from __future__ import annotations

from agent.schema.action import Action
from agent.schema.snapshot import Snapshot


class Planner:
    """
    规划器 —— 根据页面状态决定下一步动作。

    V0.1: 简单规则实现
    V0.2: 更完善的规则引擎
    V0.3+: 替换为 LLM Planner
    """

    async def plan(self, snapshot: Snapshot, goal: str) -> Action | None:
        """
        根据当前页面 Snapshot 和目标，规划下一个动作。

        Args:
            snapshot: 当前页面的结构化认知
            goal: 用户目标描述

        Returns:
            下一步 Action，或 None 表示无法规划
        """
        # TODO(V0.2): 实现规则引擎
        # TODO(V0.3): 替换为 LLM 调用
        raise NotImplementedError("Planner 尚未实现")

    async def plan_with_history(
        self,
        snapshot: Snapshot,
        goal: str,
        history: list[dict],
    ) -> Action | None:
        """
        带历史记录的规划（V0.4+ 使用）。

        Args:
            snapshot: 当前页面 Snapshot
            goal: 用户目标
            history: 历史操作列表

        Returns:
            下一步 Action
        """
        return await self.plan(snapshot, goal)


class RuleBasedPlanner(Planner):
    """
    基于规则的规划器（V0.2 实现）。

    规则示例：
    - 如果有登录表单 → 填入凭据
    - 如果页面有搜索框 → 输入关键词
    - 如果有"下一页"按钮 → 点击
    """

    def __init__(self):
        self._rules: list[tuple] = []

    def add_rule(self, condition, action_fn):
        """添加规则：condition(snapshot, goal) → bool, action_fn(snapshot) → Action"""
        self._rules.append((condition, action_fn))

    async def plan(self, snapshot: Snapshot, goal: str) -> Action | None:
        for condition, action_fn in self._rules:
            if condition(snapshot, goal):
                return action_fn(snapshot)
        return None
