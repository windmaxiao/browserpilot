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

import asyncio
import json
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

    # 单个元素需要读取的 HTML 属性（一次并发 gather 全部取回，避免逐属性 CDP 往返）
    ATTR_KEYS = (
        "data-testid", "id", "role", "type", "href", "src", "alt",
        "aria-label", "value", "placeholder",
    )
    # 写入 ElementInfo.attributes 的属性（与历史字段语义保持一致）
    ATTRIBUTE_FIELDS = ("data-testid", "role", "type", "href", "src", "alt")

    # 各类元素提取选择器（批量 JS 提取与旧逐元素路径共用，单一来源）
    SELECTOR_BUTTONS = (
        "button, [role='button'], input[type='submit'], input[type='button'], "
        "a[class*='btn'], [class*='button']"
    )
    SELECTOR_INPUTS = (
        "input:not([type='hidden']):not([type='submit']):not([type='button']), "
        "textarea, [contenteditable='true'], [role='textbox']"
    )
    SELECTOR_LINKS = "a[href]"
    SELECTOR_TEXTS = "h1, h2, h3, h4, h5, h6, p, span, label, li, td, th, strong, em"
    SELECTOR_SELECTS = "select"

    # 可点击文本元素（V1.0 子计划 A 增强）：p/span/div 等标签带交互属性
    # （onclick / gcode / data-source / data-id / data-code / data-action / role=link|button）
    # 或 cursor:pointer 时视为可点击，纳入交互元素，供 LLM 引用。
    # 覆盖 ERP 类系统用 div/span/p 充当点击入口、不写 a/button 的场景。
    SELECTOR_CLICKABLE = "p, span, div, li, td, label"
    # 已被其他类别提取的可交互元素（嵌套其中不重复提取）
    SELECTOR_CLICKABLE_SKIP = (
        "button, a[href], [role='button'], input, select, textarea, "
        "[contenteditable='true'], [role='textbox']"
    )
    _CLICKABLE_MAX = 50  # 每帧可点击文本数量上限，防噪音爆炸

    # 批量 JS 提取函数：每个 frame scope 一次 evaluate 返回全部 5 类原始数据，
    # 替代「逐元素 15 次 CDP 往返」（待解决问题 #9，大页面 501s → 数秒）。
    _EXTRACT_JS = (
        "() => {"
        " const extract = (el) => {"
        "  try {"
        "   const rect = el.getBoundingClientRect();"
        "   const cs = getComputedStyle(el);"
        "   const visible = rect.width > 0 && rect.height > 0"
        "    && cs.visibility !== 'hidden' && cs.display !== 'none';"
        "   return {"
        "    tag: el.tagName.toLowerCase(),"
        "    text: (el.innerText || '').trim(),"
        "    ariaLabel: el.getAttribute('aria-label') || '',"
        "    visible,"
        "    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },"
        "    attrs: {"
        "     'data-testid': el.getAttribute('data-testid') || '',"
        "     id: el.id || '',"
        "     role: el.getAttribute('role') || '',"
        "     type: el.getAttribute('type') || '',"
        "     href: el.getAttribute('href') || '',"
        "     src: el.getAttribute('src') || '',"
        "     alt: el.getAttribute('alt') || '',"
        "     value: el.getAttribute('value') || '',"
        "     placeholder: el.getAttribute('placeholder') || ''"
        "    }"
        "   };"
        "  } catch (err) { return null; }"
        " };"
        " const one = (sel) => Array.from(document.querySelectorAll(sel))"
        "  .map((el) => extract(el)).filter(Boolean);"
        " const CLICKABLE_ATTRS = ['onclick','gcode','data-source','data-id',"
        "   'data-code','data-action'];"
        " const clickables = Array.from(document.querySelectorAll("
        + json.dumps(SELECTOR_CLICKABLE) + ")).filter((el) => {"
        "  if (el.closest(" + json.dumps(SELECTOR_CLICKABLE_SKIP) + ")) return false;"
        "  const role = el.getAttribute('role');"
        "  if (role === 'link' || role === 'button') return true;"
        "  if (CLICKABLE_ATTRS.some((k) => el.getAttribute(k))) return true;"
        "  const tag = el.tagName.toLowerCase();"
        "  if (tag === 'div' || tag === 'td') return false;"
        "  return getComputedStyle(el).cursor === 'pointer';"
        " }).slice(0, " + str(_CLICKABLE_MAX) + ").map((el) => extract(el)).filter(Boolean);"
        " return {"
        "  buttons: one(" + json.dumps(SELECTOR_BUTTONS) + "),"
        "  inputs: one(" + json.dumps(SELECTOR_INPUTS) + "),"
        "  links: one(" + json.dumps(SELECTOR_LINKS) + "),"
        "  texts: one(" + json.dumps(SELECTOR_TEXTS) + "),"
        "  selects: one(" + json.dumps(SELECTOR_SELECTS) + "),"
        "  clickables: clickables"
        " };"
        "}"
    )

    def __init__(self, page: Page, frame_load_timeout: int = 2000):
        self._page = page
        self._element_counter = 0
        # V1.0 #5：子 frame 加载等待超时（毫秒），延迟加载帧超时即跳过
        # 深嵌套 iframe（如多层 SAP）加载慢时可调大，避免观察时漏帧
        self._frame_load_timeout = frame_load_timeout
        logger.debug("SnapshotGenerator 创建")

    def set_page(self, page: Page) -> None:
        """更新当前页面引用（点击新标签页自动跟随后由 BrowserManager 回调）。"""
        self._page = page
        self._element_counter = 0
        logger.debug("SnapshotGenerator 页面已切换 | URL: {}", page.url)

    async def generate(self) -> Snapshot:
        """生成当前页面的 Snapshot（V1.0 子计划 A：递归 iframe）"""
        logger.info("📄 生成 Snapshot...")
        start = time.time()
        # element_id 仅在单次 Snapshot 内有效，每次生成前重置（V0.2 计划 2.1）
        self._element_counter = 0
        try:
            title, url = await self._main_title_url()
            # 遍历所有 frame scope（主页面 + 嵌套 iframe），每层各提取元素
            t_scope = time.time()
            scopes = await self._iter_scopes()
            logger.info(
                "Snapshot 阶段: 帧遍历 | {} 个 scope | {:.2f}s",
                len(scopes), time.time() - t_scope,
            )
            buttons, inputs, links, texts, selects = [], [], [], [], []
            t_extract = time.time()
            for scope, frame_path in scopes:
                t_frame = time.time()
                b, i, l, tx, s = await self._extract_scope(scope, frame_path)
                buttons += b
                inputs += i
                links += l
                texts += tx
                selects += s
                logger.debug(
                    "Snapshot 提取帧 {} | {:.2f}s",
                    frame_path or "(主页面)", time.time() - t_frame,
                )
            logger.info(
                "Snapshot 阶段: 元素提取 | {:.2f}s",
                time.time() - t_extract,
            )
            loading = await self._is_loading()
        except Exception as e:
            # 页面已被关闭（用户手动关闭 / 弹窗跳转）时不崩溃，返回空 Snapshot。
            # 上层可通过 loading=True + 空元素识别，并交由 Agent 循环检测优雅收尾。
            logger.warning("⚠️ Snapshot 生成失败（页面可能已关闭）: {}", e)
            return Snapshot(title="", url="", loading=True)

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

    # ── iframe scope 遍历（V1.0 子计划 A）───────────────────────────

    async def _main_title_url(self) -> tuple[str, str]:
        """返回主页面 title / url（跨 frame 遍历时依旧以主页面为准）。"""
        title = await self._page.title()
        return title, self._page.url

    async def _iter_scopes(self) -> list[tuple]:
        """遍历所有 element scope，返回 [(scope, frame_path), ...]。

        - 主页面 frame_path=()；
        - 对应用 Mock Page（非真实 Playwright Page）时仅返回主页面，保持向后兼容。
        """
        from playwright.async_api import Page as _Page
        if not isinstance(self._page, _Page):
            return [(self._page, ())]
        scopes: list[tuple] = []
        await self._walk_scopes(self._page.main_frame, (), scopes)
        return scopes

    async def _walk_scopes(
        self, frame, frame_path: tuple, scopes: list
    ) -> None:
        """递归遍历 frame 树：进入每个 iframe 与其可见元素 scope。"""
        scopes.append((frame, frame_path))
        try:
            iframe_els = await frame.query_selector_all("iframe, frame, object")
        except Exception:
            return
        if not iframe_els:
            return
        segments = await self._frame_segments(iframe_els)
        for el, seg in zip(iframe_els, segments):
            try:
                child = await el.content_frame()
            except Exception:
                child = None
            if child is None:
                continue  # iframe 尚未挂载
            # V1.0 #5：对子 frame 做有限超时加载等待，延迟加载的空帧跳过，
            # 不使全局 Snapshot 失败（仅记录告警）。
            # 用 asyncio.wait_for 兜底：个别页面帧在 Playwright 内部可能不按
            # timeout 返回（持续导航/长轮询帧），确保每帧等待不超过配置值，
            # 避免 Snapshot 卡死。
            t_wait = time.time()
            try:
                await asyncio.wait_for(
                    child.wait_for_load_state("load"),
                    timeout=self._frame_load_timeout / 1000,
                )
            except Exception as e:
                logger.warning(
                    "iframe 加载等待超时/失败，跳过该帧 | path={} | 等待 {:.2f}s | err={}",
                    frame_path + (seg,), time.time() - t_wait, e,
                )
                continue
            logger.debug(
                "iframe 加载完成 | path={} | 等待 {:.2f}s",
                frame_path + (seg,), time.time() - t_wait,
            )
            await self._walk_scopes(child, frame_path + (seg,), scopes)

    async def _frame_segments(self, iframe_els: list) -> list[str]:
        """为同一父 document 内的 iframe 元素生成唯一定位段。

        优先级：id → name → 父内位置（`iframe >> nth=j`，按 DOM 顺序消歧）。
        与 ElementInfo.selector 契约一致：每段都是父 document 内可定位该 iframe
        元素的选择器。重复的 id/name 段（含第一个）一律改用位置索引——
        非唯一段在 FrameLocator 严格模式下无法确定目标（待解决问题 #2）。
        """
        base = []
        for el in iframe_els:
            el_id = await self._frame_attr(el, "id")
            if el_id:
                base.append(f"#{self._css_escape_ident(el_id)}")
                continue
            name = await self._frame_attr(el, "name")
            if name:
                base.append(f'iframe[name="{self._css_escape_string(name)}"]')
                continue
            base.append(None)  # 交由位置消歧
        # 统计语义段出现次数：任何重复的 id/name 段都改用位置索引
        counts: dict[str, int] = {}
        for seg in base:
            if seg is not None:
                counts[seg] = counts.get(seg, 0) + 1
        result = []
        for j, seg in enumerate(base):
            if seg is not None and counts[seg] == 1:
                result.append(seg)
            else:
                result.append(f"iframe >> nth={j}")
        return result

    @staticmethod
    async def _frame_attr(el, key: str) -> str:
        try:
            return (await el.get_attribute(key)) or ""
        except Exception:
            return ""

    # ── 提取方法（支持 scope + frame_path）──────────────────────────

    async def _extract_buttons(self, scope, frame_path=()) -> list[ElementInfo]:
        """提取所有可点击按钮"""
        elements = await scope.query_selector_all(self.SELECTOR_BUTTONS)
        infos = await asyncio.gather(
            *(self._extract_element_info(el, i, frame_path) for i, el in enumerate(elements))
        )
        result = [info for info in infos if info and info.text.strip()]
        self._disambiguate_selectors(result)
        return result

    async def _extract_inputs(self, scope, frame_path=()) -> list[ElementInfo]:
        """提取所有输入框"""
        elements = await scope.query_selector_all(self.SELECTOR_INPUTS)
        infos = await asyncio.gather(
            *(self._extract_element_info(el, i, frame_path) for i, el in enumerate(elements))
        )
        result = [info for info in infos if info]
        self._disambiguate_selectors(result)
        return result

    async def _extract_links(self, scope, frame_path=()) -> list[ElementInfo]:
        """提取所有链接"""
        elements = await scope.query_selector_all(self.SELECTOR_LINKS)
        infos = await asyncio.gather(
            *(self._extract_element_info(el, i, frame_path) for i, el in enumerate(elements))
        )
        result = [info for info in infos if info and info.text.strip()]
        self._disambiguate_selectors(result)
        return result

    async def _extract_texts(self, scope, frame_path=()) -> list[ElementInfo]:
        """提取页面上重要的文本块"""
        elements = await scope.query_selector_all(self.SELECTOR_TEXTS)
        infos = await asyncio.gather(
            *(self._extract_element_info(el, i, frame_path) for i, el in enumerate(elements))
        )
        return [info for info in infos if info and info.text.strip()]

    async def _extract_selects(self, scope, frame_path=()) -> list[ElementInfo]:
        """提取下拉选择框"""
        elements = await scope.query_selector_all(self.SELECTOR_SELECTS)
        infos = await asyncio.gather(
            *(self._extract_element_info(el, i, frame_path) for i, el in enumerate(elements))
        )
        result = [info for info in infos if info]
        self._disambiguate_selectors(result)
        return result

    async def _extract_scope(
        self, scope, frame_path: tuple = ()
    ) -> tuple[list, list, list, list, list]:
        """提取单个 scope（主页面或某层 iframe）内全部元素。

        真实 Playwright Frame/Page 走一次 `frame.evaluate` 的批量 JSON 提取，
        替代逐元素 CDP 调用（待解决问题 #9）；Mock 对象回退旧逐元素路径，保持兼容。
        """
        from playwright.async_api import Frame as _Frame, Page as _Page
        if not isinstance(scope, (_Page, _Frame)):
            return (
                await self._extract_buttons(scope, frame_path),
                await self._extract_inputs(scope, frame_path),
                await self._extract_links(scope, frame_path),
                await self._extract_texts(scope, frame_path),
                await self._extract_selects(scope, frame_path),
            )
        try:
            data = await scope.evaluate(self._EXTRACT_JS) or {}
        except Exception as e:
            logger.warning("批量提取失败，该帧返回空 | path={} | err={}", frame_path, e)
            data = {}
        # clickables（带交互属性的 p/span/div）合并到 buttons 作为可交互元素，供 LLM 点击
        buttons = self._build_infos(
            data.get("buttons", []) + data.get("clickables", []),
            frame_path, require_text=True, disambiguate=True,
        )
        return (
            buttons,
            self._build_infos(data.get("inputs", []), frame_path, require_text=False, disambiguate=True),
            self._build_infos(data.get("links", []), frame_path, require_text=True, disambiguate=True),
            self._build_infos(data.get("texts", []), frame_path, require_text=True, disambiguate=False),
            self._build_infos(data.get("selects", []), frame_path, require_text=False, disambiguate=True),
        )

    def _build_infos(
        self, raw_list: list, frame_path: tuple, *,
        require_text: bool, disambiguate: bool,
    ) -> list[ElementInfo]:
        """把批量 JS 返回的原始字典列表转换为 ElementInfo 列表。"""
        infos: list[ElementInfo] = []
        for i, raw in enumerate(raw_list):
            info = self._from_raw(raw, i, frame_path)
            if info is None:
                continue
            if require_text and not info.text.strip():
                continue
            infos.append(info)
        if disambiguate:
            self._disambiguate_selectors(infos)
        return infos

    def _from_raw(
        self, raw: dict, index: int, frame_path: tuple
    ) -> ElementInfo | None:
        """把批量 JS 提取的单条原始数据转换为 ElementInfo（逻辑与逐元素路径一致）。"""
        tag = (raw.get("tag") or "").lower()
        if tag in self.EXCLUDE_TAGS:
            return None
        if not raw.get("visible"):
            return None

        attrs = raw.get("attrs") or {}
        aria_label = raw.get("ariaLabel") or ""
        text = (raw.get("text") or "").strip()
        if not text:
            text = (attrs.get("value") or "").strip()
        if not text:
            text = aria_label.strip()

        selector = self._build_selector_from(tag, text, attrs, aria_label)

        rect = raw.get("rect") or {}
        bbox = None
        if rect.get("width") is not None and rect.get("height") is not None:
            bbox = {
                "x": rect.get("x", 0), "y": rect.get("y", 0),
                "width": rect["width"], "height": rect["height"],
            }

        self._element_counter += 1
        return ElementInfo(
            text=text[:200],
            element_id=f"e{self._element_counter}",
            tag=tag,
            element_type=self._infer_type(tag),
            selector=selector,
            bbox=bbox,
            aria_label=aria_label,
            placeholder=attrs.get("placeholder", ""),
            attributes={a: attrs[a] for a in self.ATTRIBUTE_FIELDS if attrs.get(a)},
            index=index,
            frame_path=frame_path,
        )

    def _build_selector_from(
        self, tag: str, text: str, attributes: dict, aria_label: str = ""
    ) -> str:
        """纯函数版选择器生成：数据已由批量 JS 取回，无需再发 CDP。

        优先级与 `_build_selector` 完全一致（见其 docstring）。
        """
        attributes = attributes or {}
        testid = attributes.get("data-testid", "")
        if testid:
            return f'[data-testid="{self._css_escape_string(testid)}"]'
        el_id = attributes.get("id", "")
        if el_id:
            return f"#{self._css_escape_ident(el_id)}"
        role = attributes.get("role", "")
        if role:
            return f'[role="{self._css_escape_string(role)}"]'
        if aria_label:
            return f'[aria-label="{self._css_escape_string(aria_label)}"]'
        if text and tag:
            return f'{tag}:has-text("{self._css_escape_string(text[:50])}")'
        return tag

    async def _extract_element_info(
        self, el, index: int, frame_path: tuple = ()
    ) -> ElementInfo | None:
        """从单个元素提取信息。

        性能：元素自身的全部 CDP 读取（tag/可见性/文本/属性/bbox）一次并发 gather
        取回，避免逐属性串行往返（页面元素多时耗时从秒级降到亚秒级）。
        """
        try:
            tag, visible, text, aria_label, bbox = await asyncio.gather(
                el.evaluate("el => el.tagName.toLowerCase()"),
                el.is_visible(),
                el.inner_text(),
                el.get_attribute("aria-label"),
                el.bounding_box(),
            )
            attr_values = await asyncio.gather(
                *[el.get_attribute(attr) for attr in self.ATTR_KEYS]
            )
        except Exception:
            return None

        tag = tag or ""
        if tag in self.EXCLUDE_TAGS:
            return None
        # 跳过不可见元素（避免 Agent 规划到无法操作的控件）
        if not visible:
            return None

        all_attrs = {
            attr: (val or "") for attr, val in zip(self.ATTR_KEYS, attr_values)
        }
        text = (text or "").strip()
        if not text:
            text = all_attrs.get("value", "").strip()
        if not text:
            text = (aria_label or "").strip()
        aria_label = aria_label or ""
        attributes = {
            attr: all_attrs[attr]
            for attr in self.ATTRIBUTE_FIELDS
            if all_attrs.get(attr)
        }

        # 生成选择器（属性已取回，无需再发起 CDP 查询）
        selector = await self._build_selector(el, tag, text, all_attrs)

        # bounding box（V0.2+ 启用）
        bbox_dict = None
        if bbox:
            bbox_dict = {"x": bbox["x"], "y": bbox["y"],
                         "width": bbox["width"], "height": bbox["height"]}

        # 分配全局唯一元素 ID
        self._element_counter += 1
        element_id = f"e{self._element_counter}"

        return ElementInfo(
            text=text[:200],
            element_id=element_id,
            tag=tag,
            element_type=self._infer_type(tag),
            selector=selector,
            bbox=bbox_dict,
            aria_label=aria_label,
            placeholder=all_attrs.get("placeholder", ""),
            attributes=attributes,
            index=index,
            frame_path=frame_path,
        )

    async def _build_selector(
        self, el, tag: str, text: str, attributes: dict | None = None
    ) -> str:
        """为元素生成 Playwright 选择器。

        稳定性优先（V0.2 计划 2.3）：
        1. data-testid（CSS 转义）
        2. id（CSS 转义）
        3. role
        4. aria-label
        5. 有限长度的标签 + 文本 selector
        6. 标签名兜底

        ID / 属性值一律经 CSS 转义，禁止直接拼接未转义值（Issue 16）。

        ``attributes`` 为已取回的属性字典（避免额外 CDP 往返）；
        不传时回退为直接查询元素属性（保持旧签名兼容）。
        """
        async def _get(key: str) -> str:
            if attributes is not None:
                return attributes.get(key, "")
            try:
                return (await el.get_attribute(key)) or ""
            except Exception:
                return ""

        testid = await _get("data-testid")
        if testid:
            return f'[data-testid="{self._css_escape_string(testid)}"]'

        el_id = await _get("id")
        if el_id:
            return f"#{self._css_escape_ident(el_id)}"

        role = await _get("role")
        if role:
            return f'[role="{self._css_escape_string(role)}"]'

        aria = await _get("aria-label")
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
        """将属性值转义为合法的 CSS 字符串字面量（用于 [attr="..."] 选择器）。

        换行/制表符在 CSS 字符串字面量中非法（裸换行直接导致解析失败），
        统一替换为空格；Playwright 的 :has-text 匹配本身会做空白归一化，
        因此语义不受影响。
        """
        value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
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
        if any(k in url or k in title for k in ("detail", "详情")):
            return "detail"
        return "unknown"
