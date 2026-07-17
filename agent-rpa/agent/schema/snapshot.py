"""
Snapshot Schema

Snapshot 是 Agent 对当前网页的"认知"，而不是 HTML。
它是对页面语义信息的结构化描述，供 Agent 决策使用。

V1 版本：扁平化列表结构。
后续版本会逐步增加 bbox、DOM 树简化版等增强语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ElementInfo:
    """页面元素的语义化描述"""

    text: str
    """元素的可见文本"""

    element_id: str = ""
    """全局唯一元素 ID（由 SnapshotGenerator 自动分配，格式 e0, e1, ...）"""

    tag: str = ""
    """HTML 标签名，如 button、a、input"""

    element_type: str = ""
    """元素类型，如 button、link、textbox、dropdown"""

    selector: str = ""
    """Playwright 选择器（供内部执行使用）"""

    bbox: Optional[dict[str, float]] = None
    """
    元素 bounding box。
    V0.2+ 启用：{"x": 100, "y": 200, "width": 80, "height": 32}
    """

    aria_label: str = ""
    """aria-label 属性"""

    placeholder: str = ""
    """input 的 placeholder"""

    attributes: dict[str, str] = field(default_factory=dict)
    """其他重要属性"""

    index: int = 0
    """在同类元素中的序号（用于区分重复文本）"""


@dataclass
class Snapshot:
    """
    Agent 对当前页面的结构化认知。

    V1 推荐格式：
    ```python
    Snapshot = {
        "title": "",
        "url": "",
        "inputs": [],
        "buttons": [],
        "texts": []
    }
    ```

    未来会逐步增加：
    - dialogs
    - tables
    - loading
    - toast
    - alerts
    - page_type
    """

    title: str = ""
    """页面标题"""

    url: str = ""
    """页面 URL"""

    inputs: list[ElementInfo] = field(default_factory=list)
    """输入框列表"""

    buttons: list[ElementInfo] = field(default_factory=list)
    """可点击按钮列表"""

    links: list[ElementInfo] = field(default_factory=list)
    """链接列表"""

    texts: list[ElementInfo] = field(default_factory=list)
    """页面上重要的文本块"""

    selects: list[ElementInfo] = field(default_factory=list)
    """下拉选择框列表"""

    # ── 未来扩展预留 ────────────────────────────────────────────────

    dialogs: list[dict[str, Any]] = field(default_factory=list)
    """弹窗信息"""

    loading: bool = False
    """页面是否处于加载状态"""

    page_type: str = ""
    """页面类型，如 login、table、form、detail"""

    # ── 快捷方法 ────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        """页面是否没有任何可交互元素"""
        return (
            not self.buttons
            and not self.inputs
            and not self.links
            and not self.selects
        )

    def get_interactive_elements(self) -> list[ElementInfo]:
        """获取所有可交互元素"""
        return self.buttons + self.inputs + self.links + self.selects

    def find(self, text: str) -> list[ElementInfo]:
        """按文本搜索元素"""
        results: list[ElementInfo] = []
        for el in self.get_interactive_elements():
            if text.lower() in el.text.lower():
                results.append(el)
        return results

    def summary(self) -> str:
        """返回页面的文本摘要"""
        parts = [f"标题: {self.title}", f"URL: {self.url}"]
        if self.buttons:
            parts.append(f"按钮: {[b.text for b in self.buttons]}")
        if self.inputs:
            parts.append(f"输入框: {[i.placeholder or i.text for i in self.inputs]}")
        if self.links:
            parts.append(f"链接: {[l.text for l in self.links]}")
        return " | ".join(parts)
