"""
Planner —— 规划器

根据 Snapshot 规划下一个 Action。

V0.2 使用规则驱动实现（RuleBasedPlanner），V0.3 起可替换为 LLM Planner。

设计原则：
- 输入：Snapshot（页面认知）+ goal（用户目标）
- 输出：Action（下一步操作）
- 不直接操作浏览器
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

from agent.llm.base import LLMClient, LLMError, LLMRetryableError
from agent.prompts.planner import (
    SYSTEM_PROMPT,
    ActionParseError,
    build_action_schema,
    build_user_prompt,
    parse_action_dict,
    serialize_history,
    serialize_snapshot,
)
from agent.schema.action import Action, VALID_ACTIONS, done, goto
from agent.schema.observation import Observation
from agent.schema.snapshot import ElementInfo, Snapshot


# ═══════════════════════════════════════════════════════════════
# 任务目标解析
# ═══════════════════════════════════════════════════════════════

@dataclass
class TaskSpec:
    """从用户目标中解析出的任务要素"""

    goal: str = ""
    url: Optional[str] = None                       # 目标 URL（"打开 https://..."）
    search_keywords: list[str] = field(default_factory=list)  # 搜索关键词（"查找 X"）
    target_texts: list[str] = field(default_factory=list)     # 点击目标（"点击 X" / 引号目标）
    wait_texts: list[str] = field(default_factory=list)       # 等待条件（"等待 X 出现"）
    wait_loading: bool = False                      # 是否等待页面加载完成
    done_keywords: list[str] = field(default_factory=list)    # 完成判定关键词（信息性）


_URL_RE = re.compile(r"(?:https?|file)://[^\s，。]+")
_SEARCH_RE = re.compile(r"(?:搜索|查找|查询|搜一下|搜)[：:\s]+([^\s，。]+)")
# 点击/等待 目标在遇到下一个动词、句号或结尾处截断；
# 动词后要求有空格/冒号，避免误匹配"点击率"等复合词
_VERB_BOUNDARY = r"(?=\s*(?:打开|查找|搜索|查询|搜一下|点击|等待|然后|接着|再)|，|。|$)"
_CLICK_RE = re.compile(r"点击[：:\s]+([^，。\n]+?)" + _VERB_BOUNDARY)
_WAIT_RE = re.compile(r"等待[：:\s]+([^，。\n]+?)" + _VERB_BOUNDARY)
_LOADING_PHRASES = ("页面加载完成", "页面加载", "加载完成", "页面加载完毕")


def _dedupe(items: list[str]) -> list[str]:
    """去重并保持顺序"""
    seen: set[str] = set()
    result: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            result.append(x)
    return result


def _extract_click_targets(goal: str) -> list[str]:
    """提取「点击 X」的目标文本；若 X 内含引号则优先取引号内容。"""
    targets: list[str] = []
    for m in _CLICK_RE.finditer(goal):
        raw = m.group(1).strip()
        if not raw:
            continue
        quoted = re.search(r'["“「『]([^"”」』]+)["”」』]', raw)
        targets.append(quoted.group(1) if quoted else raw.strip('"“”「『』」'))
    return targets


def parse_goal(goal: str) -> TaskSpec:
    """从自然语言目标中提取任务要素。"""
    spec = TaskSpec(goal=goal)
    m = _URL_RE.search(goal)
    if m:
        spec.url = m.group(0).rstrip(".,;，。")
    spec.search_keywords = _SEARCH_RE.findall(goal)
    quoted = re.findall(r'["“「『]([^"”」』]+)["”」』]', goal)
    spec.target_texts = _dedupe(quoted + _extract_click_targets(goal))
    spec.done_keywords = [k for k in spec.search_keywords + spec.target_texts if k]
    for m in _WAIT_RE.finditer(goal):
        cond = m.group(1).strip()
        if not cond:
            continue
        if cond in _LOADING_PHRASES:
            spec.wait_loading = True
        else:
            spec.wait_texts.append(cond)
    return spec


# ═══════════════════════════════════════════════════════════════
# Planner 基类
# ═══════════════════════════════════════════════════════════════

class Planner:
    """规划器基类（V0.2 由 RuleBasedPlanner 实现，V0.3 由 LLMPlanner 实现）。"""

    async def plan(self, snapshot: Snapshot, goal: str) -> Optional[Action]:
        raise NotImplementedError("Planner.plan() 未实现")

    async def plan_with_history(
        self, snapshot: Snapshot, goal: str, history: list
    ) -> Optional[Action]:
        return await self.plan(snapshot, goal)

    async def decompose(self, goal: str) -> Optional[list["TaskStep"]]:
        """将目标拆解为步骤队列（V0.4 前瞻）。

        默认返回 None（单步骤模式），由 Agent 将整个目标作为一步执行；
        支持拆解的规划器（TaskPlanner）覆盖本方法返回有序步骤列表。
        """
        return None

    async def plan_step(
        self,
        snapshot: Snapshot,
        goal: str,
        history: list,
        *,
        steps: Optional[list["TaskStep"]] = None,
        current_index: Optional[int] = None,
    ) -> Optional[Action]:
        """针对当前任务步骤规划一个 Action。

        默认退化为 plan_with_history（无步骤上下文）；支持分步上下文的
        规划器（TaskPlanner）覆盖本方法，将当前步骤与剩余步骤注入提示词。
        """
        return await self.plan_with_history(snapshot, goal, history)

    async def reflect(
        self,
        snapshot: Snapshot,
        goal: str,
        history: list,
        action: Action,
        error: str,
    ) -> Optional[Action]:
        """失败反思（V0.4 Reflection）：分析失败原因并给出替代动作。

        默认返回 None 表示不支持 Reflection；支持该能力的规划器
        （LLMPlanner / TaskPlanner）覆盖本方法。Agent 在机械重试失败后调用，
        得到替代动作则再执行一次，否则中止任务。
        """
        return None

    def on_action_result(self, action: Action, observation: Observation) -> None:
        """接收动作执行结果（待解决问题 #1）。

        Agent 每次动作执行完成后调用（成功或失败均调用）。默认 no-op；
        状态型规划器（RuleBasedPlanner）覆盖本方法，用于在动作执行失败时
        回滚规划阶段乐观置位的状态。
        """


# ═══════════════════════════════════════════════════════════════
# RuleBasedPlanner（V0.2）
# ═══════════════════════════════════════════════════════════════

def _error_detail(e: BaseException) -> str:
    """错误信息 + HTTP 响应码（供 LLM 调用日志展示）。

    有状态码 → ``错误 | HTTP 429``；无状态码（超时/连接类）→ ``错误（无 HTTP 响应码）``。
    """
    code = getattr(e, "status_code", None)
    return f"{e} | HTTP {code}" if code else f"{e}（无 HTTP 响应码）"


class RuleBasedPlanner(Planner):
    """
    基于规则的规划器（V0.2 实现）。

    内置规则按优先级执行：
    1. 完成判定 —— 全部点击目标已点击 / 关键词出现（详见 _should_finish）→ done
    2. 导航 —— 目标含 URL 且未到达 → goto
    3. 搜索输入 —— 有搜索框且未输入 → input(关键词)
    4. 提交搜索 —— 已输入未提交 → click(搜索按钮)
    5. 等待条件 —— 页面未加载完 / 等待文本未出现 → wait（最多 10 次）
    6. 点击目标 —— 页面出现未点击的目标文本 → click
    7. 结果链接 —— 搜索后点击第一条结果
    8. 兜底等待 —— 提交后结果未出现 → wait

    完成判定增强（解析/规则增强）：
    - 有显式点击目标时：全部目标点击完成才算完成（不再因关键词出现提前收工）
    - 无点击目标时：保持原行为（已提交且页面出现关键词即完成 / 纯导航到达 URL）

    自定义规则（add_rule）优先于内置规则执行。
    """

    _SEARCH_BUTTON_TEXTS = ("搜索", "查找", "查询", "百度一下", "Search", "Go")
    _WAIT_MAX_TRIES = 10
    _WAIT_MS = 800

    def __init__(self):
        self._rules: list[tuple[str, Callable, Callable]] = []
        self._spec: Optional[TaskSpec] = None
        self._searched = False      # 是否已输入关键词
        self._submitted = False     # 是否已提交搜索
        self._clicked: set[str] = set()   # 已点击的目标文本
        self._wait_count = 0        # 连续等待次数（防死循环）
        # 最近一次乐观置位对应的意图（待解决问题 #1，失败时回滚依据）
        # 取值：("input",) / ("submit",) / ("target", 目标文本)
        self._last_intent: Optional[tuple] = None

    # ── 自定义规则 ──────────────────────────────────────────────

    def add_rule(
        self,
        condition: Callable,
        action_fn: Callable,
        name: str = "",
    ) -> None:
        """注册自定义规则：condition(snapshot, goal) 为真时执行 action_fn(snapshot)。

        Args:
            condition: 条件函数 condition(snapshot, goal) -> bool
            action_fn: 动作函数 action_fn(snapshot) -> Action | None
            name: 可读规则名（用于日志/测试定位），默认取 action_fn.__name__
        """
        rule_name = name or getattr(action_fn, "__name__", "anonymous")
        self._rules.append((rule_name, condition, action_fn))

    # ── 主入口 ──────────────────────────────────────────────────

    async def plan(self, snapshot: Snapshot, goal: str) -> Optional[Action]:
        spec = self._ensure_spec(goal)
        logger.debug(
            "规划: url={} search={} targets={} wait={} (searched={}, submitted={}, clicked={})",
            spec.url, spec.search_keywords, spec.target_texts,
            spec.wait_texts or ("加载完成" if spec.wait_loading else "-"),
            self._searched, self._submitted, sorted(self._clicked),
        )
        # 自定义规则优先；单条规则异常不中断规划，记录后继续下一条
        for rule_name, condition, action_fn in self._rules:
            try:
                if condition(snapshot, goal):
                    action = action_fn(snapshot)
                    if action is not None:
                        return action
            except Exception as e:
                logger.warning("自定义规则 '{}' 执行异常，跳过: {}", rule_name, e)
                continue
        action = await self._builtin_plan(snapshot, spec)
        # 非等待动作视为有进展，重置等待计数
        if action is not None and action.action != "wait":
            self._wait_count = 0
        return action

    def reset(self) -> None:
        """重置任务状态（开始新任务时调用）。"""
        self._spec = None
        self._searched = False
        self._submitted = False
        self._clicked.clear()
        self._wait_count = 0
        self._last_intent = None

    def on_action_result(self, action: Action, observation: Observation) -> None:
        """按动作执行结果回滚乐观状态（待解决问题 #1）。

        内置规则的状态（_searched / _submitted / _clicked）在规划时乐观置位；
        Agent 执行完成后调用本方法（成功或失败均调用）：
        - 成功：状态保持，仅清除意图记录；
        - 失败：按最近一次规划意图精确回滚 —— 输入失败重新输入、
          提交失败重新提交、目标点击失败重新点击（且不得误判任务完成）。
        自定义规则 / Reflection 替代动作不设意图，失败时不回滚内置状态。
        """
        intent = self._last_intent
        self._last_intent = None
        if observation is None or not observation.is_error:
            return
        if action.action == "input":
            self._searched = False
        elif action.action == "click" and intent is not None:
            if intent[0] == "submit":
                self._submitted = False
            elif intent[0] == "target":
                self._clicked.discard(intent[1])

    # ── 内置规则 ────────────────────────────────────────────────

    async def _builtin_plan(self, snapshot: Snapshot, spec: TaskSpec) -> Optional[Action]:
        # 1. 完成判定
        if self._should_finish(snapshot, spec):
            return done(summary=spec.goal)
        # 2. 导航
        if spec.url and not self._at_url(snapshot.url, spec.url):
            return goto(spec.url)
        # 3. 搜索输入
        if spec.search_keywords and not self._searched:
            box = self._find_search_box(snapshot)
            if box is not None:
                self._searched = True
                self._last_intent = ("input",)
                return Action(action="input", target_id=box.element_id,
                              value=spec.search_keywords[0])
        # 4. 提交搜索
        if spec.search_keywords and self._searched and not self._submitted:
            btn = self._find_submit_button(snapshot)
            if btn is not None:
                self._submitted = True
                self._last_intent = ("submit",)
                return Action(action="click", target_id=btn.element_id)
        # 5. 等待条件（页面加载 / 指定文本出现）
        if not self._wait_conditions_satisfied(snapshot, spec):
            return self._wait_action()
        # 6. 点击目标（搜索流程中需先提交）
        if not spec.search_keywords or self._submitted:
            for text in spec.target_texts:
                if text in self._clicked:
                    continue
                el = self._find_by_text(snapshot, text)
                if el is not None:
                    self._clicked.add(text)
                    self._last_intent = ("target", text)
                    return Action(action="click", target_id=el.element_id)
        # 7. 结果链接（搜索后点击第一条）
        if self._submitted:
            for el in snapshot.links:
                if el.text.strip():
                    return Action(action="click", target_id=el.element_id)
            # 8. 等待结果渲染
            return self._wait_action()
        return None

    # ── 辅助 ────────────────────────────────────────────────────

    def _ensure_spec(self, goal: str) -> TaskSpec:
        """goal 变化时自动重置任务状态。"""
        if self._spec is None or self._spec.goal != goal:
            self.reset()
            self._spec = parse_goal(goal)
        return self._spec

    def _should_finish(self, snapshot: Snapshot, spec: TaskSpec) -> bool:
        if not self._wait_conditions_satisfied(snapshot, spec):
            return False
        # 纯导航任务：到达目标 URL 即完成
        if (spec.url and not spec.search_keywords and not spec.target_texts
                and self._at_url(snapshot.url, spec.url)):
            return True
        # 有显式点击目标：全部点击完成才算完成
        if spec.target_texts:
            return self._all_targets_clicked(spec) and (
                not spec.search_keywords or self._submitted
            )
        # 内容型任务：已提交搜索且页面出现目标关键词
        if self._submitted and spec.search_keywords:
            return any(self._page_contains(snapshot, kw) for kw in spec.search_keywords)
        return False

    def _all_targets_clicked(self, spec: TaskSpec) -> bool:
        return all(t in self._clicked for t in spec.target_texts)

    def _wait_conditions_satisfied(self, snapshot: Snapshot, spec: TaskSpec) -> bool:
        """等待条件是否全部满足"""
        if spec.wait_loading and snapshot.loading:
            return False
        if spec.wait_texts and not all(
            self._page_contains(snapshot, t) for t in spec.wait_texts
        ):
            return False
        return True

    def _wait_action(self) -> Optional[Action]:
        """返回等待动作；连续等待超过上限则放弃（返回 None）。"""
        self._wait_count += 1
        if self._wait_count > self._WAIT_MAX_TRIES:
            logger.warning("等待条件超时（{} 次），放弃等待", self._WAIT_MAX_TRIES)
            self._wait_count = 0
            return None
        return Action(action="wait", params={"ms": self._WAIT_MS})

    @staticmethod
    def _at_url(current: str, target: str) -> bool:
        a, b = current.lower().rstrip("/"), target.lower().rstrip("/")
        if not b:
            return False
        if a == b:
            return True
        if not a.startswith(b):
            return False
        # 边界检查：b 之后的字符必须是路径/查询/锚点分隔符，
        # 排除 baidu.com.evil.com 对 baidu.com 的仿冒域名误判
        rest = a[len(b):]
        return rest == "" or rest[0] in "/?#"

    @staticmethod
    def _find_search_box(snapshot: Snapshot) -> Optional[ElementInfo]:
        for el in snapshot.inputs:
            hint = f"{el.placeholder} {el.aria_label}".lower()
            if any(k in hint for k in ("搜索", "查找", "查询", "search", "query")):
                return el
        # 兜底：第一个普通文本输入框
        for el in snapshot.inputs:
            if el.attributes.get("type", "text") in ("text", "search", ""):
                return el
        return snapshot.inputs[0] if snapshot.inputs else None

    @classmethod
    def _find_submit_button(cls, snapshot: Snapshot) -> Optional[ElementInfo]:
        for el in snapshot.buttons:
            if any(k in el.text for k in cls._SEARCH_BUTTON_TEXTS):
                return el
        # 兜底：type=submit 的输入框
        for el in snapshot.inputs:
            if el.attributes.get("type") == "submit":
                return el
        return None

    _NORMALIZE_RE = re.compile(r"[\s\-_—・·|/（）()]+")

    @classmethod
    def _normalize(cls, text: str) -> str:
        """归一化文本：去除空格/破折号/斜杠等分隔符，统一大小写"""
        return cls._NORMALIZE_RE.sub("", text).lower()

    @classmethod
    def _find_by_text(cls, snapshot: Snapshot, text: str) -> Optional[ElementInfo]:
        target = cls._normalize(text)
        for el in snapshot.get_interactive_elements():
            if target and target in cls._normalize(el.text):
                return el
        return None

    @classmethod
    def _page_contains(cls, snapshot: Snapshot, keyword: str) -> bool:
        kw = cls._normalize(keyword)
        if kw and kw in cls._normalize(snapshot.title):
            return True
        for el in snapshot.get_interactive_elements():
            if kw in cls._normalize(el.text):
                return True
        for el in snapshot.texts:
            if kw in cls._normalize(el.text):
                return True
        return False


# ═══════════════════════════════════════════════════════════════
# LLMPlanner（V0.3）
# ═══════════════════════════════════════════════════════════════

class LLMPlanner(Planner):
    """LLM 驱动的规划器（V0.3 实现）。

    ``plan()`` 流程（与 V0.3 计划 2.3 对齐）：

    1. Snapshot → 模型视图（:func:`serialize_snapshot`，脱敏 + 截断）。
    2. 构建系统 / 用户提示词，用户提示词含最近历史滑动窗口。
    3. 调用 ``complete_json()`` 请求一个 Action JSON（可重试错误指数退避自动重试并封顶，V0.4）。
    4. :func:`parse_action_dict` 安全转换为已验证的 Action
       （target_id 本地映射 selector，忽略模型伪造的 selector）。
    5. 内容层失败最多发起 ``max_repair_attempts`` 次修复请求；仍失败返回 None。

    失败反思：:meth:`reflect` 分析动作失败原因并给出替代动作（V0.4 Reflection）。
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        model: str = "",
        max_repair_attempts: int = 1,
        timeout: int = 30_000,
        constraints: Optional[list[str]] = None,
        llm_retries: int = 5,
        llm_retry_delay: float = 2.0,
        llm_retry_max_delay: float = 16.0,
    ):
        self._client = client
        self._model = model
        self._max_repair_attempts = max_repair_attempts
        self._timeout = timeout
        self._constraints = list(constraints) if constraints else None
        self._llm_retries = llm_retries
        self._llm_retry_delay = llm_retry_delay
        self._llm_retry_max_delay = llm_retry_max_delay

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_repair_attempts(self) -> int:
        return self._max_repair_attempts

    async def _call_llm_with_retry(
        self, *, system_prompt: str, user_prompt: str, schema: dict,
    ) -> Optional[dict]:
        """调用 LLM；可重试错误（超时/限流/网络）指数退避自动重试（V0.4）。

        仍失败（或不可重试错误）时返回 None，由调用方决定放弃/修复。
        """
        for attempt in range(self._llm_retries):
            try:
                # 只记录摘要（模型/序号/长度），不输出完整提示词（含页面文本与目标）
                logger.info(
                    "🧠 LLM 请求（模型: {} | 第 {} 次 | system {} 字符 | user {} 字符）",
                    self._model, attempt + 1, len(system_prompt), len(user_prompt),
                )
                raw = await self._client.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    timeout=self._timeout,
                )
                logger.info("🤖 LLM 响应: {}", json.dumps(raw, ensure_ascii=False))
                return raw
            except LLMRetryableError as e:
                if attempt >= self._llm_retries - 1:
                    logger.warning(
                        "LLM 调用重试 {} 次仍失败（可重试错误）: {}", self._llm_retries, _error_detail(e),
                    )
                    return None
                delay = min(
                    self._llm_retry_delay * (2 ** attempt),
                    self._llm_retry_max_delay,
                )
                logger.warning(
                    "🔁 LLM 可重试错误（第 {}/{} 次）: {} → {}s 后重试",
                    attempt + 1, self._llm_retries, _error_detail(e), delay,
                )
                await asyncio.sleep(delay)
            except LLMError as e:
                logger.warning("LLM 调用失败（不可重试）: {}", _error_detail(e))
                return None
        return None

    async def plan(self, snapshot: Snapshot, goal: str) -> Optional[Action]:
        return await self.plan_with_history(snapshot, goal, [])

    async def plan_with_history(
        self, snapshot: Snapshot, goal: str, history: list
    ) -> Optional[Action]:
        view = serialize_snapshot(snapshot)
        schema = build_action_schema()
        user_prompt = build_user_prompt(
            goal, view, serialize_history(history), constraints=self._constraints
        )
        raw = await self._call_llm_with_retry(
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, schema=schema,
        )
        if raw is None:
            return None
        for attempt in range(self._max_repair_attempts + 1):
            try:
                return parse_action_dict(raw, snapshot)
            except ActionParseError as e:
                logger.warning(
                    "Action 解析失败（第 {}/{} 次）: {}",
                    attempt + 1, self._max_repair_attempts + 1, e,
                )
                if attempt >= self._max_repair_attempts:
                    return None
                user_prompt = self._build_repair_prompt(user_prompt, raw, str(e))
                raw = await self._call_llm_with_retry(
                    system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, schema=schema,
                )
                if raw is None:
                    return None
        return None

    @staticmethod
    def _build_repair_prompt(original: str, raw: dict, error: str) -> str:
        """构造修复请求：附带上次原始输出与校验错误，Snapshot 保持不变。"""
        return (
            f"{original}\n\n"
            "你上次的输出无法通过校验，请重新输出一个合法的 Action JSON。\n"
            f"可用 action 枚举: {', '.join(sorted(VALID_ACTIONS))}\n"
            f"上次输出: {json.dumps(raw, ensure_ascii=False)}\n"
            f"校验错误: {error}"
        )

    async def reflect(
        self,
        snapshot: Snapshot,
        goal: str,
        history: list,
        action: Action,
        error: str,
    ) -> Optional[Action]:
        """失败反思（V0.4 Reflection）：分析失败原因并给出替代动作。

        替代动作同样经安全转换（伪造 selector 忽略、幻觉 ID 拦截）；
        解析失败不再发起修复，直接返回 None 交由 Agent 中止任务。
        """
        view = serialize_snapshot(snapshot)
        schema = build_action_schema()
        base = build_user_prompt(
            goal, view, serialize_history(history), constraints=self._constraints
        )
        failed = {
            "action": action.action,
            "target": action.target,
            "value": action.value,
            "target_id": action.target_id,
        }
        user_prompt = (
            f"{base}\n\n"
            f"你刚执行的动作失败了：{json.dumps(failed, ensure_ascii=False)}\n"
            f"失败原因：{error}\n"
            "请分析原因并输出下一个动作。可以直接原样重试该动作，也可以更换目标或方式；"
            "若你认为任务已无法继续，输出 {\"action\": \"done\"}。"
        )
        raw = await self._call_llm_with_retry(
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, schema=schema,
        )
        if raw is None:
            return None
        try:
            return parse_action_dict(raw, snapshot)
        except ActionParseError as e:
            logger.warning("Reflection 输出无法解析: {}", e)
            return None


# ═══════════════════════════════════════════════════════════════
# 两阶段规划（V0.4 前瞻）：任务步骤队列
# ═══════════════════════════════════════════════════════════════

@dataclass
class TaskStep:
    """任务拆解后的单个步骤。

    kind 决定执行方式：
    - ``action``：需要 LLM 决策的浏览器操作（goto/input/click/select/scroll...）
    - ``wait``：纯等待步骤（等待加载/固定延时），由框架直接执行，不经过 LLM
    - ``verify``：页面验收步骤，由框架直接校验（URL 包含 / 文本出现）
    """

    description: str                                # 人类可读描述，如"打开百度首页"
    kind: str = "action"                            # action / wait / verify
    params: dict = field(default_factory=dict)      # wait→{"ms": 5000}；verify→{"type": "url"|"text", "value": "..."}


class TaskQueue:
    """任务步骤队列：Agent 循环按序消费。"""

    def __init__(self, steps: list[TaskStep]):
        self._steps = list(steps)
        self._index = 0

    def __len__(self) -> int:
        return len(self._steps)

    def __bool__(self) -> bool:
        return self.remaining() > 0

    @property
    def index(self) -> int:
        """当前步骤下标（0 起）。"""
        return self._index

    def remaining(self) -> int:
        return len(self._steps) - self._index

    def peek(self) -> Optional[TaskStep]:
        """返回当前步骤但不消费。"""
        if self._index >= len(self._steps):
            return None
        return self._steps[self._index]

    def pop(self) -> Optional[TaskStep]:
        """消费当前步骤并前进。"""
        if self._index >= len(self._steps):
            return None
        step = self._steps[self._index]
        self._index += 1
        return step

    def steps(self) -> list[TaskStep]:
        """返回全部步骤（含已消费，供上下文展示）。"""
        return list(self._steps)


# 拆解输出的 JSON schema（包一层 steps 数组，兼容 OpenAI 兼容端点的 object 输出）
DECOMPOSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "步骤描述，如「打开百度首页」「在搜索框输入北京时间」",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["action", "wait", "verify"],
                        "description": "action=浏览器操作（LLM 决策）；wait=纯等待（框架执行）；verify=页面验收",
                    },
                    "params": {
                        "type": "object",
                        "description": "wait 步骤填 {\"ms\": 5000}；verify 步骤填 {\"type\": \"url\"|\"text\", \"value\": \"...\"}",
                    },
                },
                "required": ["description", "kind"],
            },
        }
    },
    "required": ["steps"],
}

DECOMPOSE_PROMPT = """你是网页自动化任务拆解器。把用户目标拆解为**有序的步骤列表**，每个步骤必须是以下三种之一：
- "action"：需要浏览器操作、由执行者按当前页面情况决策的步骤（goto / input / click / select / scroll 等）
- "wait"：纯等待步骤（等待页面加载、固定延时），由框架自动执行，**执行者不需要为等待做任何决策**
- "verify"：页面验收步骤，由框架自动校验（URL 包含某文本，或页面出现某文本）

约束：
- 每个 action 步骤必须能在**一个原子动作**内完成；一个动作做不完的（如"输入并提交"）拆成两步。
- "打开/访问 URL"拆为 goto 步骤（params 给 url）；"搜索/查找 X"拆为「输入关键词」+「点击搜索/提交」两步。
- 明确写出的点击目标（如"点击「北京时间 - 百度百科」链接"）拆为 click 步骤，description 写清点击什么。
- "等待页面加载完成""等待五秒"等含"等待/延时"字样的步骤，**一律**拆为 wait 步骤（kind="wait"，params={"ms": 毫秒数}），绝不能拆成 action。
- "关闭浏览器/退出/结束"由调用方负责，**不要拆出这类步骤**。
- 最后一步通常是 verify（如"页面出现'北京时间'"），用于确认任务达成。
只输出符合 schema 的 JSON 对象（steps 数组），不要 Markdown。"""


# 中文数字 → 数值（用于"等待五秒"这类描述）
_CN_DIGITS = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 等待语义关键词：命中即视为"应拆为 wait 步骤"
_WAIT_KEYWORDS = ("等待", "延时", "延迟", "wait")

# 等待 + 中文数字秒（如"等待五秒"、"等待十五秒"）
_CN_SEC_RE = re.compile(r"([一两二三四五六七八九十]+)\s*秒")
# 等待 + 阿拉伯数字（秒/毫秒）
_NUM_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(秒|s|毫秒|ms)")


def _cn_int(text: str) -> int:
    """中文数字 → 整数（支持 1~99：五=5、十=10、十五=15、二十=20、二十五=25）。"""
    if not text:
        return 0
    if "十" not in text:
        return _CN_DIGITS[text]
    if text == "十":
        return 10
    head, _, tail = text.partition("十")
    tens = _CN_DIGITS[head] if head else 1
    ones = _CN_DIGITS[tail] if tail else 0
    return tens * 10 + ones


def _extract_wait_ms(description: str) -> Optional[int]:
    """从步骤描述中提取等待毫秒数；无法提取返回 None。

    支持：阿拉伯数字（秒/毫秒）、中文数字（秒，含 11~99）。
    """
    text = description.lower()
    m = _NUM_UNIT_RE.search(text)
    if m:
        num = float(m.group(1))
        return int(num * 1000) if m.group(2) in ("秒", "s") else int(num)
    m = _CN_SEC_RE.search(text)
    if m:
        return _cn_int(m.group(1)) * 1000
    return None


def _looks_like_wait(description: str) -> bool:
    """判断步骤描述是否含等待语义（框架兜底识别）。"""
    return any(k in description.lower() for k in _WAIT_KEYWORDS)


def _normalize_wait_steps(steps: list[TaskStep]) -> list[TaskStep]:
    """框架兜底：把含等待语义却被拆成 action 的步骤纠正为 wait 类型。

    拆解 LLM 有时不遵守「等待一律拆为 wait」的约束（如把"等待五秒"拆成
    action），导致等待也走 LLM 决策、多一次调用。此处按描述关键词纠正：
    命中等待语义 → kind 强制为 wait，并提取毫秒数（无数字时默认 1000ms）。
    已正确拆为 wait / verify 的步骤不受影响。
    """
    result: list[TaskStep] = []
    for step in steps:
        if step.kind == "action" and _looks_like_wait(step.description):
            ms = _extract_wait_ms(step.description)
            result.append(TaskStep(
                description=step.description, kind="wait",
                params={"ms": ms if ms is not None else 1000},
            ))
            logger.debug("拆解纠正: 「{}」 action → wait ({}ms)", step.description,
                         ms if ms is not None else 1000)
        else:
            result.append(step)
    return result


def parse_decompose_response(raw: Optional[dict], goal: str) -> Optional[list[TaskStep]]:
    """将 LLM 拆解输出安全转换为 TaskStep 列表。

    无效 / 非法 kind / 空描述条目直接丢弃；全部无效时返回 None（上层回退单步骤模式）。
    解析后统一执行等待语义纠正（_normalize_wait_steps），不依赖 LLM 遵守约束。
    """
    if not raw:
        return None
    raw_steps = raw.get("steps") if isinstance(raw, dict) else raw
    if not isinstance(raw_steps, list):
        return None

    steps: list[TaskStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        kind = str(item.get("kind", "action")).strip()
        if not description or kind not in ("action", "wait", "verify"):
            continue
        params = item.get("params") or {}
        params = params if isinstance(params, dict) else {}
        if kind == "wait":
            try:
                ms = max(0, int(params.get("ms", 1000)))
            except (TypeError, ValueError):
                # 非纯数字 ms（如 "5秒"/"1.5"）→ 从描述兜底提取，避免静默回退 1000
                ms = _extract_wait_ms(description)
                ms = ms if ms is not None else 1000
            params = {"ms": ms}
        elif kind == "verify":
            vtype = str(params.get("type", "text")).strip() or "text"
            params = {"type": vtype, "value": str(params.get("value", "")).strip()}
        steps.append(TaskStep(description=description, kind=kind, params=params))
    if not steps:
        return None
    return _normalize_wait_steps(steps)


class TaskPlanner(LLMPlanner):
    """两阶段规划器（V0.4 前瞻）：目标 → 步骤队列 → 按步骤规划。

    继承 :class:`LLMPlanner`：LLM 可重试错误指数退避重试、失败反思
    （:meth:`reflect`）、Action 修复等能力直接复用。

    与 LLMPlanner 的区别：
    - ``decompose()`` 先把目标拆解为步骤队列（一次 LLM 调用）。
    - ``plan_step()`` 的提示词携带「当前步骤 + 剩余步骤」，让 LLM 聚焦
      完成当前步骤，而不是对着整个目标即兴发挥（消除反复 wait 的空转）。
    - 等待 / 验收类步骤由 Agent 直接执行，完全不经过 LLM。

    拆解失败时 ``decompose()`` 返回 None，Agent 自动回退为单步骤模式
    （等价于旧 LLMPlanner 行为），保证可用性。
    """

    async def decompose(self, goal: str) -> Optional[list[TaskStep]]:
        """调用 LLM 将目标拆解为步骤列表；失败 / 无效返回 None（回退单步骤）。"""
        raw = await self._call_llm_with_retry(
            system_prompt=DECOMPOSE_PROMPT,
            user_prompt=f"用户目标：{goal}\n请把目标拆解为有序步骤。",
            schema=DECOMPOSE_SCHEMA,
        )
        if raw is None:
            logger.warning("任务拆解失败（LLM 未返回结果），回退为单步骤模式")
            return None

        steps = parse_decompose_response(raw, goal)
        if not steps:
            logger.warning("任务拆解结果无效，回退为单步骤模式")
            return None
        logger.info(
            "🧭 任务拆解完成: {} 步 | {}",
            len(steps), " → ".join(s.description for s in steps),
        )
        return steps

    async def plan(self, snapshot: Snapshot, goal: str) -> Optional[Action]:
        return await self.plan_with_history(snapshot, goal, [])

    async def plan_with_history(
        self, snapshot: Snapshot, goal: str, history: list
    ) -> Optional[Action]:
        return await self.plan_step(snapshot, goal, history)

    async def plan_step(
        self,
        snapshot: Snapshot,
        goal: str,
        history: list,
        *,
        steps: Optional[list[TaskStep]] = None,
        current_index: Optional[int] = None,
    ) -> Optional[Action]:
        """针对当前任务步骤规划一个 Action（提示词注入步骤上下文）。"""
        view = serialize_snapshot(snapshot)
        schema = build_action_schema()

        context = build_user_prompt(
            goal, view, serialize_history(history), constraints=self._constraints
        )
        if steps and current_index is not None and 0 <= current_index < len(steps):
            current = steps[current_index]
            remaining = steps[current_index + 1:]
            context += (
                f"\n\n当前任务阶段：第 {current_index + 1}/{len(steps)} 步「{current.description}」。"
                f"请输出一个 Action 完成这一步。"
            )
            if remaining:
                context += "\n剩余步骤：" + " → ".join(
                    s.description for s in remaining[:5]
                )

        user_prompt = context
        raw = await self._call_llm_with_retry(
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, schema=schema,
        )
        if raw is None:
            return None
        for attempt in range(self._max_repair_attempts + 1):
            try:
                return parse_action_dict(raw, snapshot)
            except ActionParseError as e:
                logger.warning(
                    "Action 解析失败（第 {}/{} 次）: {}",
                    attempt + 1, self._max_repair_attempts + 1, e,
                )
                if attempt >= self._max_repair_attempts:
                    return None
                user_prompt = self._build_repair_prompt(user_prompt, raw, str(e))
                raw = await self._call_llm_with_retry(
                    system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, schema=schema,
                )
                if raw is None:
                    return None
        return None
