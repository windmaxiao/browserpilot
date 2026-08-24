"""
Executor —— 动作执行器

将 Action schema 转换为实际的 Playwright 操作。
Executor 是 Agent 与 Browser Tool 之间的桥梁。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from loguru import logger

from agent.browser.playwright import BrowserTool
from agent.schema.action import Action, is_path_within_allowed
from agent.schema.observation import Observation
from agent.schema.snapshot import Snapshot

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

    def __init__(
        self,
        browser_tool: BrowserTool,
        *,
        download_dir: Optional[str | Path] = None,
        allowed_upload_dirs: Optional[list] = None,
    ):
        """
        Args:
            browser_tool: BrowserTool 实例
            download_dir: 下载落盘目录（M5 调用方策略）。模型无法指定 save_path，
                未提供时 BrowserTool 默认落盘当前目录 + 服务器文件名
            allowed_upload_dirs: 允许上传文件所在目录白名单（M5）。未配置时
                upload 动作一律拒绝，防止模型读取/上传本地任意文件
        """
        self._tool = browser_tool
        self._download_dir = download_dir
        self._allowed_upload_dirs = allowed_upload_dirs
        self._element_map: dict[str, str] = {}
        self._frame_map: dict[str, tuple] = {}
        # 分发表在 __init__ 构建一次（待解决问题 #25），避免每次 execute 重建
        self._dispatch: dict[str, Any] = {
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
        logger.debug("Executor 初始化完成")

    async def execute(self, action: Action, snapshot: Snapshot | None = None) -> Observation:
        """
        执行一个 Action，返回 Observation。

        参数验证失败时直接返回失败 Observation，不调用浏览器。

        Args:
            action: 要执行的动作
            snapshot: 当前页面的 Snapshot（用于通过 target_id 定位元素）
        """
        if snapshot:
            self._element_map = self._build_element_map(snapshot)
            self._frame_map = self._build_frame_map(snapshot)
        else:
            self._element_map = {}
            self._frame_map = {}

        errors = action.validate()
        if errors:
            logger.warning("Action 验证失败: {}", "; ".join(errors))
            return Observation.fail(
                error=f"Action 验证失败: {'; '.join(errors)}",
            )

        handler = self._dispatch.get(action.action)
        if handler is None:
            logger.error("未知动作类型: {}", action.action)
            return Observation.fail(error=f"未知动作: {action.action}")

        logger.debug("分发动作: {} | target={} | target_id={}", action.action, action.target, action.target_id)
        return await handler(action)

    # ── 各动作的具体执行 ────────────────────────────────────────────

    async def _execute_click(self, action: Action) -> Observation:
        selector = self._resolve_target(action)
        if selector:
            logger.debug("click → selector: {}", selector)
            return await self._tool.click(
                selector,
                timeout=action.params.get("timeout", 5000),
                force=action.params.get("force", False),
                frame_path=self._resolve_frame_path(action),
            )
        if action.target_id:
            return Observation.fail(
                error=f"click 动作 target_id '{action.target_id}' 在 Snapshot 中未找到"
            )
        logger.warning("click 动作缺少 target 或 target_id")
        return Observation.fail(error="click 动作缺少 target 或 target_id")

    async def _execute_input(self, action: Action) -> Observation:
        if action.value is None:
            logger.warning("input 动作缺少 value")
            return Observation.fail(error="input 动作缺少 value")
        selector = self._resolve_target(action)
        if selector:
            logger.debug("input → selector: {} | value: {}", selector, str(action.value)[:80])
            return await self._tool.input(
                selector,
                action.value,
                timeout=action.params.get("timeout", 5000),
                clear_first=action.params.get("clear_first", True),
                frame_path=self._resolve_frame_path(action),
            )
        if action.target_id:
            return Observation.fail(
                error=f"input 动作 target_id '{action.target_id}' 在 Snapshot 中未找到"
            )
        logger.warning("input 动作缺少 target 或 target_id")
        return Observation.fail(error="input 动作缺少 target 或 target_id")

    async def _execute_select(self, action: Action) -> Observation:
        if action.value is None:
            logger.warning("select 动作缺少 value")
            return Observation.fail(error="select 动作缺少 value")
        selector = self._resolve_target(action)
        if selector:
            logger.debug("select → selector: {} | value: {}", selector, action.value)
            return await self._tool.select(
                selector,
                action.value,
                timeout=action.params.get("timeout", 5000),
                frame_path=self._resolve_frame_path(action),
            )
        if action.target_id:
            return Observation.fail(
                error=f"select 动作 target_id '{action.target_id}' 在 Snapshot 中未找到"
            )
        logger.warning("select 动作缺少 target 或 target_id")
        return Observation.fail(error="select 动作缺少 target 或 target_id")

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
        # 等待时长：params.ms（毫秒）优先；LLM 常用 value 表达（如 5000），兜底支持
        ms = action.params.get("ms")
        if ms is None and action.value:
            try:
                ms = int(str(action.value).strip())
            except ValueError:
                ms = None
        if ms is None:
            ms = 1000
        logger.debug("wait → {}ms", ms)
        return await self._tool.wait(ms=ms)

    async def _execute_download(self, action: Action) -> Observation:
        selector = self._resolve_target(action)
        if selector:
            # M5：模型无法指定 save_path（parse 层已剥离），落盘目录取调用方策略
            save_path = action.params.get("save_path") or self._download_dir
            logger.info("下载文件 → selector: {} | save_path: {}", selector, save_path)
            return await self._tool.download(
                selector,
                save_path=save_path,
                timeout=action.params.get("timeout", 30000),
                frame_path=self._resolve_frame_path(action),
            )
        if action.target_id:
            return Observation.fail(
                error=f"download 动作 target_id '{action.target_id}' 在 Snapshot 中未找到"
            )
        logger.warning("download 动作缺少 target")
        return Observation.fail(error="download 动作缺少 target 或 target_id")

    async def _execute_upload(self, action: Action) -> Observation:
        if not action.value:
            logger.warning("upload 动作缺少 value (file path)")
            return Observation.fail(error="upload 动作缺少 value (file path)")
        # M5：未显式配置允许上传目录 → 一律拒绝；配置后 value 须落在允许目录内
        if not is_path_within_allowed(action.value, self._allowed_upload_dirs):
            logger.warning("upload 文件路径越权或未启用上传: {}", action.value)
            return Observation.fail(
                error=f"upload 文件路径越权（未显式允许）: {action.value!r}"
            )
        selector = self._resolve_target(action)
        if selector:
            logger.info("上传文件 → selector: {} | file: {}", selector, action.value)
            return await self._tool.upload(
                selector,
                action.value,
                timeout=action.params.get("timeout", 10000),
                frame_path=self._resolve_frame_path(action),
            )
        if action.target_id:
            return Observation.fail(
                error=f"upload 动作 target_id '{action.target_id}' 在 Snapshot 中未找到"
            )
        logger.warning("upload 动作缺少 target 或 target_id")
        return Observation.fail(error="upload 动作缺少 target 或 target_id")

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
            title=await self._tool.current_title(),
            data={"message": action.value or "任务完成", "done": True},
        )

    # ── 辅助方法 ────────────────────────────────────────────────────

    def _build_element_map(self, snapshot: Snapshot) -> dict[str, str]:
        """
        从 Snapshot 构建 element_id → selector 的映射。
        供 _resolve_target 通过 target_id 查找使用。
        """
        mapping: dict[str, str] = {}
        for el in snapshot.get_interactive_elements():
            if el.element_id and el.selector:
                mapping[el.element_id] = el.selector
        logger.debug("构建元素映射: {} 个条目", len(mapping))
        return mapping

    def _build_frame_map(self, snapshot: Snapshot) -> dict[str, tuple]:
        """
        从 Snapshot 构建 element_id → frame_path 的映射（V1.0 子计划 A）。
        供元素定位时透传 iframe 链。无 frame_path（主页面）用空元组。
        """
        mapping: dict[str, tuple] = {}
        for el in snapshot.get_interactive_elements():
            if not el.element_id:
                continue
            # 兼容旧版/Mock 元素：frame_path 非元组（如 MagicMock）时回退空元组
            fp = getattr(el, "frame_path", ())
            mapping[el.element_id] = fp if isinstance(fp, tuple) else ()
        return mapping

    def _resolve_frame_path(self, action: Action) -> tuple:
        """
        解析目标元素的 frame_path（V1.0 子计划 A）。

        优先级：
        1. target_id 命中当前 Snapshot → 从 frame_path 映射查询
           （parse_action_dict 会为命中元素注入本地 selector，二者同源；
             若先判 params["selector"] 会把 iframe 元素降级为主页面——待解决问题 #1）
        2. params["selector"] 显式指定 → 主页面（空元组）
        3. 其他 → 主页面（空元组）
        """
        if action.target_id and action.target_id in self._frame_map:
            return self._frame_map[action.target_id]
        if action.params.get("selector"):
            return ()
        return ()

    def _resolve_target(self, action: Action) -> str | None:
        """
        解析目标元素的最终 Playwright 选择器。

        优先级（高 → 低）：
        1. params[\"selector\"] — 显式指定的选择器（最高优先级）
        2. target_id — 从 Snapshot 元素映射中查找（优先级高于 target）
        3. target — 语义化文本，通过 _resolve_selector 转换为选择器

        Returns:
            选择器字符串，或 None（无法解析）
        """
        # 1. params["selector"] 优先级最高
        explicit = action.params.get("selector")
        if explicit:
            return explicit

        # 2. target_id 优先于 target（根据字段说明）
        if action.target_id and action.target_id in self._element_map:
            return self._element_map[action.target_id]

        # 3. target fallback
        if action.target:
            return self._resolve_selector(action.target, action.params)

        return None

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

        # 与 SnapshotGenerator._css_escape_string 保持一致：
        # 先归一化换行/制表符，再转义反斜杠与双引号，避免 CSS 解析错误与转义漂移
        safe = target.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
        safe = safe.replace("\\", "\\\\").replace('"', '\\"')
        return f':has-text("{safe}")'
