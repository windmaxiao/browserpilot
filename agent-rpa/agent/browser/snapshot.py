"""
Snapshot 生成器

负责从 Playwright Page 提取页面语义信息，生成 Snapshot。
Snapshot 是 Agent 对网页的"认知"，不是 HTML。

设计原则：
- 只读，不修改页面
- 提取语义信息，而非原始 DOM
- 为 Agent 提供可理解的决策依据
"""

from __future__ import annotations

import time

from loguru import logger
from playwright.async_api import Page

from agent.schema.snapshot import ElementInfo, Snapshot


class SnapshotGenerator:
    """
    从 Playwright Page 生成 Snapshot。
    """

    # 需要排除的隐藏或无意义标签
    EXCLUDE_TAGS = {"script", "style", "noscript", "svg", "path", "meta", "link"}

    def __init__(self, page: Page):
        self._page = page
        logger.debug("SnapshotGenerator 创建")

    async def generate(self) -> Snapshot:
        """生成当前页面的 Snapshot"""
        logger.info("📄 生成 Snapshot...")
        start = time.time()
        title = await self._page.title()
        url = self._page.url

        # 并行提取各类元素
        buttons = await self._extract_buttons()
        inputs = await self._extract_inputs()
        links = await self._extract_links()
        texts = await self._extract_texts()
        selects = await self._extract_selects()
        loading = await self._is_loading()

        elapsed = time.time() - start
        logger.info(
            "Snapshot 生成完成 | 按钮={} 输入框={} 链接={} 文本块={} 下拉={} | 加载={} | {:.1f}s",
            len(buttons), len(inputs), len(links), len(texts), len(selects),
            loading, elapsed,
        )

        return Snapshot(
            title=title,
            url=url,
            buttons=buttons,
            inputs=inputs,
            links=links,
            texts=texts,
            selects=selects,
            loading=loading,
        )

    async def _extract_buttons(self) -> list[ElementInfo]:
        """提取所有可点击按钮"""
        elements = await self._page.query_selector_all(
            "button, [role='button'], input[type='submit'], input[type='button'], "
            "a[class*='btn'], [class*='button']"
        )
        result: list[ElementInfo] = []
        for i, el in enumerate(elements):
            info = await self._extract_element_info(el, i)
            if info and info.text.strip():
                result.append(info)
        return result

    async def _extract_inputs(self) -> list[ElementInfo]:
        """提取所有输入框"""
        elements = await self._page.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "textarea, [contenteditable='true'], [role='textbox']"
        )
        result: list[ElementInfo] = []
        for i, el in enumerate(elements):
            info = await self._extract_element_info(el, i)
            if info:
                try:
                    info.placeholder = await el.get_attribute("placeholder") or ""
                except Exception:
                    pass
                result.append(info)
        return result

    async def _extract_links(self) -> list[ElementInfo]:
        """提取所有链接"""
        elements = await self._page.query_selector_all("a[href]")
        result: list[ElementInfo] = []
        for i, el in enumerate(elements):
            info = await self._extract_element_info(el, i)
            if info and info.text.strip():
                href = ""
                try:
                    href = await el.get_attribute("href") or ""
                except Exception:
                    pass
                info.attributes["href"] = href
                result.append(info)
        return result

    async def _extract_texts(self) -> list[ElementInfo]:
        """提取页面上重要的文本块"""
        elements = await self._page.query_selector_all(
            "h1, h2, h3, h4, h5, h6, p, span, label, li, td, th, strong, em"
        )
        result: list[ElementInfo] = []
        for i, el in enumerate(elements):
            info = await self._extract_element_info(el, i)
            if info and info.text.strip():
                result.append(info)
        return result

    async def _extract_selects(self) -> list[ElementInfo]:
        """提取下拉选择框"""
        elements = await self._page.query_selector_all("select")
        result: list[ElementInfo] = []
        for i, el in enumerate(elements):
            info = await self._extract_element_info(el, i)
            if info:
                result.append(info)
        return result

    async def _extract_element_info(
        self, el, index: int
    ) -> ElementInfo | None:
        """从单个元素提取信息"""
        try:
            tag = await el.evaluate("el => el.tagName.toLowerCase()") or ""
            if tag in self.EXCLUDE_TAGS:
                return None

            text = (await el.inner_text()).strip()
            if not text:
                text = (await el.get_attribute("value")) or ""
                text = text.strip()
            if not text:
                text = (await el.get_attribute("aria-label")) or ""

            aria_label = await el.get_attribute("aria-label") or ""

            # 生成选择器
            selector = await self._build_selector(el, tag, text)

            # bounding box（V0.2+ 启用）
            bbox = None
            try:
                box = await el.bounding_box()
                if box:
                    bbox = {"x": box["x"], "y": box["y"],
                            "width": box["width"], "height": box["height"]}
            except Exception:
                pass

            return ElementInfo(
                text=text[:200],
                tag=tag,
                element_type=self._infer_type(tag),
                selector=selector,
                bbox=bbox,
                aria_label=aria_label,
                index=index,
            )
        except Exception:
            return None

    async def _build_selector(self, el, tag: str, text: str) -> str:
        """为元素生成 Playwright 选择器"""
        if text and tag in ("button", "a", "label"):
            safe_text = text.replace('"', '\\"')
            return f'{tag}:has-text("{safe_text}")'

        el_id = ""
        try:
            el_id = await el.get_attribute("id") or ""
        except Exception:
            pass
        if el_id:
            return f"#{el_id}"

        testid = ""
        try:
            testid = await el.get_attribute("data-testid") or ""
        except Exception:
            pass
        if testid:
            return f'[data-testid="{testid}"]'

        role = ""
        try:
            role = await el.get_attribute("role") or ""
        except Exception:
            pass
        if role:
            return f'[role="{role}"]'

        aria = ""
        try:
            aria = await el.get_attribute("aria-label") or ""
        except Exception:
            pass
        if aria:
            return f'[aria-label="{aria}"]'

        if text:
            safe_text = text.replace('"', '\\"')
            return f'{tag}:has-text("{safe_text[:50]}")'

        return tag

    def _infer_type(self, tag: str) -> str:
        """从标签名推断元素类型"""
        type_map = {
            "button": "button",
            "a": "link",
            "input": "textbox",
            "textarea": "textbox",
            "select": "dropdown",
            "img": "image",
        }
        return type_map.get(tag, "text")

    async def _is_loading(self) -> bool:
        """判断页面是否处于加载状态"""
        try:
            state = await self._page.evaluate("document.readyState")
            return state != "complete"
        except Exception:
            return True

    async def detect_page_type(self) -> str:
        """尝试推断页面类型"""
        url = self._page.url.lower()
        title = (await self._page.title()).lower()

        if any(k in url or k in title for k in ("login", "signin", "登录")):
            return "login"
        if any(k in url or k in title for k in ("search", "query", "搜索", "查询")):
            return "search"
        if any(k in url or k in title for k in ("table", "list", "列表", "报表")):
            return "table"
        if any(k in url or k in title for k in ("form", "edit", "create", "表单")):
            return "form"
        if any(k in url or k in title for k in ("detail", "detail", "详情")):
            return "detail"
        return "unknown"
