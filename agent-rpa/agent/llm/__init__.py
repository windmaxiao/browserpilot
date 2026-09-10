"""
LLM 模块（V0.3）

- base.py：LLMClient 协议与错误分类
- mock.py：确定性 Mock 客户端（无网络测试）
- openai_client.py：OpenAI 兼容 Provider 适配器（阶段 E + 国内大模型预设）
"""

from agent.llm.base import (
    LLMClient,
    LLMError,
    LLMInvalidResponseError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMTimeoutError,
)
from agent.llm.mock import MockLLMCall, MockLLMClient
from agent.llm.openai_client import (
    PROVIDER_PRESETS,
    OpenAILLMClient,
    all_provider_presets,
    load_env_files,
    load_preset_files,
    resolve_api_key_env,
    resolve_provider_preset,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMRetryableError",
    "LLMTimeoutError",
    "LLMNetworkError",
    "LLMRateLimitError",
    "LLMInvalidResponseError",
    "MockLLMClient",
    "MockLLMCall",
    "OpenAILLMClient",
    "PROVIDER_PRESETS",
    "all_provider_presets",
    "load_env_files",
    "load_preset_files",
    "resolve_provider_preset",
    "resolve_api_key_env",
]
