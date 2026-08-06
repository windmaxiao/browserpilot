"""
确定性 Mock LLM 客户端（V0.3 阶段 A）

用于无网络测试与 Demo：按预设响应队列返回 dict，或原样抛出预设异常
（网络错误 / 超时 / 限流 / 无效 JSON），并记录每次调用的参数供测试断言。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agent.llm.base import LLMInvalidResponseError


@dataclass
class MockLLMCall:
    """记录一次 ``complete_json`` 调用（供测试断言）。"""

    system_prompt: str
    user_prompt: str
    schema: dict
    timeout: int


class MockLLMClient:
    """按预设响应序列返回 dict 的确定性客户端。

    用法::

        client = MockLLMClient([{"action": "click", "target_id": "e1"}])
        result = await client.complete_json(
            system_prompt="...", user_prompt="...", schema={...}, timeout=30_000,
        )

    响应队列（``responses``）元素可以是:

    - ``dict``: 直接作为返回值。
    - ``Exception`` 子类实例: 原样抛出，用于模拟超时 / 限流 / 无效 JSON 等。

    队列耗尽后回退到 ``default_response``；两者都未配置时抛出
    :class:`LLMInvalidResponseError`，避免测试在静默缺省下误通过。
    """

    def __init__(
        self,
        responses: Optional[list] = None,
        *,
        default_response: Optional[dict] = None,
    ):
        self._queue: list = list(responses) if responses else []
        self.default_response: Optional[dict] = default_response
        self.calls: list[MockLLMCall] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        timeout: int,
    ) -> dict[str, Any]:
        self.calls.append(MockLLMCall(system_prompt, user_prompt, schema, timeout))
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.default_response is not None:
            return self.default_response
        raise LLMInvalidResponseError(
            "MockLLMClient 未配置响应（responses 队列为空且未设置 default_response）"
        )
