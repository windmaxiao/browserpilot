"""
Agent 模式 Demo —— 展示完整的 Agent Loop

V0.2+ 使用：需要先实现 Planner。
当前仅展示 Agent 的初始化和手动模式用法。
"""

import asyncio

from loguru import logger

from agent.browser.playwright import BrowserManager
from agent.browser.snapshot import SnapshotGenerator
from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.schema.action import goto, click, done


async def main():
    """
    Agent 手动模式示例。
    展示如何组合各组件，但由用户（或外部脚本）控制执行流程。
    """
    manager = BrowserManager(headless=False)
    await manager.start()

    try:
        # 组装组件
        tool = manager.create_tool()
        snapshot_gen = SnapshotGenerator(manager.page)
        observer = Observer(snapshot_gen)

        # 创建 Agent（Planner 暂用占位）
        from agent.core.planner import Planner
        agent = Agent(
            observer=observer,
            planner=Planner(),  # V0.2 替换为实际 Planner
            executor=Executor(tool),
        )

        # 手动模式：逐个执行 Action
        logger.info("=== Agent 手动模式演示 ===")

        # 观察页面
        snapshot = await agent.observe()
        logger.info("当前页面: {}", snapshot.url)

        # 执行动作
        obs = await agent.step(goto("https://www.baidu.com"))
        logger.info("导航: {}", obs.success)

        snapshot = await agent.observe()
        logger.info("页面标题: {}", snapshot.title)
        logger.info("可交互元素: {}", len(snapshot.get_interactive_elements()))

        await asyncio.sleep(2)

    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
