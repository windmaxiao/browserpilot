"""
百度搜索 Demo —— 规则引擎跑真实网站（V0.2 验证）

让 RuleBasedPlanner 自动完成真实百度搜索：
    goto(百度) → 输入关键词 → 点击「百度一下」→ 结果出现 → done

注意：当前百度为 AI 版页面（textarea 搜索框 + 「百度一下」按钮），
规则引擎通过"搜索框兜底 + 提交按钮文本匹配"覆盖，无需自定义规则。
"""

import asyncio

from loguru import logger

from agent.browser.playwright import BrowserManager
from agent.browser.snapshot import SnapshotGenerator
from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import RuleBasedPlanner
from agent.logging import setup_logging


async def main():
    # 控制台 + logs/ 目录按天滚动文件
    setup_logging()
    goal = "打开 https://www.baidu.com 查找 北京时间 点击 北京时间 - 百度百科 等待 页面加载完成"
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
            logger.info("🎉 规则引擎在真实百度网站跑通！")
            for step in agent.history:
                a = step["action"]
                o = step["observation"]
                logger.info(
                    "  Step {}: {}({}) → success={}",
                    step["step"], a.action, a.value or a.target_id, o.success,
                )
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
