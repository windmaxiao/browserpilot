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
        self._element_counter = 0
        logger.debug("SnapshotGenerator 创建")

    async def generate(self) -> Snapshot:
        """生成当前页面的 Snapshot"""
        logger.info("📄 生成 Snapshot...")
        start = time.time()
        # element_id 仅在单次 Snapshot 内有效，每次生成前重置（V0.2 计划 2.1）
        self._element_counter = 0
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
        self._disambiguate_selectors(result)
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
        self._disambiguate_selectors(result)
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
        self._disambiguate_selectors(result)
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
        self._disambiguate_selectors(result)
        return result

    async def _extract_element_info(
        self, el, index: int
    ) -> ElementInfo | None:
        """从单个元素提取信息"""
        try:
            tag = await el.evaluate("el => el.tagName.toLowerCase()") or ""
            if tag in self.EXCLUDE_TAGS:
                return None

            # 跳过不可见元素（避免 Agent 规划到无法操作的控件）
            try:
                if not await el.is_visible():
                    return None
            except Exception:
                pass

            text = (await el.inner_text()).strip()
            if not text:
                text = (await el.get_attribute("value")) or ""
                text = text.strip()
            if not text:
                text = (await el.get_attribute("aria-label")) or ""

            aria_label = await el.get_attribute("aria-label") or ""

            # 提取重要 HTML 属性到 attributes 字典
            attributes: dict[str, str] = {}
            for attr in ("data-testid", "role", "type", "href", "src", "alt"):
                try:
                    val = await el.get_attribute(attr)
                    if val:
                        attributes[attr] = val
                except Exception:
                    pass

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

            # 分配全局唯一元素 ID
            self._element_counter += 1
            element_id = f"e{self._element_counter}"

            return ElementInfo(
                text=text[:200],
                element_id=element_id,
                tag=tag,
                element_type=self._infer_type(tag),
                selector=selector,
                bbox=bbox,
                aria_label=aria_label,
                attributes=attributes,
                index=index,
            )
        except Exception:
            return None

    async def _build_selector(self, el, tag: str, text: str) -> str:
        """为元素生成 Playwright 选择器。

        稳定性优先（V0.2 计划 2.3）：
        1. data-testid（CSS 转义）
        2. id（CSS 转义）
        3. role
        4. aria-label
        5. 有限长度的标签 + 文本 selector
        6. 标签名兜底

        ID / 属性值一律经 CSS 转义，禁止直接拼接未转义值（Issue 16）。
        """
        testid = ""
        try:
            testid = await el.get_attribute("data-testid") or ""
        except Exception:
            pass
        if testid:
            return f'[data-testid="{self._css_escape_string(testid)}"]'

        el_id = ""
        try:
            el_id = await el.get_attribute("id") or ""
        except Exception:
            pass
        if el_id:
            return f"#{self._css_escape_ident(el_id)}"

        role = ""
        try:
            role = await el.get_attribute("role") or ""
        except Exception:
            pass
        if role:
            return f'[role="{self._css_escape_string(role)}"]'

        aria = ""
        try:
            aria = await el.get_attribute("aria-label") or ""
        except Exception:
            pass
        if aria:
            return f'[aria-label="{self._css_escape_string(aria)}"]'

        if text and tag:
            safe_text = self._css_escape_string(text[:50])
            return f'{tag}:has-text("{safe_text}")'

        return tag

    @staticmethod
    def _css_escape_ident(value: str) -> str:
        """将字符串转义为合法的 CSS 标识符（用于 #id 选择器）。

        仅保留字母/数字/下划线/连字符；其余字符（含首字符数字）以
        \\<hex> 形式转义并加空格终止符，避免 `.` `:` `"` 等被 CSS 误解析。
        """
        if not value:
            return value
        out: list[str] = []
        for i, ch in enumerate(value):
            if i == 0 and ch.isdigit():
                out.append(f"\\{ord(ch):x} ")
            elif ch.isalnum() or ch in ("_", "-"):
                out.append(ch)
            else:
                out.append(f"\\{ord(ch):x} ")
        return "".join(out)

    @staticmethod
    def _css_escape_string(value: str) -> str:
        """将属性值转义为合法的 CSS 字符串字面量（用于 [attr="..."] 选择器）。"""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _disambiguate_selectors(elements: list[ElementInfo]) -> None:
        """保证交互元素定位唯一（V0.2 增强）。

        问题：页面可能出现同文本/同 id 的孪生元素（如百度 AI 版的双「百度一下」按钮），
        其中部分不可见。`_build_selector` 生成的选择器（如 `button:has-text("...")`）
        会同时命中可见与不可见元素，导致 strict mode violation。

        处理：
        1. 统一追加 `:visible` —— 与提取时的 `is_visible()` 过滤保持一致，排除不可见孪生；
        2. 同类目内选择器仍重复时，追加 `>> nth=j` 索引（按 DOM 提取顺序）精确定位。
        """
        counts: dict[str, int] = {}
        for el in elements:
            counts[el.selector] = counts.get(el.selector, 0) + 1

        seen: dict[str, int] = {}
        for el in elements:
            visible_selector = f"{el.selector}:visible"
            if counts[el.selector] > 1:
                j = seen.get(el.selector, 0)
                seen[el.selector] = j + 1
                el.selector = f"{visible_selector} >> nth={j}"
            else:
                el.selector = visible_selector

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
