"""
手动模式 Demo —— 完整 RPA 流程

功能：打开百度 → 检测版本 → 搜索"北京时间"
      → 获取搜索结果 → 点击结果（新标签页）→ 获取页面内容并保存
"""

import asyncio
from datetime import datetime
from pathlib import Path

from loguru import logger

from agent.browser.playwright import BrowserManager, BrowserTool

# ====== 配置 ======
CONFIG = {
    "browser": {
        "headless": False,
    },
    # "business": {
    #     "username": "",
    #     "password": "",
    # },
}

# ====== 常量 ======
SCRIPT_DIR = Path(__file__).parent


async def main():
    """RPA 自动化流程"""
    browser_cfg = CONFIG.get("browser", {})
    # biz_cfg = CONFIG.get("business", {})

    # 1. 创建输出目录
    output_dir = SCRIPT_DIR / "结果" / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"输出目录: {output_dir}")

    # 2. 启动浏览器
    headless = browser_cfg.get("headless", False)
    manager = BrowserManager(headless=headless)
    await manager.start()
    tool = BrowserTool(manager.page)

    try:
        # ====== RPA 流程 ======

        # 5. 打开百度
        logger.info("=== 打开百度 ===")
        await tool.goto("https://www.baidu.com")

        # 6. 检测百度页面版本：经典版 (#kw) vs AI 版 (#chat-textarea)
        chat_textarea = manager.page.locator("#chat-textarea")
        if await chat_textarea.is_visible(timeout=20000):
            await asyncio.sleep(0.5)
            logger.info("检测到 AI 版百度，使用 #chat-textarea 搜索")
            await tool.input("#chat-textarea", "北京时间")
            await asyncio.sleep(0.5)
            await tool.click("#chat-submit-button")
        else:
            logger.info("检测到经典版百度，使用 #kw 搜索")
            await asyncio.sleep(0.5)
            await tool.input("#kw", "北京时间")
            await asyncio.sleep(0.5)
            await tool.click("#su")

        # 7. 获取搜索结果第一条链接的文字
        await asyncio.sleep(0.5)
        first_result = manager.page.locator(
            "//a[starts-with(@href, 'http://www.baidu.com/link?url=')]"
        ).first
        result_text = await first_result.inner_text()
        logger.info("获取结果: {}", result_text)

        # 8. 点击搜索结果，在新标签页打开
        await asyncio.sleep(0.5)
        async with manager.page.context.expect_page() as new_page_info:
            await first_result.click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state()

        # 9. 获取新页面的 URL 和标题
        await asyncio.sleep(0.5)
        new_url = new_page.url
        new_title = await new_page.title()
        logger.info("新页面 URL: {}", new_url)
        logger.info("新页面标题: {}", new_title)

        # 10. 获取新页面 HTML 并保存到文件
        content_html = await new_page.content()
        logger.info("正文 HTML 长度: {}", len(content_html or ""))

        html_path = output_dir / "正文.html"
        with open(str(html_path), "w", encoding="utf-8") as f:
            f.write(content_html or "")
        logger.info("正文已保存: {}", html_path)

        # ====== 流程结束 ======

        logger.success("流程执行完成！5秒后关闭浏览器...")

    except Exception as e:
        logger.opt(exception=True).error(f"流程异常: {e}")
    finally:
        await asyncio.sleep(5)
        await manager.stop()
        logger.info("浏览器已关闭")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
