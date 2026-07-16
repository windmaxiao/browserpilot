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
import time
from pathlib import Path
from typing import Optional

from loguru import logger
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
        logger.debug("BrowserTool 创建 | URL: {}", page.url)

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
        logger.info("🌐 goto: {}", url)
        start = time.time()
        try:
            await self._page.goto(url, timeout=timeout, wait_until="load")
            elapsed = time.time() - start
            logger.info("✅ goto 完成 | URL: {} | title: {} | {:.1f}s",
                        self._page.url, await self._page.title(), elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ goto 失败 | URL: {} | {:.1f}s | 错误: {}", url, elapsed, e)
            return Observation.fail(error=f"导航失败: {e}", url=self._page.url)

    async def click(
        self,
        selector: str,
        timeout: int = 5000,
        force: bool = False,
    ) -> Observation:
        """点击元素"""
        logger.info("🖱️ click: {}", selector)
        start = time.time()
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            await locator.click(force=force, timeout=timeout)
            await self._smart_wait()
            new_url = self._page.url
            new_title = await self._page.title()
            elapsed = time.time() - start
            changed = new_url != old_url
            if changed:
                logger.info("✅ click 完成 | 发生跳转 → {} | {:.1f}s", new_url, elapsed)
            else:
                logger.info("✅ click 完成 | 页面无跳转 | {:.1f}s", elapsed)
            return Observation.ok(
                url=new_url,
                title=new_title,
                page_changed=changed,
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ click 失败 | selector: {} | {:.1f}s | 错误: {}",
                         selector, elapsed, e)
            return Observation.fail(error=f"点击失败: {e}", url=self._page.url)

    async def input(
        self,
        selector: str,
        text: str,
        timeout: int = 5000,
        clear_first: bool = True,
    ) -> Observation:
        """输入文本"""
        logger.info("⌨️ input: {} | text: {}", selector, text[:80])
        start = time.time()
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            if clear_first:
                await locator.clear()
            await locator.fill(text)
            elapsed = time.time() - start
            logger.info("✅ input 完成 | {} | {:.1f}s", selector, elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ input 失败 | selector: {} | {:.1f}s | 错误: {}",
                         selector, elapsed, e)
            return Observation.fail(error=f"输入失败: {e}", url=self._page.url)

    async def select(
        self,
        selector: str,
        value: str,
        timeout: int = 5000,
    ) -> Observation:
        """下拉选择"""
        logger.info("📋 select: {} → {}", selector, value)
        start = time.time()
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            await locator.select_option(value)
            new_url = self._page.url
            elapsed = time.time() - start
            logger.info("✅ select 完成 | {} | {:.1f}s", selector, elapsed)
            return Observation.ok(
                url=new_url,
                title=await self._page.title(),
                page_changed=(new_url != old_url),
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ select 失败 | selector: {} | {:.1f}s | 错误: {}",
                         selector, elapsed, e)
            return Observation.fail(error=f"选择失败: {e}", url=self._page.url)

    async def scroll(
        self,
        direction: str = "down",
        amount: int = 300,
    ) -> Observation:
        """滚动页面"""
        logger.debug("📜 scroll: {} | {}", direction, amount)
        start = time.time()
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
            elapsed = time.time() - start
            logger.debug("✅ scroll 完成 | {:.1f}s", elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            logger.error("❌ scroll 失败: {}", e)
            return Observation.fail(error=f"滚动失败: {e}", url=self._page.url)

    async def wait(self, ms: int = 1000) -> Observation:
        """等待指定毫秒数"""
        logger.debug("⏳ wait: {}ms", ms)
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
        logger.info("⬇️ download: {}", selector)
        start = time.time()
        try:
            async with self._page.expect_download(timeout=timeout) as download_info:
                locator = self._page.locator(selector)
                await locator.click()

            download = await download_info.value
            target_path = save_path or Path.cwd() / download.suggested_filename
            await download.save_as(str(target_path))

            elapsed = time.time() - start
            logger.info("✅ download 完成 | 保存到: {} | {:.1f}s", target_path, elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                data={"download_path": str(target_path)},
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ download 失败 | {:.1f}s | 错误: {}", elapsed, e)
            return Observation.fail(error=f"下载失败: {e}", url=self._page.url)

    async def upload(
        self,
        selector: str,
        file_path: str | Path,
        timeout: int = 10000,
    ) -> Observation:
        """上传文件"""
        logger.info("⬆️ upload: {} → {}", selector, file_path)
        start = time.time()
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.set_input_files(str(file_path))
            elapsed = time.time() - start
            logger.info("✅ upload 完成 | {:.1f}s", elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            logger.error("❌ upload 失败: {}", e)
            return Observation.fail(error=f"上传失败: {e}", url=self._page.url)

    async def back(self) -> Observation:
        """浏览器后退"""
        logger.info("◀️ back")
        start = time.time()
        try:
            await self._page.go_back(wait_until="load")
            elapsed = time.time() - start
            logger.info("✅ back 完成 | URL: {} | {:.1f}s", self._page.url, elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            logger.error("❌ back 失败: {}", e)
            return Observation.fail(error=f"后退失败: {e}", url=self._page.url)

    async def refresh(self) -> Observation:
        """刷新页面"""
        logger.info("🔄 refresh")
        start = time.time()
        try:
            await self._page.reload(wait_until="load")
            elapsed = time.time() - start
            logger.info("✅ refresh 完成 | {:.1f}s", elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                page_changed=True,
            )
        except Exception as e:
            logger.error("❌ refresh 失败: {}", e)
            return Observation.fail(error=f"刷新失败: {e}", url=self._page.url)

    async def screenshot(self, full_page: bool = True) -> Observation:
        """截取页面截图"""
        logger.info("📸 screenshot | full_page={}", full_page)
        start = time.time()
        try:
            screenshot_bytes = await self._page.screenshot(full_page=full_page)
            import base64
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            elapsed = time.time() - start
            logger.info("✅ screenshot 完成 | {} bytes | {:.1f}s",
                        len(screenshot_bytes), elapsed)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
                data={"screenshot_base64": b64},
            )
        except Exception as e:
            logger.error("❌ screenshot 失败: {}", e)
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

    def __init__(self, headless: bool = True, slow_mo: int = 50, **launch_kwargs):
        """参数:
            headless: 是否启用无头模式
            slow_mo: 操作间延迟（毫秒），模拟人类操作速度
            **launch_kwargs: 传递给 playwright.chromium.launch 的额外参数
                           （如 channel, executable_path, proxy 等）
        """
        self._headless = headless
        self._slow_mo = slow_mo
        self._launch_kwargs = launch_kwargs
        self._playwright: Optional[Playwright] = None
        self._browser = None
        self._context = None
        self._page: Optional[Page] = None
        logger.debug("BrowserManager 创建 | headless={} slow_mo={} kwargs={}",
                     headless, slow_mo, launch_kwargs)

    async def start(self):
        """启动浏览器"""
        logger.info("🚀 启动浏览器 | headless={}", self._headless)
        start = time.time()
        try:
            self._playwright = await async_playwright().start()

            # 浏览器稳定性与反检测参数
            _default_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ]
            # 合并用户自定义 args（去重）
            _user_args = self._launch_kwargs.get("args", [])
            launch_args = list(_default_args)
            for a in _user_args:
                if a not in launch_args:
                    launch_args.append(a)

            if not self._headless:
                launch_args.append("--start-maximized")

            # 构建 launch 选项
            launch_options = {
                "headless": self._headless,
                "slow_mo": self._slow_mo,
                "args": launch_args,
            }

            # 优先使用 executable_path，其次 channel，否则用 Playwright 内置 Chromium
            if self._launch_kwargs.get("executable_path"):
                launch_options["executable_path"] = self._launch_kwargs["executable_path"]
            elif self._launch_kwargs.get("channel"):
                launch_options["channel"] = self._launch_kwargs["channel"]

            if self._launch_kwargs.get("proxy"):
                launch_options["proxy"] = self._launch_kwargs["proxy"]

            self._browser = await self._playwright.chromium.launch(**launch_options)

            if not self._headless:
                self._context = await self._browser.new_context(
                    no_viewport=True,
                    locale="zh-CN",
                )
            else:
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    locale="zh-CN",
                )

            self._page = await self._context.new_page()
            elapsed = time.time() - start
            logger.info("✅ 浏览器启动完成 | {:.1f}s", elapsed)
        except Exception as e:
            elapsed = time.time() - start
            logger.error("❌ 浏览器启动失败 | {:.1f}s | 错误: {}", elapsed, e)
            raise

    async def stop(self):
        """关闭浏览器"""
        logger.info("🛑 关闭浏览器...")
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("✅ 浏览器已关闭")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page

    def create_tool(self) -> BrowserTool:
        return BrowserTool(self.page)
