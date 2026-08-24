"""
Action Schema

定义 Agent（LLM）输出的动作格式。
LLM 不知道 Playwright，只输出高层级动作。
Playwright 只是执行器。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, get_args


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

# 由 ActionType 派生，避免双份维护（待解决问题 #25）
VALID_ACTIONS = frozenset(get_args(ActionType))

# 需要目标元素定位的动作：target / target_id / params["selector"] 三选一
NEEDS_TARGET = frozenset({"click", "select", "download", "upload", "input"})


# ── 参数校验辅助函数 ────────────────────────────────────────────────

def is_path_within_allowed(value: str, allowed_dirs) -> bool:
    """M5 文件路径越权防护：value 解析为绝对路径后是否位于任一允许目录内。

    未配置 allowed_dirs（None / 空）一律返回 False —— 未显式允许的路径拒绝访问。
    """
    if not allowed_dirs:
        return False
    target = Path(os.path.abspath(os.path.expanduser(str(value))))
    for d in allowed_dirs:
        try:
            target.relative_to(Path(os.path.abspath(os.path.expanduser(str(d)))))
            return True
        except ValueError:
            continue
    return False


def _check_positive_int(params: dict, key: str, errors: list[str]) -> None:
    """校验正整数参数（如 timeout）。未设置时跳过。"""
    val = params.get(key)
    if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val <= 0):
        errors.append(f"参数非法: {key}={val!r}（应为正整数）")


def _check_non_negative_int(params: dict, key: str, errors: list[str]) -> None:
    """校验非负整数参数（如 wait.ms / scroll.amount）。未设置时跳过。"""
    val = params.get(key)
    if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val < 0):
        errors.append(f"参数非法: {key}={val!r}（应为非负整数）")


def _check_bool(params: dict, key: str, errors: list[str]) -> None:
    """校验布尔参数（如 force / clear_first / full_page）。未设置时跳过。"""
    val = params.get(key)
    if val is not None and not isinstance(val, bool):
        errors.append(f"参数非法: {key}={val!r}（应为布尔值）")


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
    用于精确定位，优先级高于 target。
    设置此字段后无需再设置 target 或 params["selector"]，
    Executor 会自动从 Snapshot 映射中查找选择器。
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
        """返回验证错误列表，为空表示合法。

        参数契约见 V0.2 开发计划 2.2：
        - click: timeout 正整数, force bool
        - input: value 可为空字符串（清空用）, clear_first bool, timeout 正整数
        - select/download/upload/goto: timeout 正整数；select/upload 需 value
        - scroll: direction 枚举, amount 非负整数
        - wait: ms 非负整数
        - screenshot: full_page bool
        - 需要元素的动作: target / target_id / params["selector"] 三选一
        """
        errors: list[str] = []

        if self.action not in VALID_ACTIONS:
            errors.append(f"未知动作类型: {self.action}")
            return errors

        # ── 必填 value 校验 ──
        if self.action == "goto" and not self.value:
            errors.append("goto 动作需要提供 value (URL)")
        if self.action == "input" and self.value is None:
            errors.append("input 动作需要提供 value (输入文本)")
        if self.action == "select" and not self.value:
            errors.append("select 动作需要提供 value (选项值)")
        if self.action == "upload" and not self.value:
            errors.append("upload 动作需要提供 value (文件路径)")

        # 需要目标元素的动作：必须提供 target / target_id / params["selector"]
        if self.action in NEEDS_TARGET and not (
            self.target or self.target_id or self.params.get("selector")
        ):
            errors.append(
                f"{self.action} 动作需要提供 target、target_id 或 params['selector']"
            )

        # 验证 target_id 格式（如果设置）
        if self.target_id is not None:
            if not self.target_id.startswith("e") or not self.target_id[1:].isdigit():
                errors.append(
                    f"target_id 格式无效: '{self.target_id}'"
                    "（应为 'e' 开头后跟数字，如 e0、e1）"
                )

        # ── 参数类型 / 范围校验 ──
        if self.action == "click":
            _check_positive_int(self.params, "timeout", errors)
            _check_bool(self.params, "force", errors)
        elif self.action == "input":
            _check_bool(self.params, "clear_first", errors)
            _check_positive_int(self.params, "timeout", errors)
        elif self.action in ("select", "goto", "download", "upload"):
            _check_positive_int(self.params, "timeout", errors)
        elif self.action == "scroll":
            direction = self.params.get("direction", "down")
            if direction not in ("down", "up", "top", "bottom"):
                errors.append(
                    f"scroll 参数非法: direction={direction!r}"
                    "（应为 down/up/top/bottom）"
                )
            _check_non_negative_int(self.params, "amount", errors)
        elif self.action == "wait":
            _check_non_negative_int(self.params, "ms", errors)
        elif self.action == "screenshot":
            _check_bool(self.params, "full_page", errors)

        if self.action == "download":
            save_path = self.params.get("save_path")
            if save_path is not None and not isinstance(save_path, (str, Path)):
                errors.append(
                    f"download 参数非法: save_path={save_path!r}"
                    "（应为字符串或 Path）"
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


def scroll(direction: str = "down", amount: int = 300) -> Action:
    return Action(
        action="scroll",
        params={"direction": direction, "amount": amount},
    )


def wait(ms: int = 1000) -> Action:
    return Action(action="wait", params={"ms": ms})


def done(summary: str = "") -> Action:
    return Action(action="done", value=summary)
