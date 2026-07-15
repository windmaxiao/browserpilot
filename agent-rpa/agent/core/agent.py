"""
Agent —— 主循环

负责协调 Observer、Planner、Executor 完成自动化任务。

Agent Loop 流程：
```
Goal → Snapshot → Planner → Action → Executor → Observation → Loop
```

设计原则：
- Agent 负责决策，不直接操作 Playwright
- 高内聚、低耦合
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import Planner
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot

logger = logging.getLogger(__name__)


class Agent:
    """
    Agent 主循环。

    V0.1: 手动模式（直接调用工具）
    V0.2: 规则驱动模式
    V0.3: LLM 驱动模式
    V1.0: 带记忆和 Reflection 的完整模式
    """

    def __init__(
        self,
        observer: Observer,
        planner: Planner,
        executor: Executor,
        max_steps: int = 50,
    ):
        self._observer = observer
        self._planner = planner
        self._executor = executor
        self._max_steps = max_steps

        # 运行时状态
        self._history: list[dict] = []
        self._current_step: int = 0
        self._goal: str = ""

    # ── 公开接口 ────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        """返回历史操作记录"""
        return list(self._history)

    @property
    def current_step(self) -> int:
        return self._current_step

    @property
    def goal(self) -> str:
        return self._goal

    async def run(self, goal: str) -> Observation:
        """
        运行 Agent 完成任务。

        Args:
            goal: 用户目标描述

        Returns:
            最终 Observation（包含 done=True 或错误信息）
        """
        self._goal = goal
        self._history.clear()
        self._current_step = 0

        logger.info(f"Agent 启动 | 目标: {goal}")

        while self._current_step < self._max_steps:
            self._current_step += 1

            # 1. Observe
            logger.info(f"[Step {self._current_step}] 观察页面...")
            snapshot = await self._observer.observe()
            self._log_snapshot(snapshot)

            # 2. Plan
            logger.info(f"[Step {self._current_step}] 规划动作...")
            action = await self._planner.plan(snapshot, goal)

            if action is None:
                return Observation.fail(
                    error=f"在第 {self._current_step} 步无法规划出有效动作",
                    url=snapshot.url,
                )

            # 3. Check done
            if action.action == "done":
                logger.info(f"Agent 完成任务: {action.value or goal}")
                return Observation.ok(
                    url=snapshot.url,
                    title=snapshot.title,
                    data={"done": True, "message": action.value or "任务完成"},
                )

            # 4. Execute
            logger.info(
                f"[Step {self._current_step}] 执行: {action.action}({action.target})"
            )
            observation = await self._executor.execute(action)

            # 5. Record history
            self._history.append({
                "step": self._current_step,
                "action": action,
                "observation": observation,
            })

            # 6. Check failure
            if observation.is_error:
                logger.warning(
                    f"[Step {self._current_step}] 动作失败: {observation.error}"
                )
                # TODO(V0.4): 触发 Reflection 重试
                return observation

        # 超出最大步数
        return Observation.fail(
            error=f"超出最大步数限制 ({self._max_steps})",
        )

    async def step(self, action: Action) -> Observation:
        """
        单步执行一个 Action（手动模式 / 调试用）。

        Args:
            action: 要执行的动作

        Returns:
            执行结果 Observation
        """
        return await self._executor.execute(action)

    async def observe(self) -> Snapshot:
        """获取当前页面的 Snapshot（手动模式 / 调试用）"""
        return await self._observer.observe()

    # ── 内部方法 ────────────────────────────────────────────────────

    def _log_snapshot(self, snapshot: Snapshot):
        """记录 Snapshot 摘要到日志"""
        btn_count = len(snapshot.buttons)
        input_count = len(snapshot.inputs)
        link_count = len(snapshot.links)
        logger.debug(
            f"页面: {snapshot.title} | "
            f"按钮={btn_count} 输入框={input_count} 链接={link_count}"
        )
