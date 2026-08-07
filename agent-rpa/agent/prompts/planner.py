"""
Prompt 相关纯函数（V0.3 阶段 B）

Snapshot → LLM 上下文序列化、URL 脱敏、element_id 映射、历史窗口摘要。

设计约束：
- 本模块不依赖网络、不依赖 Playwright，只做纯数据转换。
- 默认只输出可交互元素与有限元信息，不输出原始 selector、完整 href、
  页面 HTML、截图 base64 或 Cookie。
- 单个字段与元素总量均有截断上限，截断信息显式标记。
- 模型通过 element_id 引用元素；selector 只由本地 Snapshot 映射产生。
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from agent.schema.action import Action, NEEDS_TARGET, VALID_ACTIONS
from agent.schema.snapshot import ElementInfo, Snapshot


# ── 常量与默认限制 ──────────────────────────────────────────────────

# 可安全暴露给模型的属性白名单（selector / href / 敏感属性一律不输出）
_SAFE_ATTRIBUTE_KEYS = ("data-testid", "name", "type", "role")

# URL 查询参数中疑似敏感的关键字（值会被掩码为 ***）
_SENSITIVE_QUERY_PARAMS = frozenset({
    "token", "access_token", "refresh_token", "session", "sessionid",
    "code", "authorization", "auth", "key", "api_key", "apikey",
    "secret", "password",
})

_DEFAULT_MAX_ELEMENTS = 50
_DEFAULT_MAX_TEXT_LENGTH = 200
_DEFAULT_MAX_HISTORY = 5

_TRUNCATED_MARK = "…(truncated)"


# ── 工具函数 ────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    """截断文本；超过上限时附加省略标记，避免模型误以为内容完整。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATED_MARK


def _mask_query(query: str) -> str:
    """将查询串中敏感参数的值替换为 ***。"""
    if not query:
        return ""
    masked: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            key, _, value = pair.partition("=")
            if key.lower() in _SENSITIVE_QUERY_PARAMS:
                value = "***"
            masked.append(f"{key}={value}")
        else:
            masked.append(pair)
    return "&".join(masked)


def sanitize_url(url: str) -> str:
    """脱敏 URL：移除 fragment，并将疑似 token/session/code 等查询参数值掩码为 ***。

    解析失败时原样返回，保证序列化流程不被异常中断。
    """
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       _mask_query(parsed.query), ""))


# ── Snapshot 序列化 ─────────────────────────────────────────────────

def serialize_element(
    element: ElementInfo,
    *,
    max_text_length: int = _DEFAULT_MAX_TEXT_LENGTH,
) -> dict[str, Any]:
    """单个元素 → 模型视图。

    只暴露 id / kind / text / label / placeholder 与白名单属性；
    不输出 selector、bbox、完整 href 或 HTML。
    """
    attributes = {
        k: v for k, v in element.attributes.items() if k in _SAFE_ATTRIBUTE_KEYS
    }
    return {
        "id": element.element_id,
        "kind": element.element_type or element.tag or "element",
        "text": _truncate(element.text, max_text_length),
        "label": element.aria_label,
        "placeholder": element.placeholder,
        "attributes": attributes,
    }


def serialize_snapshot(
    snapshot: Snapshot,
    *,
    max_elements: int = _DEFAULT_MAX_ELEMENTS,
    max_text_length: int = _DEFAULT_MAX_TEXT_LENGTH,
    max_text_elements: int = 10,
) -> dict[str, Any]:
    """Snapshot → LLM 上下文（紧凑 dict，不含整页 HTML）。

    - 只包含可交互元素与有限页面元信息。
    - 元素数量超过 ``max_elements`` 时截断并显式标记 ``elements_truncated``。
    - URL 经 :func:`sanitize_url` 脱敏。
    - 附带正文摘要（h1-p 等文本，前 ``max_text_elements`` 条，同样截断），
      帮助模型基于正文判断页面内容与任务完成条件。
    """
    elements = snapshot.get_interactive_elements()
    truncated = len(elements) > max_elements
    if truncated:
        elements = elements[:max_elements]
    data: dict[str, Any] = {
        "title": _truncate(snapshot.title, max_text_length),
        "url": sanitize_url(snapshot.url),
        "page_type": snapshot.page_type,
        "loading": snapshot.loading,
        "elements": [
            serialize_element(el, max_text_length=max_text_length) for el in elements
        ],
    }
    if truncated:
        data["elements_truncated"] = True
    page_texts = [el.text for el in snapshot.texts if el.text][:max_text_elements]
    if page_texts:
        data["page_text"] = [_truncate(t, max_text_length) for t in page_texts]
    return data


def find_element_by_id(
    snapshot: Snapshot, element_id: str
) -> Optional[ElementInfo]:
    """在当前 Snapshot 的可交互元素中按 element_id 查找（只含可见元素）。

    未命中或 ID 为空时返回 None。
    """
    if not element_id:
        return None
    for el in snapshot.get_interactive_elements():
        if el.element_id == element_id:
            return el
    return None


# ── 历史窗口 ────────────────────────────────────────────────────────

def serialize_history(
    history: list[dict],
    *,
    max_items: int = _DEFAULT_MAX_HISTORY,
) -> list[dict[str, Any]]:
    """将 Agent 历史压缩为最近窗口内的摘要列表（V0.3 只保留滑动窗口）。

    每个条目只保留 action 类型 / 目标 / 成功与否 / 结果 URL，
    剔除截图 base64、下载路径、异常堆栈等敏感或冗余内容；
    URL 与当前 Snapshot 一致，经 :func:`sanitize_url` 脱敏。
    """
    window = history[-max_items:] if max_items > 0 else history
    out: list[dict[str, Any]] = []
    for entry in window:
        action = entry.get("action")
        observation = entry.get("observation")
        out.append({
            "action": getattr(action, "action", str(action)),
            "target": getattr(action, "target", None),
            "target_id": getattr(action, "target_id", None),
            "success": getattr(observation, "success", None),
            "url": sanitize_url(getattr(observation, "url", None)) if observation else None,
        })
    return out


# ═══════════════════════════════════════════════════════════════
# Prompt 构建与 Action 解析（V0.3 阶段 C）
# ═══════════════════════════════════════════════════════════════

class ActionParseError(ValueError):
    """模型输出无法安全转换为合法 Action 时抛出（内容层错误，不重试网络）。"""


# 系统提示词：稳定、短小，明确"规划器而非执行器"边界
SYSTEM_PROMPT = """你是网页任务规划器，而不是浏览器执行器。
约束：
- 每轮只能输出一个原子 Action，且只返回符合 JSON schema 的对象，不要添加 Markdown。
- 可用 action 只能是以下枚举（请原样使用，不要用 type、fill、press、submit 等浏览器术语）：
  click / input / select / goto / scroll / wait / download / upload / back / refresh / screenshot / done
- 需要操作元素的动作必须引用当前 Snapshot 中的元素 ID（target_id），不得虚构元素或编号。
- 不得构造 CSS、XPath、JavaScript 或任何选择器；不得提供 params.selector。
- 输入文本必须用 input（value 为要输入的文本）；点击用 click；跳转用 goto（value 为完整 URL）。
- 等待用 wait（value 为毫秒数，如 5000 表示等待 5 秒）。
- 页面已满足用户目标时，输出 {"action": "done"}。
- 目标中提及"关闭浏览器、退出、结束"等行为由调用方负责，Agent 完成全部页面操作后
  直接输出 {"action": "done"}，不要反复 wait 或做无意义动作。
- 涉及敏感信息输入、上传、下载、导航到外部 URL 等动作，必须服从调用方策略。"""


def build_action_schema() -> dict:
    """模型 Action 输出 schema。

    action 枚举与 :data:`agent.schema.action.VALID_ACTIONS` 同源，
    保证 Prompt 与 Action.validate() 不会漂移。
    """
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(VALID_ACTIONS)},
            "target_id": {
                "type": "string",
                "description": "当前 Snapshot 中的元素 ID，如 e1",
            },
            "target": {"type": ["string", "null"]},
            "value": {"type": ["string", "null"]},
            "params": {
                "type": "object",
                "description": "动作参数（selector 由本地注入，模型不得提供）",
            },
            "reason": {
                "type": "string",
                "description": "决策理由，仅用于日志，不参与执行",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }


def build_user_prompt(
    goal: str,
    snapshot_view: dict,
    history: list[dict] | None = None,
    *,
    constraints: list[str] | None = None,
    max_history: int = _DEFAULT_MAX_HISTORY,
) -> str:
    """构建用户提示词：目标 + Snapshot 模型视图 + 最近历史 + 可选约束。"""
    parts = [
        f"目标: {goal}",
        "当前页面:",
        json.dumps(snapshot_view, ensure_ascii=False),
    ]
    if history:
        parts.append("最近历史:")
        parts.append(json.dumps(history[-max_history:], ensure_ascii=False))
    if constraints:
        parts.append("约束: " + "; ".join(constraints))
    return "\n\n".join(parts)


def parse_action_dict(
    data: Any,
    snapshot: Snapshot,
    *,
    allow_selector: bool = False,
) -> Action:
    """将模型输出的 dict 安全转换为已验证的 Action。

    规则（与 V0.3 计划 2.5 对齐）：
    - 只接受顶层 JSON 对象；数组 / 字符串等抛出 ActionParseError。
    - 未知字段（如 ``reason``）被忽略，不写入 Action。
    - 模型提供的 ``params.selector`` 一律忽略（除非 ``allow_selector=True``），
      selector 只允许由本地 Snapshot 映射产生。
    - 需要元素的动作必须提供在当前 Snapshot 中命中的 ``target_id``；
      Planner 把元素的可执行 selector 写入 ``params["selector"]``，
      可读文本写入 ``target``。
    - 不需要元素的动作（goto/scroll/wait/back/refresh/screenshot/done）
      不要求 target_id。
    - 返回的 Action 已通过 ``Action.validate()``；失败抛 ActionParseError。
    """
    if not isinstance(data, dict):
        raise ActionParseError(
            f"模型输出必须是 JSON 对象，收到 {type(data).__name__}"
        )

    action_name = data.get("action")
    if not isinstance(action_name, str) or action_name not in VALID_ACTIONS:
        raise ActionParseError(f"未知或缺失 action: {action_name!r}")

    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise ActionParseError(f"params 必须是 JSON 对象，收到 {type(params).__name__}")
    params = dict(params)

    target_id = data.get("target_id")
    target = data.get("target")
    value = data.get("value")
    # value 契约是字符串：部分模型无视 schema 输出数字（如 wait 的毫秒数），
    # 统一强转 str 防止后续切片崩溃
    if value is not None and not isinstance(value, str):
        value = str(value)

    # 模型不得伪造 selector；selector 只由本地 Snapshot 映射产生
    if not allow_selector:
        params.pop("selector", None)

    if action_name in NEEDS_TARGET:
        element = find_element_by_id(snapshot, target_id) if target_id else None
        if target_id and element is None:
            raise ActionParseError(f"target_id 不存在于当前 Snapshot: {target_id!r}")
        if element is not None:
            params["selector"] = element.selector or ""
            target = target or (element.text or element.placeholder or element.aria_label)
        elif not allow_selector:
            raise ActionParseError(f"{action_name} 必须提供有效的 target_id")

    action = Action(
        action=action_name,
        target=target,
        target_id=target_id if target_id is not None else None,
        value=value,
        params=params,
    )
    errors = action.validate()
    if errors:
        raise ActionParseError("Action 校验失败: " + "; ".join(errors))
    return action
