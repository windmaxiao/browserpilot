"""
Agent 模式 Demo —— 规则驱动的完整 Agent Loop（V0.2）

运行环境：Playwright 浏览器（默认有头模式，会弹出浏览器窗口）
目标：让 RuleBasedPlanner 自动完成一次完整的搜索任务

执行流程（全部由规则引擎驱动）：
    goto(本地搜索页) → 输入关键词 → 点击搜索按钮 → 识别结果 → done

本地搜索页（search_page.html）保证流程确定性，不依赖外部网站；
同一套规则引擎稍加扩展即可用于真实网站（如百度搜索）。
"""

import asyncio
from pathlib import Path

from loguru import logger

from agent.browser.playwright import BrowserManager
from agent.browser.snapshot import SnapshotGenerator
from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import RuleBasedPlanner
from agent.logging import setup_logging

SCRIPT_DIR = Path(__file__).parent
SEARCH_PAGE_URL = (SCRIPT_DIR / "search_page.html").as_uri()


async def main():
    # 控制台 + logs/ 目录按天滚动文件
    setup_logging()
    # 目标同时包含 URL（触发导航规则）和搜索词（触发搜索规则）
    goal = f"打开 {SEARCH_PAGE_URL} 查找 北京时间"
    logger.info("🎯 目标: {}", goal)

    manager = BrowserManager(headless=False)
    await manager.start()
    try:
        tool = manager.create_tool()
        observer = Observer(SnapshotGenerator(manager.page))
        agent = Agent(
            observer=observer,
            planner=RuleBasedPlanner(),
            executor=Executor(tool),
            max_steps=20,
        )

        result = await agent.run(goal)

        logger.info("📋 任务结果: success={} | data={}", result.success, result.data)
        if result.is_error:
            logger.error("❌ 任务失败: {}", result.error)
        else:
            logger.info("🎉 规则 Agent 端到端跑通！")
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
