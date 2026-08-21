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
import base64
import time
from pathlib import Path
from typing import Callable, Optional

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

    def __init__(
        self,
        page: Page,
        on_page_changed: Optional[Callable[[Page], None]] = None,
    ):
        """参数:
            page: 当前活动 Page。
            on_page_changed: 点击打开新标签页并自动跟随时的回调
                             （由 BrowserManager 传入，用于同步当前页面）。
        """
        self._page = page
        self._on_page_changed = on_page_changed
        logger.debug("BrowserTool 创建 | URL: {}", page.url)

    # ── 页面属性 ────────────────────────────────────────────────────

    @property
    def page(self) -> Page:
        return self._page

    @property
    def current_url(self) -> str:
        return self._page.url

    async def current_title(self) -> str:
        """当前页面标题（普通异步方法，非 property，待解决问题 #19）。

        调用方必须写 ``await tool.current_title()``；漏写括号时
        ``await tool.current_title`` 会立即抛 TypeError，而非静默把
        coroutine 塞进数据字段（旧 async property 的隐患）。
        """
        return await self._page.title()

    # ── 核心操作 ────────────────────────────────────────────────────

    async def goto(self, url: str, timeout: int = 30000) -> Observation:
        """导航到指定 URL"""
        logger.info("🌐 goto: {}", url)
        start = time.time()
        try:
            await self._page.goto(url, timeout=timeout, wait_until="load")
            elapsed = time.time() - start
            # title 只取一次（待解决问题 #25），避免日志与返回各发一次 CDP
            title = await self._page.title()
            logger.info("✅ goto 完成 | URL: {} | title: {} | {:.1f}s",
                        self._page.url, title, elapsed)
            return Observation.ok(
                url=self._page.url,
                title=title,
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
        frame_path=(),
    ) -> Observation:
        """点击元素

        page_changed 判定（V0.2 增强）：
        - URL 变化
        - 标题变化
        - DOM 指纹变化（元素数 / 文本长度，捕获 SPA 等无导航内容变化）
        - 点击打开新标签页 → 自动跟随并切换当前页面（target=_blank 链接）
        """
        logger.info("🖱️ click: {}", selector)
        start = time.time()
        try:
            locator = self._locator(selector, frame_path)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            old_title = await self._page.title()
            old_fp = await self._page_fingerprint()
            old_pages = self._current_pages()
            await locator.click(force=force, timeout=timeout)
            await self._smart_wait()
            new_url = self._page.url
            if new_url == old_url:
                # URL 未变化 → 可能打开了新标签页，短轮询检测（快速失败，避免每次点击固定等待）
                new_page = await self._detect_new_page(old_pages)
            else:
                # URL 已变化 → 同页导航发生，无需检测新标签页
                new_page = None
            if new_page is not None:
                return await self._switch_to_page(new_page)
            new_title = await self._page.title()
            new_fp = await self._page_fingerprint()
            elapsed = time.time() - start
            changed = (
                (new_url != old_url)
                or (new_title != old_title)
                or (old_fp != new_fp)
            )
            if changed:
                logger.info("✅ click 完成 | 页面变化 | {:.1f}s", elapsed)
            else:
                logger.info("✅ click 完成 | 页面无变化 | {:.1f}s", elapsed)
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
        frame_path=(),
    ) -> Observation:
        """输入文本"""
        logger.info("⌨️ input: {} | text: {}", selector, text[:80])
        start = time.time()
        try:
            locator = self._locator(selector, frame_path)
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
        frame_path=(),
    ) -> Observation:
        """下拉选择"""
        logger.info("📋 select: {} → {}", selector, value)
        start = time.time()
        try:
            locator = self._locator(selector, frame_path)
            await locator.wait_for(state="visible", timeout=timeout)
            old_url = self._page.url
            old_title = await self._page.title()
            await locator.select_option(value)
            await self._smart_wait()
            new_url = self._page.url
            new_title = await self._page.title()
            elapsed = time.time() - start
            logger.info("✅ select 完成 | {} | {:.1f}s", selector, elapsed)
            return Observation.ok(
                url=new_url,
                title=new_title,
                page_changed=(new_url != old_url) or (new_title != old_title),
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
            # 入口强制转换 amount，避免 f-string 拼接用户输入导致的 JS 注入
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                return Observation.fail(
                    error=f"滚动参数非法: amount={amount!r}（应为整数）",
                    url=self._page.url,
                )

            if direction == "down":
                # 参数化传值，禁止字符串拼接
                await self._page.evaluate(
                    "(delta) => window.scrollBy(0, delta)", amount
                )
            elif direction == "up":
                await self._page.evaluate(
                    "(delta) => window.scrollBy(0, delta)", -amount
                )
            elif direction == "bottom":
                await self._page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)"
                )
            elif direction == "top":
                await self._page.evaluate("() => window.scrollTo(0, 0)")
            else:
                return Observation.fail(
                    error=f"滚动方向非法: {direction}（应为 down/up/top/bottom）",
                    url=self._page.url,
                )
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
        start = time.time()
        try:
            if not isinstance(ms, int) or isinstance(ms, bool) or ms < 0:
                return Observation.fail(
                    error=f"等待参数非法: ms={ms!r}（应为非负整数）",
                    url=self._page.url,
                )
            await asyncio.sleep(ms / 1000)
            return Observation.ok(
                url=self._page.url,
                title=await self._page.title(),
            )
        except Exception as e:
            logger.error("❌ wait 失败: {}", e)
            return Observation.fail(error=f"等待失败: {e}", url=self._page.url)

    async def download(
        self,
        selector: str,
        save_path: Optional[str | Path] = None,
        timeout: int = 30000,
        frame_path=(),
    ) -> Observation:
        """下载文件"""
        logger.info("⬇️ download: {}", selector)
        start = time.time()
        try:
            async with self._page.expect_download(timeout=timeout) as download_info:
                locator = self._locator(selector, frame_path)
                await locator.click()

            download = await download_info.value
            target_path = save_path or Path.cwd() / download.suggested_filename
            await download.save_as(str(target_path))
            await self._smart_wait()

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
        frame_path=(),
    ) -> Observation:
        """上传文件"""
        logger.info("⬆️ upload: {} → {}", selector, file_path)
        start = time.time()
        try:
            locator = await self._locator(selector, frame_path)
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

    def _locator(self, selector: str, frame_path=()):
        """按 (selector, frame_path) 解析最终 Locator（V1.0 子计划 A）。

        空 frame_path → 直接 `page.locator(selector)`（主页面，向后兼容）；
        非空 → 逐层 `frame_locator(seg)` 穿透 iframe，最后对目标 frame 定位元素。
        frame_locator / locator 均为同步构建（#19 去除多余的 async）。
        """
        from playwright.async_api import Locator
        target: object = self._page
        for seg in frame_path:
            target = target.frame_locator(seg)
        locator: Locator = target.locator(selector)
        return locator

    async def _page_fingerprint(self) -> dict:
        """轻量 DOM 指纹，用于检测页面内容变化（SPA 等无导航场景）。

        返回 {"elements": 元素总数, "text_len": body 可见文本长度}，
        任一项发生变化即视为页面内容发生变化。跨所有 frame（主页面 + 嵌套
        iframe）累加，使纯 iframe 内容变化也能被判定为 page_changed（#5）。
        注意：动态内容（时钟、轮播等）可能造成轻微误报，属已知取舍。
        """
        elements = 0
        text_len = 0
        for frame in self._page.frames:
            try:
                data = await frame.evaluate(
                    """() => {
                        const body = document.body;
                        return {
                            elements: document.getElementsByTagName('*').length,
                            text_len: body ? body.innerText.length : 0,
                        };
                    }"""
                )
                elements += data.get("elements", 0)
                text_len += data.get("text_len", 0)
            except Exception:
                continue  # 单帧获取失败不中断整体指纹
        return {"elements": elements, "text_len": text_len}

    async def _smart_wait(self):
        """智能等待页面稳定

        Playwright 的 click() 已内置导航等待（no_wait_after=False），
        此方法作为轻量安全垫，仅需等待 DOM 加载完成即可。
        """
        try:
            await self._page.wait_for_load_state("load", timeout=3000)
        except Exception:
            pass

    # ── 新标签页跟随（target=_blank 链接）───────────────────────────────

    def _current_pages(self) -> set:
        """当前 context 中的所有 Page（异常时返回空集，保持向后兼容）。"""
        try:
            return set(self._page.context.pages)
        except Exception:
            return set()

    async def _detect_new_page(
        self, old_pages: set, timeout: float = 1.0
    ) -> Optional[Page]:
        """点击后若打开了新标签页则返回该 Page，否则返回 None。

        新标签页创建存在延迟（JS/浏览器行为），故采用轮询等待而非只查一次：
        百度等站点点击 target=_blank 链接后，新 Page 可能数百毫秒后才出现。
        为避免每次点击都固定等待，默认轮询窗口已缩短为 1.0s（快速失败）。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pages = self._page.context.pages
            except Exception:
                return None
            for p in pages:
                if p in old_pages or p is self._page:
                    continue
                try:
                    closed = p.is_closed()
                    if asyncio.iscoroutine(closed):
                        closed = await closed
                except Exception:
                    closed = False
                if not closed:
                    return p
            await asyncio.sleep(0.2)
        return None

    async def _switch_to_page(self, page: Page) -> Observation:
        """跟随新标签页：更新内部引用并通知外部订阅者（BrowserManager）。"""
        try:
            await page.wait_for_load_state("load", timeout=5000)
        except Exception:
            pass
        self._page = page
        if self._on_page_changed:
            self._on_page_changed(page)
        title = await self._page.title()
        logger.info("🖱️ click 打开新标签页，已自动跟随 | URL: {} | title: {}",
                    self._page.url, title)
        return Observation.ok(
            url=self._page.url,
            title=title,
            page_changed=True,
        )


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
        self._page_listeners: list = []
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

            # 本框架接管/覆盖的 launch 键：这些值由框架决定，不接受用户参数直接覆盖
            _managed = {"headless", "slow_mo", "args", "executable_path", "channel", "proxy"}
            # 其余合法 launch 参数（user_data_dir/env/devtools/IgnoreDefaultArgs 等）
            # 原样透传，不再静默丢弃（待解决问题 #4）
            launch_options = {
                k: v for k, v in self._launch_kwargs.items() if k not in _managed
            }

            launch_options.update({
                "headless": self._headless,
                "slow_mo": self._slow_mo,
                "args": launch_args,
            })

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
            # 待解决问题 #17：逐项清理已启动的资源（page → context → browser →
            # playwright 驱动），避免部分失败时浏览器进程与驱动连接泄漏。
            for obj, name in (
                (self._page, "页面"), (self._context, "context"),
                (self._browser, "浏览器"),
            ):
                if obj is not None:
                    try:
                        await obj.close()
                    except Exception as close_e:
                        logger.warning("⚠️ 启动失败后关闭{}失败: {}", name, close_e)
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception as close_e:
                    logger.warning("⚠️ 启动失败后停止 playwright 驱动失败: {}", close_e)
            raise

    async def stop(self):
        """关闭浏览器

        每步独立 try/except：单个资源关闭失败（如页面已被外部关闭）时，
        不中断其余资源清理，避免浏览器进程与驱动连接泄漏。
        """
        logger.info("🛑 关闭浏览器...")
        if self._page:
            try:
                await self._page.close()
            except Exception as e:
                logger.warning("⚠️ 关闭页面失败: {}", e)
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("⚠️ 关闭 context 失败: {}", e)
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning("⚠️ 关闭浏览器失败: {}", e)
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("⚠️ 停止 Playwright 失败: {}", e)
        logger.info("✅ 浏览器已关闭")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._page

    def create_tool(self) -> BrowserTool:
        return BrowserTool(self.page, on_page_changed=self.switch_page)

    def switch_page(self, page: Page) -> None:
        """切换当前活动页面，并通知所有订阅者（如 SnapshotGenerator）。

        点击 target=_blank 链接自动跟随新标签页时由 BrowserTool 调用。
        """
        self._page = page
        for cb in self._page_listeners:
            cb(page)

    def subscribe_page(self, callback) -> None:
        """订阅页面切换事件：跟随新标签页后回调 callback(page)。"""
        self._page_listeners.append(callback)
