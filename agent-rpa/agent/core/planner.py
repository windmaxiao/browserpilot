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

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

from agent.schema.action import Action, done, goto
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
    """规划器基类（V0.2 由 RuleBasedPlanner 实现）。"""

    async def plan(self, snapshot: Snapshot, goal: str) -> Optional[Action]:
        raise NotImplementedError("Planner.plan() 未实现")

    async def plan_with_history(
        self, snapshot: Snapshot, goal: str, history: list
    ) -> Optional[Action]:
        return await self.plan(snapshot, goal)


# ═══════════════════════════════════════════════════════════════
# RuleBasedPlanner（V0.2）
# ═══════════════════════════════════════════════════════════════

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
                return Action(action="input", target_id=box.element_id,
                              value=spec.search_keywords[0])
        # 4. 提交搜索
        if spec.search_keywords and self._searched and not self._submitted:
            btn = self._find_submit_button(snapshot)
            if btn is not None:
                self._submitted = True
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
        a, b = current.lower(), target.lower()
        if a == b:
            return True
        if b.endswith("/") and a == b.rstrip("/"):
            return True
        return a.startswith(b)

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
