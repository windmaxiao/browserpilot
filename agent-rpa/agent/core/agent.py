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

import asyncio
from typing import Optional

from loguru import logger

from agent.core.executor import Executor
from agent.core.memory import HistoryMemory
from agent.core.observer import Observer
from agent.core.planner import Planner, TaskQueue, TaskStep
from agent.schema.action import Action, done
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot

# 重复动作检测（V1.0 批量增强）：连续 N 次对同一目标执行相同动作且页面无变化 → 判停滞
_MAX_REPEAT_SKIPS = 3


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
        max_recoveries: int = 2,
    ):
        self._observer = observer
        self._planner = planner
        self._executor = executor
        self._max_steps = max_steps
        self._max_recoveries = max_recoveries

        # 运行时状态
        self._history: list[dict] = []
        self._memory: HistoryMemory = HistoryMemory()
        self._current_step: int = 0
        self._goal: str = ""
        self._recovery_count: int = 0

        logger.debug("Agent 初始化完成 | max_steps={} max_recoveries={}", max_steps, max_recoveries)

    # ── 公开接口 ────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        """返回历史操作记录（原始完整列表）"""
        return list(self._history)

    @property
    def memory(self) -> HistoryMemory:
        """增量式历史记忆（V0.5）：摘要 + 滑动窗口 + 上下文压缩"""
        return self._memory

    @property
    def current_step(self) -> int:
        return self._current_step

    @property
    def goal(self) -> str:
        return self._goal

    async def run(self, goal: str, *, timeout_seconds: Optional[float] = None) -> Observation:
        """
        运行 Agent 完成任务。

        两种模式（由 Planner 是否支持拆解决定）：
        - 步骤模式：Planner.decompose() 返回步骤队列，按队列逐项执行，
          等待 / 验收类步骤由框架直接处理（V0.4 前瞻）。
        - 自由模式：Planner 不支持拆解（或拆解失败），退化为单目标循环，
          LLM 自主决策直到输出 done。

        Args:
            goal: 用户目标描述
            timeout_seconds: 可选的总执行超时（秒，待解决问题 #22）。
                超时后中断当前执行并返回失败 Observation，避免 LLM 慢时
                任务无限拉长（仅 max_steps 无 wall-clock 兜底）。

        Returns:
            最终 Observation（包含 done=True 或错误信息）
        """
        self._goal = goal
        self._history.clear()
        self._memory.clear()
        self._current_step = 0
        self._recovery_count = 0
        # 清空规划器状态（#11），保证复用 Agent 时无状态残留；外部自定义
        # Planner 可能未实现 reset，用 getattr 防御（与 decompose 一致）。
        reset = getattr(self._planner, "reset", None)
        if reset is not None:
            reset()

        if timeout_seconds is None:
            return await self._run_body(goal)
        try:
            return await asyncio.wait_for(
                self._run_body(goal), timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("⏱️ 任务超时（{:.0f}s）终止 | goal: {}", timeout_seconds, goal)
            return Observation.fail(
                error=f"任务执行超过 {timeout_seconds:g} 秒，已超时终止",
            )

    async def _run_body(self, goal: str) -> Observation:
        """run() 的执行体：由 Planner 是否支持拆解决定步骤 / 自由模式。"""
        decompose = getattr(self._planner, "decompose", None)
        steps = await decompose(goal) if decompose is not None else None
        if steps:
            return await self._run_with_steps(goal, steps)
        return await self._run_free(goal)

    async def _run_free(self, goal: str) -> Observation:
        """自由模式：单目标循环，LLM 自主决策直到输出 done。

        V1.0 批量增强：每轮 observe 一次、批量规划一次（plan_batch），
        返回的多个动作在同一 Snapshot 上连续执行（表单填写从 N 次 LLM 降为 1 次）；
        任一动作失败、导致页面变化或遇到 done 时结束本批，回到主循环重新观察。
        """
        # 循环检测（V0.4 前瞻）：连续无效果等待计数
        consecutive_waits = 0
        # 重复动作检测（V1.0 批量增强）：上一步指纹 + 页面是否变化 + 连续跳过计数
        last_fp: Optional[tuple] = None
        last_changed = True
        repeat_skips = 0

        logger.info("🧠 Agent 启动（自由模式）| 目标: {}", goal)

        while self._current_step < self._max_steps:
            # 1. Observe（每批一次）
            logger.info("📷 [Step {}/{}] 观察页面...", self._current_step + 1, self._max_steps)
            snapshot = await self._safe_observe()
            if snapshot is None:
                return Observation.fail(
                    error=f"在第 {self._current_step + 1} 步观察页面失败（浏览器可能已关闭）",
                )
            self._log_snapshot(snapshot)

            # 2. Plan batch（每批一次 LLM，可返回多个连续动作）
            # M4：兼容 V0.3 鸭子类型自定义 Planner（未继承基类）——
            # plan_batch → plan_with_history → plan 逐级回退，避免 AttributeError
            logger.info("📝 [Step {}/{}] 规划动作...", self._current_step + 1, self._max_steps)
            planner = self._planner
            plan_batch = getattr(planner, "plan_batch", None)
            if plan_batch is not None:
                actions = await plan_batch(
                    snapshot, goal, self._memory.context_entries(),
                )
            else:
                plan_wh = getattr(planner, "plan_with_history", None)
                action = (
                    await plan_wh(snapshot, goal, self._memory.context_entries())
                    if plan_wh is not None
                    else await planner.plan(snapshot, goal)
                )
                actions = [action] if action is not None else None
            if not actions:
                logger.warning("⚠️  无法规划出有效动作")
                return Observation.fail(
                    error=f"在第 {self._current_step + 1} 步无法规划出有效动作",
                    url=snapshot.url,
                )

            # 3. 批量执行（同一 Snapshot，逐动作执行/记录）
            failed_obs: Optional[Observation] = None
            for action in actions:
                self._current_step += 1
                step = self._current_step

                # 3.0 Check done
                if action.action == "done":
                    logger.info("✅ Agent 完成任务: {}", action.value or goal)
                    return Observation.ok(
                        url=snapshot.url,
                        title=snapshot.title,
                        data={"done": True, "message": action.value or "任务完成"},
                    )

                # 3.0a 重复动作检测：页面未变化时连续对同一目标执行相同动作 → 跳过
                # 防止 LLM 在输入框填完值后仍反复填同一字段（如登录连续 4 次填用户名）
                skip_action = False
                if (
                    not last_changed
                    and last_fp is not None
                    and action.action in ("input", "click", "select", "scroll")
                ):
                    current_fp = (action.action, action.target_id)
                    if current_fp == last_fp:
                        repeat_skips += 1
                        logger.warning(
                            "⏭️ [Step {}] 跳过重复动作: {}[{}]（页面未变化，连续跳过 {}/{}）",
                            step, action.action, action.target_id,
                            repeat_skips, _MAX_REPEAT_SKIPS,
                        )
                        if repeat_skips >= _MAX_REPEAT_SKIPS:
                            logger.warning(
                                "❌ [Step {}] 连续 {} 次重复动作被跳过，任务停滞",
                                step, _MAX_REPEAT_SKIPS,
                            )
                            return Observation.fail(
                                error=f"重复动作停滞：连续 {_MAX_REPEAT_SKIPS} 次对同一目标执行相同动作且页面无变化",
                                url=snapshot.url,
                            )
                        skip_action = True
                    else:
                        repeat_skips = 0
                else:
                    repeat_skips = 0
                if skip_action:
                    await self._record_step(
                        step, action,
                        Observation.fail(
                            error=f"重复动作被跳过（连续 {repeat_skips} 次对同一目标执行相同动作且页面无变化），请勿重复该动作",
                        ),
                    )
                    continue

                # 3.1 Execute（失败自动重试：机械 1 次 → Reflection 1 次）
                self._log_action(action, step)
                observation, final_action = await self._execute_action_with_retry(
                    action, snapshot, step=step,
                )
                if observation is None:
                    return Observation.fail(
                        error=f"在第 {step} 步动作执行异常（浏览器可能已关闭）",
                    )

                # 3.2 Record history（V0.5 起同步写入记忆，供上下文压缩）
                await self._record_step(step, final_action, observation)
                self._notify_planner_result(final_action, observation)

                # 3.3 Check failure（重试与 Reflection 均失败 → 中断本批走页面恢复）
                if observation.is_error:
                    logger.warning("❌ [Step {}] 动作失败（已重试）: {}", step, observation.error)
                    failed_obs = observation
                    break

                # 3.4 循环检测：连续等待且页面无变化 → 任务停滞，提前终止
                # M4：仅对声明启用停滞检测的规划器生效（规则型自行控制等待次数）
                if (
                    getattr(self._planner, "stagnation_detection", True)
                    and final_action.action == "wait"
                    and not observation.page_changed
                ):
                    consecutive_waits += 1
                else:
                    consecutive_waits = 0
                if consecutive_waits >= 2:
                    logger.warning(
                        "⚠️ [Step {}] 连续 {} 次等待且页面无变化，判定任务停滞",
                        step, consecutive_waits,
                    )
                    return Observation.fail(
                        error="任务停滞：连续等待且页面无变化",
                        url=snapshot.url,
                    )

                # 3.5 页面已变化 → 结束本批（旧 target_id 可能失效），并清空重复基准
                if observation.page_changed:
                    logger.info("📄 [Step {}] 页面已变化，结束本批，重新观察", step)
                    last_fp = None
                    last_changed = True
                    break

                # 3.6 更新重复检测基准（仅记录"页面未变化"的最近一次成功动作）
                last_fp = (final_action.action, final_action.target_id)
                last_changed = observation.page_changed

                logger.info("✅ [Step {}] 成功 | URL: {}", step, observation.url)

            # 4. 批量中某动作失败 → 尝试页面恢复，仍失败则中止
            if failed_obs is not None:
                if self._recovery_count < self._max_recoveries and await self._recover_page():
                    self._recovery_count += 1
                    logger.info(
                        "🔄 页面已恢复，重新规划（恢复 {}/{}）",
                        self._recovery_count, self._max_recoveries,
                    )
                    continue
                return failed_obs

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
                try:
                    ms = max(0, int(current.params.get("ms", 1000)))
                except (TypeError, ValueError):
                    # 手动构造队列可能出现非纯数字 ms → 兜底 1000（正常链路已归一化）
                    ms = 1000
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
                await self._record_step(self._current_step, action, observation)
                # 等待失败：机械重试 1 次 → 页面恢复 → 中止（#7，与 action 步骤对齐）
                if observation.is_error:
                    logger.warning(
                        "❌ [Step {}] 计划内等待失败: {} → 重试",
                        self._current_step, observation.error,
                    )
                    retry = await self._safe_execute(action, snapshot)
                    if retry is None:
                        return Observation.fail(
                            error=f"在第 {self._current_step} 步动作执行异常（浏览器可能已关闭）",
                        )
                    if not retry.is_error:
                        await self._record_step(self._current_step, action, retry)
                        observation = retry
                    elif self._recovery_count < self._max_recoveries and await self._recover_page():
                        self._recovery_count += 1
                        logger.info(
                            "🔄 [Step {}] 页面已恢复，重试该等待步骤（恢复 {}/{}）",
                            self._current_step, self._recovery_count, self._max_recoveries,
                        )
                        continue
                    else:
                        return retry
                queue.pop()
                logger.info("✅ [Step {}] 等待完成 | URL: {}", self._current_step, observation.url)
                continue

            if current.kind == "verify":
                if self._verify_step(snapshot, current):
                    logger.info(
                        "✅ [Step {}] 步骤验收通过: {}", self._current_step, current.description,
                    )
                    # 与 wait/action 步骤保持一致，验收结果也写入 history
                    # （history["action"] 恒为 Action 对象，供序列化与 demo 展示）
                    await self._record_step(
                        self._current_step,
                        Action(action="verify", value=current.description),
                        Observation.ok(url=snapshot.url, title=snapshot.title),
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
                snapshot, goal, self._memory.context_entries(),
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

            # 5. Record history（V0.5 起同步写入记忆，供上下文压缩）
            await self._record_step(self._current_step, final_action, observation)

            # 5.5 反馈执行结果给 Planner（失败时回滚乐观状态，待解决问题 #1）
            self._notify_planner_result(final_action, observation)

            # 6. Check failure（重试与 Reflection 均失败 → 尝试页面恢复，仍失败则中止）
            if observation.is_error:
                logger.warning("❌ [Step {}] 动作失败（已重试）: {}", self._current_step, observation.error)
                if self._recovery_count < self._max_recoveries and await self._recover_page():
                    self._recovery_count += 1
                    logger.info(
                        "🔄 [Step {}] 页面已恢复，重新规划该步（恢复 {}/{}）",
                        self._current_step, self._recovery_count, self._max_recoveries,
                    )
                    continue
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
        # 走 _safe_execute：浏览器被关闭等异常返回失败 Observation 而非崩溃（#8）
        result = await self._safe_execute(action)
        if result is None:
            return Observation.fail(
                error=f"动作执行异常（浏览器可能已关闭）: {action.action}",
            )
        if result.is_error:
            logger.warning("❌ 手动执行失败: {}", result.error)
        else:
            logger.info("✅ 手动执行成功 | URL: {}", result.url)
        return result

    async def observe(self) -> Optional[Snapshot]:
        """获取当前页面的 Snapshot（手动模式 / 调试用）。

        Returns:
            页面 Snapshot；浏览器被关闭等异常时返回 None（#8）。
        """
        logger.info("📷 观察页面...")
        snapshot = await self._safe_observe()
        if snapshot is None:
            logger.warning("⚠️  观察页面失败（浏览器可能已关闭）")
            return None
        self._log_snapshot(snapshot)
        return snapshot

    # ── 内部方法 ────────────────────────────────────────────────────

    async def _record_step(
        self, step: int, action: Action, observation: Observation,
    ) -> None:
        """记录一步历史：写入原始历史与增量记忆（V0.5）。

        entry 同时被 ``Agent.history``（完整原始列表）与 ``HistoryMemory``
        （只读引用）持有；写入后触发记忆折叠，超出窗口的旧批次被压缩为摘要。
        """
        entry = {"step": step, "action": action, "observation": observation}
        self._history.append(entry)
        self._memory.add(entry)
        await self._memory.maybe_summarize()

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

    def _notify_planner_result(
        self, action: Action, observation: Observation,
    ) -> None:
        """反馈动作执行结果给 Planner（待解决问题 #1）。

        状态型规划器（RuleBasedPlanner）据此在动作失败时回滚乐观置位，
        避免下一轮规划跳过失败步骤或误判任务完成；对不支持该接口的
        Planner（基类 no-op / LLM 规划器）无副作用。
        """
        notify = getattr(self._planner, "on_action_result", None)
        if notify is None:
            return
        try:
            notify(action, observation)
        except Exception as e:
            logger.warning("⚠️  Planner.on_action_result 执行异常: {}", e)

    def _is_frame_action(self, snapshot: Optional[Snapshot], action: Action) -> bool:
        """判断 action 的目标元素是否位于 iframe 内（#5）。

        仅当 target_id 命中 Snapshot 且 frame_path 非空时才为 True。
        若无法确认（如无 snapshot 或非 target_id 定位），保守返回 False。
        """
        if not snapshot or not action.target_id:
            return False
        for el in snapshot.get_interactive_elements():
            if el.element_id == action.target_id:
                return bool(getattr(el, "frame_path", ()))
        return False

    async def _refresh_if_frame(self, snapshot, action) -> Snapshot:
        """iframe Action 失败后：重新 Observe，避免复用点击后可能已失效的
        frame_path / target_id（#5）。无法观察时回退原 snapshot。
        """
        if not self._is_frame_action(snapshot, action):
            return snapshot
        fresh = await self._safe_observe()
        return fresh if fresh is not None else snapshot

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
        2. 失败 → 机械重试 1 次（处理瞬时错误，如元素刚渲染；iframe Action
           重试前重新 Observe，避免复用失效的 frame_path / target_id —— #5）；
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
        snapshot = await self._refresh_if_frame(snapshot, action)
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
            snapshot, self._goal, self._memory.context_entries(), action, retry.error or ""
        )
        if reflect_action is None:
            logger.warning("⚠️  [Step {}] Reflection 无替代动作，放弃该步", step)
            return retry, action
        self._log_action(reflect_action, step)
        reflect_obs = await self._safe_execute(reflect_action, snapshot)
        if reflect_obs is None:
            return None, reflect_action
        return reflect_obs, reflect_action

    async def _recover_page(self) -> bool:
        """失败后尝试恢复页面状态（V0.4）：优先后退，否则刷新。

        适用场景：动作失败后页面状态异常（如误跳转、元素整体失效），
        后退/刷新可重置页面让 Agent 重新观察规划。

        Returns:
            True 表示页面已恢复，可重新观察；False 表示恢复不可用。
        """
        # 走 Executor 公开分发（_execute_back/_execute_refresh），避免直接访问私有属性 _tool
        try:
            back_obs = await self._executor.execute(Action(action="back"))
            if back_obs is not None and not back_obs.is_error:
                logger.info("🔄 页面恢复: 后退成功 | URL: {}", back_obs.url)
                await asyncio.sleep(0.5)
                return True
            logger.debug("后退恢复未生效: {}", back_obs.error if back_obs else "无返回")
        except Exception as e:
            logger.debug("后退恢复不可用: {}", e)
        try:
            refresh_obs = await self._executor.execute(Action(action="refresh"))
            if refresh_obs is not None and not refresh_obs.is_error:
                logger.info("🔄 页面恢复: 刷新成功")
                return True
            logger.debug("刷新恢复未生效: {}", refresh_obs.error if refresh_obs else "无返回")
        except Exception as e:
            logger.debug("刷新恢复不可用: {}", e)
        logger.warning("🔄 页面恢复失败（back/refresh 均不可用）")
        return False

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
        """校验步骤验收条件：verify.type=url → URL 包含；text → 标题/元素/正文文本包含。"""
        value = step.params.get("value", "")
        if not value:
            return False
        vtype = step.params.get("type", "text")
        if vtype == "url":
            return value.lower() in (snapshot.url or "").lower()
        haystack = f"{snapshot.title or ''} " + " ".join(
            el.text for el in snapshot.get_interactive_elements()
        )
        # 与 RuleBasedPlanner._page_contains 对齐：正文文本（h1-h6/p/span 等）也纳入验收
        haystack += " " + " ".join(el.text for el in snapshot.texts)
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
