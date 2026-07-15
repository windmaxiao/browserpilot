"""
Browser Tool —— Playwright 执行层

封装 Playwright 的所有底层操作，对外统一返回 Observation。
Agent 不直接调用 Playwright API，通过本模块间接操作浏览器。

设计原则：
- 每个公开方法都返回 Observation
- 方法内部自动处理等待和稳定性检查
- 保持无状态，依赖传入的 page 对象
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, Playwright, async_playwright

from agent.schema.observation import Observation
from agent.schema.snapshot import ElementInfo, Snapshot


class BrowserTool:
    """
    Browser Tool 封装。

    支持操作：
    - goto()
    - click()
    - input()
    - select()
    - scroll()
    - wait()
    - download()
    - upload()
    - back()
    - refresh()
    - screenshot()
    """

    def __init__(self, page: Page):
        self._page = page

    # ── 页面属性 ────────────────────────────────────────────────────

    @property
    def page(self) -> Page:
        return self._page

    @property
    def current_url(self) -> str:
        return self._page.url

    @property
    async def current_title(self) -> str:
        return await self._page.title()

    # ── 核心操作 ────────────────────────────────────────────────────

    async def goto(self, url: str, timeout: int = 30000) -> Observation:
        """导航到指定 URL"""
        try:
            await self._page.goto(url, timeout=timeout, wait_until="load")
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            return Observation.fail(error=f"导航失败: {e}", url=self._page.url)

    async def click(
        self,
        selector: str,
        timeout: int = 5000,
        force: bool = False,
    ) -> Observation:
        """点击元素"""
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            await locator.click(force=force, timeout=timeout)
            # 等待页面稳定
            await self._smart_wait()
            new_url = self._page.url
            new_title = await self._page.title()
            return Observation.ok(
                url=new_url,
                title=new_title,
                page_changed=(new_url != old_url),
            )
        except Exception as e:
            return Observation.fail(error=f"点击失败: {e}", url=self._page.url)

    async def input(
        self,
        selector: str,
        text: str,
        timeout: int = 5000,
        clear_first: bool = True,
    ) -> Observation:
        """输入文本"""
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            if clear_first:
                await locator.clear()
            await locator.fill(text)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            return Observation.fail(error=f"输入失败: {e}", url=self._page.url)

    async def select(
        self,
        selector: str,
        value: str,
        timeout: int = 5000,
    ) -> Observation:
        """下拉选择"""
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            await locator.select_option(value)
            new_url = self._page.url
            return Observation.ok(
                url=new_url,
                title=await self._page.title(),
                page_changed=(new_url != old_url),
            )
        except Exception as e:
            return Observation.fail(error=f"选择失败: {e}", url=self._page.url)

    async def scroll(
        self,
        direction: str = "down",
        amount: int = 300,
    ) -> Observation:
        """滚动页面"""
        try:
            delta_y = amount if direction == "down" else -amount
            if direction in ("down", "up"):
                await self._page.evaluate(f"window.scrollBy(0, {delta_y})")
            elif direction == "bottom":
                await self._page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            elif direction == "top":
                await self._page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.3)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            return Observation.fail(error=f"滚动失败: {e}", url=self._page.url)

    async def wait(self, ms: int = 1000) -> Observation:
        """等待指定毫秒数"""
        await asyncio.sleep(ms / 1000)
        return Observation.ok(
            url=self._page.url,
            title=await self._page.title(),
        )

    async def download(
        self,
        selector: str,
        save_path: Optional[str | Path] = None,
        timeout: int = 30000,
    ) -> Observation:
        """下载文件"""
        try:
            async with self._page.expect_download(timeout=timeout) as download_info:
                locator = self._page.locator(selector)
                await locator.click()

            download = await download_info.value
            target_path = save_path or Path.cwd() / download.suggested_filename
            await download.save_as(str(target_path))

            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                data={"download_path": str(target_path)},
            )
        except Exception as e:
            return Observation.fail(error=f"下载失败: {e}", url=self._page.url)

    async def upload(
        self,
        selector: str,
        file_path: str | Path,
        timeout: int = 10000,
    ) -> Observation:
        """上传文件"""
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.set_input_files(str(file_path))
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            return Observation.fail(error=f"上传失败: {e}", url=self._page.url)

    async def back(self) -> Observation:
        """浏览器后退"""
        try:
            await self._page.go_back(wait_until="load")
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            return Observation.fail(error=f"后退失败: {e}", url=self._page.url)

    async def refresh(self) -> Observation:
        """刷新页面"""
        try:
            await self._page.reload(wait_until="load")
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            return Observation.fail(error=f"刷新失败: {e}", url=self._page.url)

    async def screenshot(self, full_page: bool = True) -> Observation:
        """截取页面截图"""
        try:
            screenshot_bytes = await self._page.screenshot(full_page=full_page)
            import base64
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                data={"screenshot_base64": b64},
            )
        except Exception as e:
            return Observation.fail(error=f"截图失败: {e}", url=self._page.url)

    # ── 辅助方法 ────────────────────────────────────────────────────

    async def _smart_wait(self):
        """智能等待页面稳定"""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass


# ── Playwright 生命周期管理 ──────────────────────────────────────────


class BrowserManager:
    """管理 Playwright 浏览器实例的生命周期。"""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser = None
        self._context = None
        self._page: Optional[Page] = None

    async def start(self):
        """启动浏览器"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        self._page = await self._context.new_page()

    async def stop(self):
        """关闭浏览器"""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page

    def create_tool(self) -> BrowserTool:
        return BrowserTool(self.page)
