"""
OpenAI 兼容 Provider 适配器（V0.3 阶段 E + 国内大模型适配）

基于 openai SDK 的 Chat Completions 实现 :class:`LLMClient` 协议。
国内主流大模型（DeepSeek / Kimi / 智谱 / 通义 / 豆包 / 千帆 / 星火等）
均提供 OpenAI 兼容端点：可通过 ``provider`` 预设一键接入，
或用 ``base_url`` 指向任意 OpenAI 兼容地址（中转/自部署）。

约定：
- API Key 只从环境变量或调用方显式传入读取，不写入日志、配置示例或异常文本。
- 超时 / 连接 / 限流 / 5xx 映射为可重试错误；认证、无效请求等
  内容或配置类错误映射为不可重试的 ``LLMError``。
- 未安装 openai 包时给出明确提示，不影响其他模块与测试。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from agent.llm.base import (
    LLMError,
    LLMInvalidResponseError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# 国内主流大模型 OpenAI 兼容端点预设。
# 字段：base_url（兼容端点）/ model（默认模型）/ env_key（专属 API Key 环境变量）。
# 任一字段均可被显式参数或通用环境变量（OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY）覆盖。
# model 已按各厂商官方文档/模型清单逐一核对（2026-08，待解决问题 #6）。
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    # DeepSeek 开放平台（deepseek-chat / deepseek-reasoner 已停用，改用 V4 系列）
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
    },
    # Moonshot（Kimi，kimi-k3 旗舰，moonshot-v1 系列已下线）
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",
        "env_key": "MOONSHOT_API_KEY",
    },
    # 智谱 AI（GLM-4.7 系列）
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "env_key": "ZHIPU_API_KEY",
    },
    # 阿里云百炼（通义千问）
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-max",
        "env_key": "DASHSCOPE_API_KEY",
    },
    # 火山方舟（豆包）：doubao-seed-2.1-pro（2026-06 发布）；
    # model 可直接传模型 ID，亦可传控制台创建的自定义推理接入点 ID
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-pro-260628",
        "env_key": "ARK_API_KEY",
    },
    # 百度千帆（文心）
    "ernie": {
        "base_url": "https://qianfan.baidubce.com/v2",
        "model": "ernie-5.0",
        "env_key": "QIANFAN_API_KEY",
    },
    # 讯飞星火
    "spark": {
        "base_url": "https://spark-api-open.xf-yun.com/v1",
        "model": "4.0Ultra",
        "env_key": "SPARK_API_KEY",
    },
}


# 包目录（agent-rpa）下的 .env：保证从任意 cwd 运行 demo/脚本都能找到项目配置
_PROJECT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"


def _load_dotenv_file(path: Path) -> None:
    """解析 ``KEY=VALUE`` 格式的 .env 文件，仅填充尚未设置的环境变量。

    行为对齐 python-dotenv 默认策略：
    - 不覆盖已存在的环境变量（显式设置 / 系统环境优先）
    - 忽略空行与 ``#`` 注释；值两侧的引号会被去除
    - 不支持变量展开；文件缺失或不可读时静默跳过
    """
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_env_files() -> None:
    """加载本地 .env 配置（先项目级后用户级，均不覆盖已有环境变量）。

    - 项目级：当前工作目录下的 ``.env``，其次包目录（agent-rpa）下的 ``.env``
      （保证从任意目录运行 demo/脚本都能找到，cwd 优先）
    - 用户级：``~/.browserpilot/.env``（存放跨项目通用的厂商 Key）
    幂等、可安全重复调用；文件均不存在时行为与之前完全一致。
    """
    _load_dotenv_file(Path.cwd() / ".env")
    _load_dotenv_file(_PROJECT_ENV)
    _load_dotenv_file(Path.home() / ".browserpilot" / ".env")


def resolve_provider_preset(provider: Optional[str]) -> Optional[dict[str, str]]:
    """返回 provider 预设；未知 provider 抛 ``LLMError``。"""
    if not provider:
        return None
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise LLMError(
            f"未知 LLM provider: {provider}（可选: {', '.join(PROVIDER_PRESETS)}）"
        )
    return preset


def resolve_api_key_env(provider: Optional[str]) -> str:
    """返回应优先读取 API Key 的环境变量名。

    命中预设时返回厂商专属变量；未指定或未知 provider 时回退 ``OPENAI_API_KEY``。
    该函数不抛异常，可安全用于提示信息。
    """
    if provider:
        preset = PROVIDER_PRESETS.get(provider)
        if preset:
            return preset["env_key"]
    return "OPENAI_API_KEY"


class OpenAILLMClient:
    """基于 openai SDK 的 Chat Completions 适配器（实现 LLMClient）。

    参数优先级（高 → 低）：
        显式参数（api_key / base_url / model） > provider 预设 > 通用环境变量 > 内置默认。
    ``provider`` 与 ``base_url`` 同时给出时，base_url 显式值优先（用于中转等场景）。
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        # 先加载本地 .env 配置（项目级与用户级，不覆盖已有环境变量）
        load_env_files()

        # provider 来源：显式参数 > 环境变量 LLM_PROVIDER
        provider = provider or os.environ.get("LLM_PROVIDER")
        preset = resolve_provider_preset(provider)

        # base_url：显式 > provider 预设 > OPENAI_BASE_URL
        if base_url is None:
            base_url = (
                preset["base_url"]
                if preset
                else os.environ.get("OPENAI_BASE_URL")
            )
        # model：显式 > provider 预设 > OPENAI_MODEL > 内置默认
        if model is None:
            model = (
                preset["model"]
                if preset
                else os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            )
        self._model = model

        # API Key：显式 > provider 专属环境变量 > OPENAI_API_KEY 回退
        if not api_key:
            env_key = preset["env_key"] if preset else "OPENAI_API_KEY"
            api_key = os.environ.get(env_key) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            hint = (
                f"请设置环境变量 {env_key}"
                if env_key == "OPENAI_API_KEY"
                else f"请设置环境变量 {env_key}（或 OPENAI_API_KEY）"
            )
            raise LLMError(f"未配置 API Key：{hint}，或传入 api_key")
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise LLMError(
                "缺少依赖 openai：请执行 pip install -e '.[llm]' 或 pip install openai"
            ) from e
        # max_retries=0：SDK 不做内部无限重试，重试策略交给上层（V0.4）
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    @property
    def model(self) -> str:
        return self._model

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        timeout: int,
    ) -> dict[str, Any]:
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                # 要求 JSON 对象输出；具体字段由系统提示词与 schema 约束
                response_format={"type": "json_object"},
                timeout=timeout / 1000.0,
            )
        except Exception as e:
            raise self._map_error(e) from e

        content = (resp.choices[0].message.content or "").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMInvalidResponseError("模型返回内容不是合法 JSON") from e
        if not isinstance(data, dict):
            raise LLMInvalidResponseError(
                f"模型返回的 JSON 不是对象，收到 {type(data).__name__}"
            )
        return data

    @staticmethod
    def _map_error(e: Exception) -> LLMError:
        """将 openai SDK 异常映射为统一错误分类（可重试 / 不可重试）。

        同时提取 HTTP 状态码（``status_code``，超时/连接类错误通常为 None），
        供上层日志展示响应码，便于区分 401/404/429/5xx 等。
        """
        try:
            import openai
        except ImportError:
            return LLMNetworkError(f"LLM 调用失败: {e}")
        status = getattr(e, "status_code", None)
        if isinstance(e, openai.APITimeoutError):
            return LLMTimeoutError("LLM 请求超时", status_code=status)
        if isinstance(e, openai.APIConnectionError):
            return LLMNetworkError("LLM 网络连接失败", status_code=status)
        if isinstance(e, openai.RateLimitError):
            return LLMRateLimitError("LLM 请求被限流", status_code=status)
        if isinstance(e, openai.InternalServerError):
            return LLMNetworkError("LLM 服务端错误", status_code=status)
        if isinstance(e, openai.APIError):
            # 认证 / 权限 / 无效请求等：不可重试
            return LLMError(f"LLM API 错误: {e}", status_code=status)
        return LLMNetworkError(f"LLM 调用失败: {e}", status_code=status)
