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

from typing import Optional

from loguru import logger

from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import Planner, TaskQueue, TaskStep
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot


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

        logger.debug("Agent 初始化完成 | max_steps={}", max_steps)

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

        两种模式（由 Planner 是否支持拆解决定）：
        - 步骤模式：Planner.decompose() 返回步骤队列，按队列逐项执行，
          等待 / 验收类步骤由框架直接处理（V0.4 前瞻）。
        - 自由模式：Planner 不支持拆解（或拆解失败），退化为单目标循环，
          LLM 自主决策直到输出 done。

        Args:
            goal: 用户目标描述

        Returns:
            最终 Observation（包含 done=True 或错误信息）
        """
        self._goal = goal
        self._history.clear()
        self._current_step = 0

        decompose = getattr(self._planner, "decompose", None)
        steps = await decompose(goal) if decompose is not None else None
        if steps:
            return await self._run_with_steps(goal, steps)
        return await self._run_free(goal)

    async def _run_free(self, goal: str) -> Observation:
        """自由模式：单目标循环，LLM 自主决策直到输出 done（旧 V0.3 行为）。"""
        # 循环检测（V0.4 前瞻）：连续无效果等待计数
        consecutive_waits = 0

        logger.info("🧠 Agent 启动（自由模式）| 目标: {}", goal)

        while self._current_step < self._max_steps:
            self._current_step += 1

            # 1. Observe
            logger.info("📷 [Step {}/{}] 观察页面...", self._current_step, self._max_steps)
            snapshot = await self._safe_observe()
            if snapshot is None:
                return Observation.fail(
                    error=f"在第 {self._current_step} 步观察页面失败（浏览器可能已关闭）",
                )
            self._log_snapshot(snapshot)

            # 2. Plan
            logger.info("📝 [Step {}/{}] 规划动作...", self._current_step, self._max_steps)
            # V0.3 起通过 plan_with_history 传入有限历史（基类默认忽略历史转发 plan）
            action = await self._planner.plan_with_history(snapshot, goal, self._history)

            if action is None:
                logger.warning("⚠️  [Step {}] 无法规划出有效动作", self._current_step)
                return Observation.fail(
                    error=f"在第 {self._current_step} 步无法规划出有效动作",
                    url=snapshot.url,
                )

            # 3. Check done
            if action.action == "done":
                logger.info("✅ Agent 完成任务: {}", action.value or goal)
                return Observation.ok(
                    url=snapshot.url,
                    title=snapshot.title,
                    data={"done": True, "message": action.value or "任务完成"},
                )

            # 4. Execute（失败自动重试：机械 1 次 → Reflection 1 次）
            self._log_action(action, self._current_step)
            observation, final_action = await self._execute_action_with_retry(
                action, snapshot, step=self._current_step,
            )
            if observation is None:
                return Observation.fail(
                    error=f"在第 {self._current_step} 步动作执行异常（浏览器可能已关闭）",
                )

            # 5. Record history
            self._history.append({
                "step": self._current_step,
                "action": final_action,
                "observation": observation,
            })

            # 6. Check failure（重试与 Reflection 均失败 → 中止任务）
            if observation.is_error:
                logger.warning("❌ [Step {}] 动作失败（已重试）: {}", self._current_step, observation.error)
                return observation

            # 7. 循环检测：连续等待且页面无变化 → 任务停滞，提前终止
            if final_action.action == "wait" and not observation.page_changed:
                consecutive_waits += 1
            else:
                consecutive_waits = 0
            if consecutive_waits >= 2:
                logger.warning(
                    "⚠️ [Step {}] 连续 {} 次等待且页面无变化，判定任务停滞",
                    self._current_step, consecutive_waits,
                )
                return Observation.fail(
                    error="任务停滞：连续等待且页面无变化",
                    url=snapshot.url,
                )

            logger.info("✅ [Step {}] 成功 | URL: {}", self._current_step, observation.url)

        # 超出最大步数
        logger.warning("⚠️  超出最大步数限制 ({})", self._max_steps)
        return Observation.fail(
            error=f"超出最大步数限制 ({self._max_steps})",
        )

    async def _run_with_steps(self, goal: str, steps: list[TaskStep]) -> Observation:
        """步骤模式：按拆解队列逐项执行；wait / verify 步骤由框架直接处理。

        步骤模式不做连续 wait 停滞检测：队列必然推进（成功即消费一步），
        且有 max_steps 上限兜底；计划内的等待（等待加载/固定延时）是用户
        明确要求的合法动作，不应误判停滞（自由模式才保留该检测）。
        """
        queue = TaskQueue(steps)
        logger.info(
            "🧠 Agent 启动（步骤模式）| 目标: {} | 共 {} 步",
            goal, len(queue),
        )

        while queue and self._current_step < self._max_steps:
            self._current_step += 1
            current = queue.peek()

            # 1. Observe
            snapshot = await self._safe_observe()
            if snapshot is None:
                return Observation.fail(
                    error=f"在第 {self._current_step} 步观察页面失败（浏览器可能已关闭）",
                )
            self._log_snapshot(snapshot)

            # 2. 框架直执行的步骤：wait / verify（不经过 LLM）
            if current.kind == "wait":
                ms = int(current.params.get("ms", 1000))
                action = Action(action="wait", params={"ms": ms})
                logger.info(
                    "⏱️  [Step {}] 计划内等待 {}ms（{}）",
                    self._current_step, ms, current.description,
                )
                observation = await self._safe_execute(action, snapshot)
                if observation is None:
                    return Observation.fail(
                        error=f"在第 {self._current_step} 步动作执行异常（浏览器可能已关闭）",
                    )
                self._history.append({
                    "step": self._current_step,
                    "action": action,
                    "observation": observation,
                })
                if observation.is_error:
                    return observation
                queue.pop()
                logger.info("✅ [Step {}] 等待完成 | URL: {}", self._current_step, observation.url)
                continue

            if current.kind == "verify":
                if self._verify_step(snapshot, current):
                    logger.info(
                        "✅ [Step {}] 步骤验收通过: {}", self._current_step, current.description,
                    )
                    queue.pop()
                    continue
                logger.warning(
                    "❌ [Step {}] 步骤验收未通过: {}", self._current_step, current.description,
                )
                return Observation.fail(
                    error=f"步骤验收未通过: {current.description}",
                    url=snapshot.url,
                )

            # 3. action 步骤：LLM 只聚焦当前步骤
            logger.info(
                "📝 [Step {}/{}] 规划动作（当前: {}）...",
                self._current_step, len(queue), current.description,
            )
            action = await self._planner.plan_step(
                snapshot, goal, self._history,
                steps=queue.steps(), current_index=queue.index,
            )

            if action is None:
                logger.warning("⚠️  [Step {}] 无法规划出有效动作", self._current_step)
                return Observation.fail(
                    error=f"在第 {self._current_step} 步无法规划出有效动作",
                    url=snapshot.url,
                )
            if action.action == "done":
                logger.info("✅ Agent 提前声明完成（第 {} 步）", self._current_step)
                return Observation.ok(
                    url=snapshot.url,
                    title=snapshot.title,
                    data={"done": True, "message": "任务完成"},
                )

            # 4. Execute（失败自动重试：机械 1 次 → Reflection 1 次）
            self._log_action(action, self._current_step)
            observation, final_action = await self._execute_action_with_retry(
                action, snapshot, step=self._current_step,
            )
            if observation is None:
                return Observation.fail(
                    error=f"在第 {self._current_step} 步动作执行异常（浏览器可能已关闭）",
                )

            # 5. Record history
            self._history.append({
                "step": self._current_step,
                "action": final_action,
                "observation": observation,
            })

            # 6. Check failure（重试与 Reflection 均失败 → 中止任务）
            if observation.is_error:
                logger.warning("❌ [Step {}] 动作失败（已重试）: {}", self._current_step, observation.error)
                return observation

            # 7. 推进队列（wait 步骤已在上面单独 pop；此处只推进 action/verify 已通过）
            queue.pop()

            logger.info("✅ [Step {}] 成功 | URL: {}", self._current_step, observation.url)

        # 队列耗尽 → 任务完成
        if not queue:
            logger.info("🎉 任务完成（所有步骤执行完毕）")
            return Observation.ok(
                data={"done": True, "message": "任务完成"},
            )

        logger.warning("⚠️  超出最大步数限制 ({})", self._max_steps)
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
        log_action = f"{action.action}({action.target})"
        if action.value:
            log_action += f" = {str(action.value)[:50]}"
        logger.info("⚡ 手动执行: {}", log_action)
        result = await self._executor.execute(action)
        if result.is_error:
            logger.warning("❌ 手动执行失败: {}", result.error)
        else:
            logger.info("✅ 手动执行成功 | URL: {}", result.url)
        return result

    async def observe(self) -> Snapshot:
        """获取当前页面的 Snapshot（手动模式 / 调试用）"""
        logger.info("📷 观察页面...")
        snapshot = await self._observer.observe()
        self._log_snapshot(snapshot)
        return snapshot

    # ── 内部方法 ────────────────────────────────────────────────────

    async def _safe_observe(self) -> Optional[Snapshot]:
        """观察页面；浏览器被关闭等异常时返回 None 而非抛崩溃。"""
        try:
            return await self._observer.observe()
        except Exception as e:
            logger.warning("⚠️  观察页面失败（浏览器可能已关闭）: {}", e)
            return None

    async def _safe_execute(
        self, action: Action, snapshot: Optional[Snapshot] = None,
    ) -> Optional[Observation]:
        """执行动作；浏览器被关闭等异常时返回 None 而非抛崩溃。"""
        try:
            return await self._executor.execute(action, snapshot=snapshot)
        except Exception as e:
            logger.warning("⚠️  动作执行异常（浏览器可能已关闭）: {}", e)
            return None

    async def _execute_action_with_retry(
        self,
        action: Action,
        snapshot: Optional[Snapshot],
        *,
        step: int,
    ) -> tuple[Optional[Observation], Action]:
        """执行动作并处理失败重试（V0.4 Reflection）。

        策略（用户确认）：
        1. 首次执行；
        2. 失败 → 机械重试 1 次（处理瞬时错误，如元素刚渲染）；
        3. 仍失败 → Reflection：Planner 分析失败原因给出替代动作并执行 1 次；
        4. 仍失败 → 返回最后一次失败 Observation（上层中止任务）。

        Returns:
            (observation, final_action)。observation 为 None 表示浏览器关闭等致命异常；
            final_action 为最终实际执行的动作（Reflection 后可能不同于原动作）。
        """
        # 1. 首次执行
        observation = await self._safe_execute(action, snapshot)
        if observation is None or not observation.is_error:
            return observation, action

        # 2. 机械重试 1 次
        logger.warning("🔁 [Step {}] 执行失败: {} → 机械重试", step, observation.error)
        retry = await self._safe_execute(action, snapshot)
        if retry is None:
            return None, action
        if not retry.is_error:
            logger.info("✅ [Step {}] 机械重试成功", step)
            return retry, action

        # 3. Reflection：让 Planner 分析失败原因并给出替代动作
        logger.warning("🔁 [Step {}] 机械重试仍失败: {} → 进入 Reflection", step, retry.error)
        reflect_fn = getattr(self._planner, "reflect", None)
        if reflect_fn is None:
            logger.warning("⚠️  [Step {}] Planner 不支持 Reflection，放弃该步", step)
            return retry, action
        reflect_action = await reflect_fn(
            snapshot, self._goal, self._history, action, retry.error or ""
        )
        if reflect_action is None:
            logger.warning("⚠️  [Step {}] Reflection 无替代动作，放弃该步", step)
            return retry, action
        self._log_action(reflect_action, step)
        reflect_obs = await self._safe_execute(reflect_action, snapshot)
        if reflect_obs is None:
            return None, reflect_action
        return reflect_obs, reflect_action

    def _log_action(self, action: Action, step: int) -> None:
        """记录 Action 摘要（value 可能为 int，统一转字符串防切片崩溃）。"""
        log_action = f"{action.action}({action.target})"
        if action.value:
            log_action += f" = {str(action.value)[:50]}"
        if action.target_id:
            log_action += f" [{action.target_id}]"
        logger.info("⚡ [Step {}] 执行: {}", step, log_action)

    @staticmethod
    def _verify_step(snapshot: Snapshot, step: TaskStep) -> bool:
        """校验步骤验收条件：verify.type=url → URL 包含；text → 标题/元素文本包含。"""
        value = step.params.get("value", "")
        if not value:
            return False
        vtype = step.params.get("type", "text")
        if vtype == "url":
            return value.lower() in (snapshot.url or "").lower()
        haystack = f"{snapshot.title or ''} " + " ".join(
            el.text for el in snapshot.get_interactive_elements()
        )
        return value.lower() in haystack.lower()

    def _log_snapshot(self, snapshot: Snapshot):
        """记录 Snapshot 摘要到日志"""
        btn_count = len(snapshot.buttons)
        input_count = len(snapshot.inputs)
        link_count = len(snapshot.links)
        select_count = len(snapshot.selects)
        logger.info(
            "📄 页面: {} | URL: {} | 按钮={} 输入框={} 链接={} 下拉={}",
            snapshot.title, snapshot.url,
            btn_count, input_count, link_count, select_count,
        )
