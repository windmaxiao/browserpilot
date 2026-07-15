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

from loguru import logger

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
        logger.warning("Planner.plan() 未实现（V0.1 骨架），无法规划动作")
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
        logger.debug("plan_with_history 调用，历史记录数: {}", len(history))
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
        logger.info("RuleBasedPlanner 初始化，规则数: 0")

    def add_rule(self, condition, action_fn):
        """添加规则：condition(snapshot, goal) → bool, action_fn(snapshot) → Action"""
        self._rules.append((condition, action_fn))
        logger.debug("添加规则，当前规则总数: {}", len(self._rules))

    async def plan(self, snapshot: Snapshot, goal: str) -> Action | None:
        for i, (condition, action_fn) in enumerate(self._rules):
            if condition(snapshot, goal):
                action = action_fn(snapshot)
                logger.info("规则 {} 命中 → action: {}", i + 1, action.action)
                return action
        logger.debug("无规则命中，返回 None")
        return None
