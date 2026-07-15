"""
Observation Schema

所有 Browser Tool 都返回 Observation。
Observation 是 Agent 感知页面变化和判断执行结果的依据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Observation:
    """
    Browser Tool 执行后的返回结果。

    示例：
    ```python
    {
        "success": True,
        "url": "https://example.com/dashboard",
        "title": "控制台",
        "page_changed": True,
        "error": None,
    }
    ```
    """

    success: bool = True
    """动作是否执行成功"""

    url: Optional[str] = None
    """执行后的页面 URL"""

    title: Optional[str] = None
    """执行后的页面标题"""

    page_changed: bool = False
    """页面是否发生了变化（跳转/弹窗/内容更新等）"""

    error: Optional[str] = None
    """错误信息，success=False 时必填"""

    data: dict[str, Any] = field(default_factory=dict)
    """附加数据，例如下载路径、截图 base64 等"""

    # ── 快捷属性 ────────────────────────────────────────────────────

    @property
    def is_error(self) -> bool:
        return not self.success

    @property
    def has_error(self) -> bool:
        return self.error is not None

    # ── 工厂方法 ────────────────────────────────────────────────────

    @classmethod
    def ok(
        cls,
        url: Optional[str] = None,
        title: Optional[str] = None,
        page_changed: bool = False,
        **data,
    ) -> Observation:
        return cls(
            success=True,
            url=url,
            title=title,
            page_changed=page_changed,
            data=data,
        )

    @classmethod
    def fail(cls, error: str, url: Optional[str] = None, **data) -> Observation:
        return cls(
            success=False,
            url=url,
            error=error,
            data=data,
        )
