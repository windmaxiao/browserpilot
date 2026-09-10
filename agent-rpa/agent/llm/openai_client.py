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

from loguru import logger

from agent.llm.base import (
    LLMError,
    LLMInvalidResponseError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# 厂商预设现由配置文件管理（代码不再内嵌厂商清单）：
#   - 默认：agent-rpa/llm_presets.default.yaml（随库发布，含 8 家内置厂商）
#   - 私有覆盖/新增：llm_presets.yaml（依次查 包目录 → 当前目录 → ~/.browserpilot/）
# 任一字段均可被显式参数或通用环境变量（OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY）覆盖。
# 各字段含义见 llm_presets.default.yaml 顶部注释；厂商端点/模型已按官方文档核对（2026-08）。
# ``PROVIDER_PRESETS`` 为模块加载时读默认配置文件的快照（见文件底部赋值）。


# 包目录（agent-rpa）下的 .env：保证从任意 cwd 运行 demo/脚本都能找到项目配置
_PROJECT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"

# 随库发布的内置厂商默认配置文件（入库；见同目录 llm_presets.default.yaml）
_DEFAULT_PRESETS_FILE = _PROJECT_ENV.parent / "llm_presets.default.yaml"


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


# 可覆盖/新增厂商预设的外部配置文件（用户级 > 当前目录 > 包目录 > 内置。
# 后加载者覆盖同名 provider）。支持 YAML（需 PyYAML，缺失时回退 JSON）；
# 省略顶层 ``providers`` 键时，整个文件内容视为 provider 表。
_PRESET_FILE_NAMES = ("llm_presets.yaml", "llm_presets.yml", "llm_presets.json")


def _parse_presets_file(path: Path) -> Optional[dict[str, dict[str, str]]]:
    """解析单个预设文件为 ``{provider: {base_url, model, env_key}}``；失败返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import yaml  # 可选依赖：缺失时回退 json 格式
        data = yaml.safe_load(text)
    except ImportError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("providers") if isinstance(data.get("providers"), dict) else data
    out: dict[str, dict[str, str]] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        entry = {k: cfg[k] for k in ("base_url", "model", "env_key")
                 if isinstance(cfg.get(k), str) and cfg[k]}
        if entry:
            out[str(name)] = entry
    return out or None


def load_preset_files() -> dict[str, dict[str, str]]:
    """读取所有外部厂商预设（包目录 → 当前目录 → 用户级），后加载者覆盖同名项。"""
    merged: dict[str, dict[str, str]] = {}
    for base_dir in (_PROJECT_ENV.parent, Path.cwd(), Path.home() / ".browserpilot"):
        for name in _PRESET_FILE_NAMES:
            cfg = _parse_presets_file(base_dir / name)
            if cfg:
                merged.update(cfg)
                break
    return merged


def all_provider_presets() -> dict[str, dict[str, str]]:
    """内置预设合并外部配置文件（外部覆盖内置同名项）。

    采用字段级合并：外部条目以同名内置项为基底叠加覆盖字段，
    故外部可只覆盖单个字段（如仅改 base_url）；全新 provider 未提供的
    字段缺省，由调用方回退通用环境变量。供 ``resolve_provider_preset`` /
    ``resolve_api_key_env`` 与连通性测试使用，使外部自定义的 provider 也能被识别。
    """
    merged: dict[str, dict[str, str]] = dict(PROVIDER_PRESETS)
    for name, entry in load_preset_files().items():
        merged[name] = {**merged.get(name, {}), **entry}
    return merged


def resolve_provider_preset(provider: Optional[str]) -> Optional[dict[str, str]]:
    """返回 provider 预设；未知 provider 抛 ``LLMError``。"""
    if not provider:
        return None
    presets = all_provider_presets()
    preset = presets.get(provider)
    if preset is None:
        raise LLMError(
            f"未知 LLM provider: {provider}（可选: {', '.join(presets)}）"
        )
    return preset


def resolve_api_key_env(provider: Optional[str]) -> str:
    """返回应优先读取 API Key 的环境变量名。

    命中预设时返回厂商专属变量；未指定或未知 provider 时回退 ``OPENAI_API_KEY``。
    该函数不抛异常，可安全用于提示信息。
    """
    if provider:
        preset = all_provider_presets().get(provider)
        if preset:
            return preset.get("env_key") or "OPENAI_API_KEY"
    return "OPENAI_API_KEY"


# 内置厂商默认预设：模块加载时读 llm_presets.default.yaml 的快照。
# 供向后兼容导入引用；动态合并私有覆盖请用 all_provider_presets()。
PROVIDER_PRESETS: dict[str, dict[str, str]] = (
    _parse_presets_file(_DEFAULT_PRESETS_FILE) or {}
)


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
        json_mode: str = "json_object",
    ):
        # 先加载本地 .env 配置（项目级与用户级，不覆盖已有环境变量）
        load_env_files()

        # JSON 输出模式：默认 json_object（OpenAI 与国内云厂商均支持）；
        # 本地 llama.cpp 类服务（如 LM Studio）只认 json_schema / text，
        # 需显式传 json_mode="json_schema" 才能落入其 JSON 通道（SDK 会用它组装 schema）。
        if json_mode not in ("json_object", "json_schema", "text"):
            raise LLMError(
                f"不支持的 json_mode: {json_mode}（可选 json_object / json_schema / text）"
            )
        self._json_mode = json_mode

        # provider 来源：显式参数 > 环境变量 LLM_PROVIDER
        provider = provider or os.environ.get("LLM_PROVIDER")
        preset = resolve_provider_preset(provider)

        # base_url：显式 > provider 预设 > OPENAI_BASE_URL
        if base_url is None:
            base_url = (
                (preset.get("base_url") if preset else None)
                or os.environ.get("OPENAI_BASE_URL")
            )
        # model：显式 > provider 预设 > OPENAI_MODEL > 内置默认
        if model is None:
            model = (
                (preset.get("model") if preset else None)
                or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            )
        self._model = model

        # API Key：显式 > provider 专属环境变量 > OPENAI_API_KEY 回退
        if not api_key:
            env_key = (
                (preset.get("env_key") or "OPENAI_API_KEY")
                if preset
                else "OPENAI_API_KEY"
            )
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

    def _build_response_format(self, schema: dict) -> dict:
        """按 json_mode 组装 response_format（供 Chat Completions 请求使用）。

        - json_object：标准 OpenAI 通道，OpenAI 与国内云厂商默认路径。
        - json_schema：把完整 schema 交给服务端约束输出，
          用于本地 llama.cpp 类服务（LM Studio），其不认 json_object。
          schema 为空时回退 json_object，避免发送空 schema。
        - text：不约束 JSON 结构（纯文本输出）。
        """
        if self._json_mode == "json_schema" and schema:
            return {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema},
            }
        return {"type": self._json_mode}

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
                # 要求 JSON 输出；具体类型由 json_mode 决定（见构造参数说明）。
                # json_schema 模式把完整 schema 交给服务端约束输出（本地 llama.cpp 兼容路径）
                response_format=self._build_response_format(schema),
                timeout=timeout / 1000.0,
            )
        except Exception as e:
            raise self._map_error(e) from e

        # M1：choices 提取同样可能抛异常（部分兼容端点返回空 choices 或结构异常），
        # 统一映射为不可重试的 LLMInvalidResponseError，避免击穿 Agent 主循环。
        try:
            choice = resp.choices[0]
        except (IndexError, AttributeError, TypeError) as e:
            raise LLMInvalidResponseError(
                f"模型返回结构异常（无可用 choices/content）: {type(e).__name__}"
            ) from e
        # 待解决问题 #16：finish_reason=length 表示输出因 max_tokens 被截断，
        # 不应把残缺 JSON 当作正常输出进入解析/内容层修复（修复也会再次截断）。
        # 记录明确告警并映射为不可重试错误，消息注明「截断」与普通格式错误区分。
        if getattr(choice, "finish_reason", None) == "length":
            logger.warning(
                "模型响应因 max_tokens 被截断（finish_reason=length），"
                "请增大 max_tokens 或简化输出；本次按不可重试错误处理"
            )
            raise LLMInvalidResponseError(
                "模型响应被截断（finish_reason=length）：输出超过 max_tokens 上限，"
                "请增大 max_tokens 或简化输出"
            )
        try:
            content = (choice.message.content or "").strip()
        except (IndexError, AttributeError, TypeError) as e:
            raise LLMInvalidResponseError(
                f"模型返回结构异常（无可用 choices/content）: {type(e).__name__}"
            ) from e
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
