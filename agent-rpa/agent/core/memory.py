"""
HistoryMemory —— 增量式历史记忆（V0.5 Memory）

解决长任务中滑动窗口丢弃早期上下文的问题：
- 原始条目始终完整保留（供 Agent.history 展示与调试）。
- 超出窗口的旧条目按批折叠为摘要（规则式默认，可注入异步 LLM 摘要器）。
- 上下文压缩：context_entries() 返回 [摘要?] + [最近窗口]，摘要不占窗口名额。

安全边界：摘要只含 action / target / 成功与否，不含输入值、URL、截图等敏感内容，
与 prompts/planner.py 的 serialize_history 白名单保持一致。

依赖：仅依赖 schema 层（纯数据），不依赖 LLM / Playwright。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

# 摘要器类型：输入一批原始历史条目，输出一段摘要文本
Summarizer = Callable[[list[dict]], Awaitable[str]]


def summarize_entries(entries: list[dict], *, max_lines: int = 50) -> str:
    """规则式历史摘要（纯函数、确定性、零 LLM 成本）。

    每条历史压缩为一行「动作 目标 → 结果」；只保留与 ``serialize_history``
    一致的白名单字段，避免输入值等敏感内容进入模型上下文。

    Args:
        entries: Agent 历史条目列表，每项形如 {"step", "action", "observation"}
        max_lines: 摘要行数上限（防异常数据无限增长）

    Returns:
        多行摘要文本；空输入返回空字符串
    """
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action")
        observation = entry.get("observation")
        if isinstance(action, dict):
            name = str(action.get("action", "?"))
            target = str(action.get("target", "") or "")
        else:
            name = str(getattr(action, "action", "?"))
            target = str(getattr(action, "target", "") or "")
        ok = getattr(observation, "success", None)
        status = "成功" if ok is True else ("失败" if ok is False else "未知")
        line = name
        if target:
            line += f"「{target}」"
        line += f" {status}"
        lines.append(line)
        if len(lines) >= max_lines:
            lines.append("…")
            break
    return "\n".join(lines)


class HistoryMemory:
    """增量式历史记忆：滚动摘要 + 滑动窗口 + 上下文压缩。

    折叠策略（window + batch 触发，逐批增量）：
    当 ``len(entries) - summarized_upto >= window + batch`` 时，
    把最旧的一批 ``batch`` 条折叠为一条摘要，``summarized_upto`` 前移，
    保证每条目只被摘要一次。

    Args:
        window: 最近窗口大小（原始详情保留条数）
        summarize_batch: 每批折叠条数（达到 window + batch 时触发一次）
        summarizer: 可选异步摘要器 ``async (entries) -> str``；
            未提供时使用规则式 :func:`summarize_entries`（LLM 摘要扩展点）
        summarize_timeout: 注入摘要器单次调用超时（秒），超时/异常降级规则式
    """

    def __init__(
        self,
        *,
        window: int = 5,
        summarize_batch: int = 10,
        summarizer: Optional[Summarizer] = None,
        summarize_timeout: float = 30.0,
    ):
        if window < 1 or summarize_batch < 1:
            raise ValueError("window / summarize_batch 必须为正整数")
        self._window = window
        self._batch = summarize_batch
        self._summarizer = summarizer
        self._summarize_timeout = summarize_timeout
        self._entries: list[dict] = []
        self._summary_lines: list[str] = []
        self._summarized_upto = 0

    # ── 只读视图 ────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """原始条目总数（含已折叠部分）。"""
        return len(self._entries)

    @property
    def summary(self) -> str:
        """累计摘要文本（多批摘要以换行拼接）。"""
        return "\n".join(self._summary_lines)

    @property
    def recent(self) -> list[dict]:
        """尚未折叠的最近条目（原始对象引用）。"""
        return list(self._entries[self._summarized_upto:])

    # ── 写入 ────────────────────────────────────────────────────────

    def add(self, entry: dict) -> None:
        """追加一条历史记录（entry 与 Agent.history 共享，只读不修改）。"""
        self._entries.append(entry)

    async def maybe_summarize(self) -> None:
        """达到阈值时把最旧的未折叠批次折叠为摘要（增量，每条目只摘要一次）。"""
        while len(self._entries) - self._summarized_upto >= self._window + self._batch:
            batch = self._entries[
                self._summarized_upto : self._summarized_upto + self._batch
            ]
            text = await self._summarize(batch)
            if text:
                self._summary_lines.append(text)
            self._summarized_upto += len(batch)

    async def _summarize(self, batch: list[dict]) -> str:
        """对一批条目生成摘要：优先使用注入的摘要器，否则规则式。

        M3：注入摘要器（文档扩展点，如 LLM）可能抛网络异常或长时间阻塞，
        加超时与异常防护，失败降级为规则式摘要——摘要失败不应阻断任务本身。
        """
        if self._summarizer is not None:
            try:
                text = await asyncio.wait_for(
                    self._summarizer(batch), timeout=self._summarize_timeout,
                )
                return str(text).strip()
            except Exception as e:
                logger.warning("注入摘要器失败/超时，降级规则式摘要: {}", e)
        return summarize_entries(batch)

    # ── 上下文输出 ──────────────────────────────────────────────────

    def context_entries(self) -> list[dict]:
        """返回 Planner 使用的压缩上下文：[摘要条目?] + [最近窗口]。

        摘要条目形如 ``{"kind": "summary", "text": "..."}`` 且恒在头部，
        不占窗口名额；由 prompts 层渲染为模型视图。
        """
        out: list[dict] = []
        if self._summary_lines:
            out.append({"kind": "summary", "text": self.summary})
        out.extend(self._entries[self._summarized_upto:][-self._window:])
        return out

    def clear(self) -> None:
        """重置记忆（开始新任务时调用）。"""
        self._entries.clear()
        self._summary_lines.clear()
        self._summarized_upto = 0
