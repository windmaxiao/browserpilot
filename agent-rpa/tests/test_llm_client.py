"""
LLM Client 抽象测试（V0.3 阶段 A）

覆盖：
- MockLLMClient 响应队列 / 默认响应 / 队列耗尽 / 预设异常 / 调用记录
- LLMClient 结构子类型校验（鸭子类型）
- 错误分类：可重试（超时/网络/限流）与不可重试（内容错误）
"""

import os
import sys

import pytest

from agent.llm import (
    LLMClient,
    LLMError,
    LLMInvalidResponseError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMTimeoutError,
    MockLLMClient,
    OpenAILLMClient,
    load_env_files,
    openai_client,
)


@pytest.fixture(autouse=True)
def isolate_real_env(monkeypatch, tmp_path):
    """模块内默认隔离真实 .env（cwd 与包目录），避免本机真实 Key 污染测试进程。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(openai_client, "_PROJECT_ENV", tmp_path / "unused.env")


class TestMockLLMClient:
    async def test_returns_preset_dict(self):
        client = MockLLMClient([{"action": "done"}])
        result = await client.complete_json(
            system_prompt="s", user_prompt="u", schema={}, timeout=1000
        )
        assert result == {"action": "done"}

    async def test_queue_consumed_in_order(self):
        client = MockLLMClient([{"n": 1}, {"n": 2}])
        r1 = await client.complete_json(
            system_prompt="s", user_prompt="u", schema={}, timeout=1000
        )
        r2 = await client.complete_json(
            system_prompt="s", user_prompt="u", schema={}, timeout=1000
        )
        assert [r1, r2] == [{"n": 1}, {"n": 2}]

    async def test_falls_back_to_default_response(self):
        client = MockLLMClient([{"n": 1}], default_response={"n": 9})
        r1 = await client.complete_json(
            system_prompt="s", user_prompt="u", schema={}, timeout=1000
        )
        r2 = await client.complete_json(
            system_prompt="s", user_prompt="u", schema={}, timeout=1000
        )
        assert [r1, r2] == [{"n": 1}, {"n": 9}]

    async def test_raises_when_no_response_configured(self):
        client = MockLLMClient()
        with pytest.raises(LLMInvalidResponseError):
            await client.complete_json(
                system_prompt="s", user_prompt="u", schema={}, timeout=1000
            )

    async def test_raises_queued_exception(self):
        client = MockLLMClient([LLMTimeoutError("mock timeout")])
        with pytest.raises(LLMTimeoutError, match="mock timeout"):
            await client.complete_json(
                system_prompt="s", user_prompt="u", schema={}, timeout=1000
            )

    async def test_records_call_arguments(self):
        client = MockLLMClient([{}])
        await client.complete_json(
            system_prompt="sys", user_prompt="user", schema={"type": "object"}, timeout=42
        )
        assert client.call_count == 1
        call = client.calls[0]
        assert call.system_prompt == "sys"
        assert call.user_prompt == "user"
        assert call.schema == {"type": "object"}
        assert call.timeout == 42

    def test_satisfies_llm_client_protocol(self):
        # 结构子类型：Mock 客户端必须能当作 LLMClient 使用
        assert isinstance(MockLLMClient([{}]), LLMClient)


class TestErrorClassification:
    def test_timeout_is_retryable(self):
        assert issubclass(LLMTimeoutError, LLMRetryableError)

    def test_network_is_retryable(self):
        assert issubclass(LLMNetworkError, LLMRetryableError)

    def test_rate_limit_is_retryable(self):
        assert issubclass(LLMRateLimitError, LLMRetryableError)

    def test_invalid_response_is_not_retryable(self):
        assert issubclass(LLMInvalidResponseError, LLMError)
        assert not issubclass(LLMInvalidResponseError, LLMRetryableError)

    def test_all_errors_share_base(self):
        for exc in (LLMTimeoutError, LLMNetworkError,
                    LLMRateLimitError, LLMInvalidResponseError):
            assert issubclass(exc, LLMError)

    def test_status_code_propagates_to_error(self):
        """HTTP 状态码随错误分类传递（供日志展示 401/404/429/5xx 等）"""
        assert LLMRateLimitError("限流", status_code=429).status_code == 429
        assert LLMTimeoutError("超时").status_code is None


class TestOpenAILLMClient:
    def test_missing_api_key_gives_clear_error(self, monkeypatch):
        """未配置 API Key 时给出明确、无敏感信息的提示"""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(LLMError, match="OPENAI_API_KEY"):
            OpenAILLMClient()

    def test_explicit_api_key_accepted_without_env(self, monkeypatch):
        """显式传入 api_key 时无需环境变量（未装 openai 时提示安装依赖）"""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # 模拟 openai 未安装：拦截 import，让 OpenAILLMClient 提示安装依赖
        import builtins

        monkeypatch.delitem(sys.modules, "openai", raising=False)
        real_import = builtins.__import__

        def _block_openai(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_openai)
        with pytest.raises(LLMError, match="openai"):
            OpenAILLMClient(api_key="sk-dummy")


class TestOpenAILLMClientProvider:
    """国内大模型 provider 预设解析（注入假 openai 模块，不依赖真实网络/依赖）。"""

    @pytest.fixture
    def fake_openai(self, monkeypatch):
        import sys
        import types

        class _FakeAsyncOpenAI:
            def __init__(self, api_key, base_url=None, max_retries=0):
                self.api_key = api_key
                self.base_url = base_url or "https://api.openai.com/v1"
                self.max_retries = max_retries

        fake = types.ModuleType("openai")
        fake.AsyncOpenAI = _FakeAsyncOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake)
        return _FakeAsyncOpenAI

    def test_deepseek_preset_fills_endpoint_model_env(self, monkeypatch, fake_openai):
        """provider 预设自动填充 base_url / 默认模型 / 专属 Key 环境变量"""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        client = OpenAILLMClient(provider="deepseek")
        assert client.model == "deepseek-v4-flash"
        assert "api.deepseek.com" in client._client.base_url
        assert client._client.api_key == "sk-deepseek"

    def test_provider_from_env_var(self, monkeypatch, fake_openai):
        """LLM_PROVIDER 环境变量可代替显式 provider 参数"""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "zhipu")
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-zhipu")
        client = OpenAILLMClient()
        assert client.model == "glm-4.7-flash"
        assert "bigmodel.cn" in client._client.base_url

    def test_unknown_provider_gives_clear_error(self, monkeypatch, fake_openai):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(LLMError, match="未知 LLM provider"):
            OpenAILLMClient(provider="not-a-provider")

    def test_explicit_params_override_preset(self, monkeypatch, fake_openai):
        """显式参数优先于 provider 预设（中转 / 自定义模型）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        client = OpenAILLMClient(
            provider="deepseek",
            model="my-model",
            base_url="https://proxy.example/v1",
        )
        assert client.model == "my-model"
        assert client._client.base_url == "https://proxy.example/v1"
        assert client._client.api_key == "sk-deepseek"

    def test_openai_env_fallback_without_provider(self, monkeypatch, fake_openai):
        """无 provider 时使用通用环境变量 OPENAI_*，行为与 V0.3 一致"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        client = OpenAILLMClient()
        assert client.model == "gpt-4o"
        assert client._client.base_url == "https://proxy.example/v1"

    def test_provider_key_env_resolution(self):
        from agent.llm import resolve_api_key_env

        assert resolve_api_key_env("deepseek") == "DEEPSEEK_API_KEY"
        assert resolve_api_key_env("moonshot") == "MOONSHOT_API_KEY"
        assert resolve_api_key_env(None) == "OPENAI_API_KEY"
        assert resolve_api_key_env("not-a-provider") == "OPENAI_API_KEY"

    def test_provider_presets_are_complete(self):
        from agent.llm import PROVIDER_PRESETS

        for name, preset in PROVIDER_PRESETS.items():
            assert set(preset) == {"base_url", "model", "env_key"}, name
            assert preset["base_url"].startswith("https://"), name


class TestEnvFileLoading:
    """本地 .env 配置加载（零依赖，仅填充未设置的环境变量）。"""

    @pytest.fixture
    def isolated_env(self, monkeypatch, tmp_path):
        """把 cwd 与家目录都指向临时目录，隔离真实环境与包目录 .env。"""
        from pathlib import Path

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(openai_client, "_PROJECT_ENV", tmp_path / "unused.env")
        monkeypatch.delenv("_BP_TEST_KEY", raising=False)

    def test_loads_key_from_cwd_env(self, isolated_env, tmp_path):
        (tmp_path / ".env").write_text(
            "# 注释行\n_BP_TEST_KEY=hello\n", encoding="utf-8"
        )
        load_env_files()
        assert os.environ["_BP_TEST_KEY"] == "hello"
        os.environ.pop("_BP_TEST_KEY", None)

    def test_project_level_env_loaded_from_any_cwd(
        self, monkeypatch, isolated_env, tmp_path
    ):
        """cwd 无 .env 时，包目录（agent-rpa）下的 .env 仍会被加载。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".env").write_text(
            "_BP_TEST_KEY=project\n", encoding="utf-8"
        )
        monkeypatch.setattr(openai_client, "_PROJECT_ENV", project_dir / ".env")
        load_env_files()
        assert os.environ["_BP_TEST_KEY"] == "project"
        os.environ.pop("_BP_TEST_KEY", None)

    def test_user_level_env_file_also_loaded(self, isolated_env, tmp_path):
        (tmp_path / ".browserpilot").mkdir(parents=True)
        (tmp_path / ".browserpilot" / ".env").write_text(
            "_BP_TEST_KEY=user\n", encoding="utf-8"
        )
        load_env_files()
        assert os.environ["_BP_TEST_KEY"] == "user"
        os.environ.pop("_BP_TEST_KEY", None)

    def test_existing_env_not_overridden(self, isolated_env, tmp_path):
        os.environ["_BP_TEST_KEY"] = "existing"
        (tmp_path / ".env").write_text("_BP_TEST_KEY=file\n", encoding="utf-8")
        load_env_files()
        assert os.environ["_BP_TEST_KEY"] == "existing"
        os.environ.pop("_BP_TEST_KEY", None)

    def test_strips_quotes_and_skips_bad_lines(self, isolated_env, tmp_path):
        (tmp_path / ".env").write_text(
            '_BP_TEST_KEY="x y"\n_BP_EMPTY=\n=bad\n# c\n', encoding="utf-8"
        )
        load_env_files()
        assert os.environ.get("_BP_TEST_KEY") == "x y"
        assert os.environ.get("_BP_EMPTY") == ""  # 与 dotenv 一致：空值设为空字符串
        assert os.environ.get("=bad") is None  # 无 key 的行被跳过
        os.environ.pop("_BP_TEST_KEY", None)

    def test_missing_env_files_are_noop(self, isolated_env):
        load_env_files()  # 无任何 .env 文件时静默通过
