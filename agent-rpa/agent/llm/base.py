"""
LLM Client 抽象层（V0.3 阶段 A）

定义最小异步接口 LLMClient 与错误分类。

约定：
- Planner 只依赖本模块定义的结构子类型接口，不直接依赖任一 LLM SDK。
- Provider 适配器（openai_client.py 等）负责认证、网络调用、供应商格式差异
  与原始响应解析；LLMPlanner 只处理 Python dict。
- 异常消息不得包含 API Key、Cookie、完整提示词或敏感页面内容。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """最小 LLM 结构化输出接口。

    ``complete_json()`` 返回已解析为 dict 的 JSON 对象（非 SDK 原始响应）。
    调用方据此构造 Action；不满足接口的客户端无法通过 ``isinstance`` 校验。
    """

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        timeout: int,
    ) -> dict[str, Any]:
        """请求一次结构化 JSON 输出。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            schema: 期望的输出结构描述（Provider 可映射为结构化输出 / JSON Schema）。

                当前实现（OpenAILLMClient）暂不使用该参数：仅固定请求
                ``response_format=json_object``，结构约束由提示词承担（待解决问题
                #18）。保留该参数作为结构化输出能力（``json_schema`` 等端点）的
                预留接口，调用方仍应传入真实 schema 以兼容未来实现。
            timeout: 超时（毫秒）。

        Returns:
            解析后的 JSON 对象（dict）。

        Raises:
            LLMRetryableError: 网络/服务端层面的可重试错误
                （超时、连接失败、限流、5xx 等）。
            LLMInvalidResponseError: 内容层面的错误
                （非 JSON、结构非法等），重试同类请求通常无意义；
                是否发起一次修复请求由 LLMPlanner 决定。
        """
        ...


class LLMError(Exception):
    """LLM 调用相关错误基类。

    ``status_code``：HTTP 响应码（超时/连接类错误通常无码，为 None）。
    """

    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMRetryableError(LLMError):
    """可重试错误：网络中断、超时、限流、服务端 5xx 等。"""


class LLMTimeoutError(LLMRetryableError):
    """请求超时。"""


class LLMNetworkError(LLMRetryableError):
    """网络层错误（连接失败、DNS、TLS 等）。"""


class LLMRateLimitError(LLMRetryableError):
    """限流（HTTP 429）。"""


class LLMInvalidResponseError(LLMError):
    """不可重试的内容错误：无效 JSON、结构不匹配等。

    该类错误通常由 Provider 在解析原始响应时抛出；
    格式类错误由 LLMPlanner 判断是否针对当前请求发起一次修复请求。
    """
