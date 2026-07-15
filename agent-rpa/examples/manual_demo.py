"""
手动模式 Demo —— 直接使用 Browser Tool 和 Executor

展示 V0.1 执行层的能力：不依赖 Agent Loop，手动调用工具。
"""

import asyncio
import logging

from agent.browser.playwright import BrowserManager, BrowserTool
from agent.browser.snapshot import SnapshotGenerator
from agent.schema.action import goto, click, input_text, done

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """手动模式示例：打开百度并搜索"""
    manager = BrowserManager(headless=False)
    await manager.start()

    try:
        tool = BrowserTool(manager.page)
        snapshot_gen = SnapshotGenerator(manager.page)

        # 1. 打开百度
        logger.info("=== 步骤 1: 打开百度 ===")
        obs = await tool.goto("https://www.baidu.com")
        print(f"  导航结果: success={obs.success}, title={obs.title}")

        # 2. 获取 Snapshot
        logger.info("=== 步骤 2: 获取页面 Snapshot ===")
        snapshot = await snapshot_gen.generate()
        print(f"  页面标题: {snapshot.title}")
        print(f"  按钮数: {len(snapshot.buttons)}")
        print(f"  输入框数: {len(snapshot.inputs)}")
        print(f"  链接数: {len(snapshot.links)}")

        # 3. 搜索
        logger.info("=== 步骤 3: 输入搜索关键词 ===")
        obs = await tool.input("#kw", "Agentic RPA")
        print(f"  输入结果: success={obs.success}")

        # 4. 点击搜索按钮
        logger.info("=== 步骤 4: 点击搜索 ===")
        obs = await tool.click("#su")
        print(f"  点击结果: success={obs.success}")

        # 5. 等待结果加载
        await asyncio.sleep(2)

        # 6. 查看结果页面 Snapshot
        logger.info("=== 步骤 5: 查看搜索结果 ===")
        snapshot = await snapshot_gen.generate()
        print(f"  页面标题: {snapshot.title}")
        print(f"  页面 URL: {snapshot.url}")

        logger.info("=== 完成 ===")
        await asyncio.sleep(3)

    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
