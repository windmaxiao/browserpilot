"""
Action Schema

定义 Agent（LLM）输出的动作格式。
LLM 不知道 Playwright，只输出高层级动作。
Playwright 只是执行器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# ── 支持的原子动作类型 ──────────────────────────────────────────────
ActionType = Literal[
    "click",       # 点击元素
    "input",       # 输入文本
    "select",      # 下拉选择
    "goto",        # 导航
    "scroll",      # 滚动
    "wait",        # 等待
    "download",    # 下载文件
    "upload",      # 上传文件
    "back",        # 浏览器返回
    "refresh",     # 刷新页面
    "screenshot",  # 截图
    "done",        # 任务完成
]


@dataclass
class Action:
    """
    LLM 输出的标准动作格式。

    LLM 输出示例：
    ```json
    {
      "action": "click",
      "target": "登录",
      "params": {}
    }
    ```
    """

    action: ActionType
    """动作类型"""

    target: Optional[str] = None
    """
    动作目标元素描述。
    例如："登录按钮"、"搜索框"、"第3行的删除链接"。
    可以是文字、aria-label、位置描述等语义化信息。
    """

    target_id: Optional[str] = None
    """
    目标元素的 element_id（从 Snapshot 获取）。
    设置此字段后应同时设置 params["selector"] 以提供精确定位。
    优先级高于 target。
    """

    value: Optional[str] = None
    """
    动作参数值。
    例如 input 动作的文本内容，select 动作的选项值。
    """

    params: dict[str, Any] = field(default_factory=dict)
    """
    额外参数。
    例如：{"index": 2, "timeout": 5000, "key": "Enter"}
    """

    # ── 验证 ────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """返回验证错误列表，为空表示合法。"""
        errors: list[str] = []

        valid_actions = {
            "click", "input", "select", "goto", "scroll",
            "wait", "download", "upload", "back", "refresh",
            "screenshot", "done",
        }
        if self.action not in valid_actions:
            errors.append(f"未知动作类型: {self.action}")

        if self.action == "goto" and not self.value:
            errors.append("goto 动作需要提供 value (URL)")

        if self.action == "input" and self.value is None:
            errors.append("input 动作需要提供 value (输入文本)")

        # 验证 target_id 格式（如果设置）
        if self.target_id is not None:
            if not self.target_id.startswith("e") or not self.target_id[1:].isdigit():
                errors.append(
                    f"target_id 格式无效: '{self.target_id}'"
                    "（应为 'e' 开头后跟数字，如 e0、e1）"
                )

        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0


# ── 便捷工厂函数 ────────────────────────────────────────────────────

def click(target: str, **kwargs) -> Action:
    return Action(action="click", target=target, **kwargs)


def input_text(target: str, text: str, **kwargs) -> Action:
    return Action(action="input", target=target, value=text, **kwargs)


def goto(url: str, **kwargs) -> Action:
    return Action(action="goto", value=url, **kwargs)


def select(target: str, option: str, **kwargs) -> Action:
    return Action(action="select", target=target, value=option, **kwargs)


def scroll(direction: str = "down", amount: int = 300, **kwargs) -> Action:
    return Action(
        action="scroll",
        params={"direction": direction, "amount": amount, **kwargs},
    )


def wait(ms: int = 1000, **kwargs) -> Action:
    return Action(action="wait", params={"ms": ms, **kwargs})


def done(summary: str = "") -> Action:
    return Action(action="done", value=summary)
