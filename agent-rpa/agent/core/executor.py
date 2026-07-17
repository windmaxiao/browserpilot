"""
Executor —— 动作执行器

将 Action schema 转换为实际的 Playwright 操作。
Executor 是 Agent 与 Browser Tool 之间的桥梁。
"""

from __future__ import annotations

from loguru import logger

from agent.browser.playwright import BrowserTool
from agent.schema.action import Action
from agent.schema.observation import Observation

# 已知 HTML 标签名集合（_resolve_selector 用于判断纯标签选择器）
_HTML_TAGS = frozenset({
    "input", "button", "a", "select", "textarea",
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "label", "li", "ul", "ol", "table", "tr", "td", "th",
    "form", "img", "nav", "header", "footer", "section",
    "article", "main", "aside", "iframe", "video", "audio",
    "canvas", "figure", "figcaption", "dl", "dt", "dd",
})


class Executor:
    """
    执行 Agent 规划的 Action。
    Action → Executor → Browser Tool → Observation
    """

    def __init__(self, browser_tool: BrowserTool):
        self._tool = browser_tool
        logger.debug("Executor 初始化完成")

    async def execute(self, action: Action) -> Observation:
        """
        执行一个 Action，返回 Observation。

        参数验证失败时直接返回失败 Observation，不调用浏览器。
        """
        errors = action.validate()
        if errors:
            logger.warning("Action 验证失败: {}", "; ".join(errors))
            return Observation.fail(
                error=f"Action 验证失败: {'; '.join(errors)}",
            )

        dispatch = {
            "click": self._execute_click,
            "input": self._execute_input,
            "select": self._execute_select,
            "goto": self._execute_goto,
            "scroll": self._execute_scroll,
            "wait": self._execute_wait,
            "download": self._execute_download,
            "upload": self._execute_upload,
            "back": self._execute_back,
            "refresh": self._execute_refresh,
            "screenshot": self._execute_screenshot,
            "done": self._execute_done,
        }

        handler = dispatch.get(action.action)
        if handler is None:
            logger.error("未知动作类型: {}", action.action)
            return Observation.fail(error=f"未知动作: {action.action}")

        logger.debug("分发动: {} | target={}", action.action, action.target)
        return await handler(action)

    # ── 各动作的具体执行 ────────────────────────────────────────────

    async def _execute_click(self, action: Action) -> Observation:
        if action.target:
            selector = self._resolve_selector(action.target, action.params)
            logger.debug("click → selector: {}", selector)
            return await self._tool.click(
                selector,
                timeout=action.params.get("timeout", 5000),
                force=action.params.get("force", False),
            )
        logger.warning("click 动作缺少 target")
        return Observation.fail(error="click 动作缺少 target")

    async def _execute_input(self, action: Action) -> Observation:
        if action.target and action.value is not None:
            selector = self._resolve_selector(action.target, action.params)
            logger.debug("input → selector: {} | value: {}", selector, action.value[:80])
            return await self._tool.input(
                selector,
                action.value,
                timeout=action.params.get("timeout", 5000),
                clear_first=action.params.get("clear_first", True),
            )
        logger.warning("input 动作缺少 target 或 value")
        return Observation.fail(error="input 动作缺少 target 或 value")

    async def _execute_select(self, action: Action) -> Observation:
        if action.target and action.value is not None:
            selector = self._resolve_selector(action.target, action.params)
            logger.debug("select → selector: {} | value: {}", selector, action.value)
            return await self._tool.select(
                selector,
                action.value,
                timeout=action.params.get("timeout", 5000),
            )
        logger.warning("select 动作缺少 target 或 value")
        return Observation.fail(error="select 动作缺少 target 或 value")

    async def _execute_goto(self, action: Action) -> Observation:
        if action.value:
            logger.debug("goto → URL: {}", action.value)
            return await self._tool.goto(
                action.value,
                timeout=action.params.get("timeout", 30000),
            )
        logger.warning("goto 动作缺少 URL")
        return Observation.fail(error="goto 动作缺少 URL")

    async def _execute_scroll(self, action: Action) -> Observation:
        direction = action.params.get("direction", "down")
        amount = action.params.get("amount", 300)
        logger.debug("scroll → direction: {} | amount: {}", direction, amount)
        return await self._tool.scroll(direction=direction, amount=amount)

    async def _execute_wait(self, action: Action) -> Observation:
        ms = action.params.get("ms", 1000)
        logger.debug("wait → {}ms", ms)
        return await self._tool.wait(ms=ms)

    async def _execute_download(self, action: Action) -> Observation:
        if action.target:
            selector = self._resolve_selector(action.target, action.params)
            save_path = action.params.get("save_path")
            logger.info("下载文件 → selector: {} | save_path: {}", selector, save_path)
            return await self._tool.download(
                selector,
                save_path=save_path,
                timeout=action.params.get("timeout", 30000),
            )
        logger.warning("download 动作缺少 target")
        return Observation.fail(error="download 动作缺少 target")

    async def _execute_upload(self, action: Action) -> Observation:
        if action.target and action.value:
            selector = self._resolve_selector(action.target, action.params)
            logger.info("上传文件 → selector: {} | file: {}", selector, action.value)
            return await self._tool.upload(
                selector,
                action.value,
                timeout=action.params.get("timeout", 10000),
            )
        logger.warning("upload 动作缺少 target 或 value (file path)")
        return Observation.fail(error="upload 动作缺少 target 或 value (file path)")

    async def _execute_back(self, action: Action) -> Observation:
        logger.debug("浏览器后退")
        return await self._tool.back()

    async def _execute_refresh(self, action: Action) -> Observation:
        logger.debug("刷新页面")
        return await self._tool.refresh()

    async def _execute_screenshot(self, action: Action) -> Observation:
        full_page = action.params.get("full_page", True)
        logger.info("截取页面截图 | full_page={}", full_page)
        return await self._tool.screenshot(full_page=full_page)

    async def _execute_done(self, action: Action) -> Observation:
        """任务完成标志"""
        logger.info("🏁 任务完成: {}", action.value or "无备注")
        return Observation.ok(
            url=self._tool.current_url,
            title=await self._tool.current_title,
            data={"message": action.value or "任务完成", "done": True},
        )

    # ── 辅助方法 ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_selector(target: str, params: dict) -> str:
        """
        将 Agent 的语义 target 解析为 Playwright 选择器。

        优先级：
        1. params 中显式指定的 selector
        2. CSS 选择器前缀 (# . [ :)
        3. 纯 HTML 标签名（如 input、button）
        4. 语义化文本匹配 fallback → :has-text("")
        """
        explicit = params.get("selector")
        if explicit:
            return explicit

        if target.startswith(("#", ".", "[", ":")):
            return target

        if target in _HTML_TAGS:
            return target

        safe = target.replace('"', '\\"')
        return f':has-text("{safe}")'
