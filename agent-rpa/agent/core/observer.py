"""
Observer —— 观察者

负责收集当前页面的 Snapshot 供 Agent 决策。
将 SnapshotGenerator 的输出包装为 Agent 可直接使用的格式。
"""

from __future__ import annotations

from loguru import logger

from agent.browser.snapshot import SnapshotGenerator
from agent.schema.snapshot import Snapshot


class Observer:
    """
    观察者 —— 生成当前页面的 Snapshot。

    在 V0.1 中主要负责：
    - 调用 SnapshotGenerator 生成快照
    - 附加额外上下文信息
    - 未来：过滤广告弹窗、合并历史信息等
    """

    def __init__(self, snapshot_generator: SnapshotGenerator):
        self._generator = snapshot_generator
        logger.debug("Observer 初始化完成")

    async def observe(self) -> Snapshot:
        """
        观察当前页面，生成 Snapshot。

        Returns:
            Snapshot: 当前页面的结构化认知
        """
        logger.info("生成页面 Snapshot...")
        snapshot = await self._generator.generate()

        if not snapshot.page_type:
            snapshot.page_type = await self._generator.detect_page_type()

        logger.info(
            "Snapshot 就绪 | title={} | 交互元素={} | page_type={}",
            snapshot.title,
            len(snapshot.get_interactive_elements()),
            snapshot.page_type,
        )
        return snapshot

    async def observe_simplified(self) -> dict:
        """
        生成简化版的页面描述（供 LLM 提示词使用）。

        Returns:
            dict: 包含页面摘要信息的字典
        """
        snapshot = await self.observe()
        simplified = {
            "title": snapshot.title,
            "url": snapshot.url,
            "page_type": snapshot.page_type,
            "summary": snapshot.summary(),
            "interactive_count": len(snapshot.get_interactive_elements()),
            "loading": snapshot.loading,
        }
        logger.debug("简化版 Snapshot: {}", simplified["summary"])
        return simplified
