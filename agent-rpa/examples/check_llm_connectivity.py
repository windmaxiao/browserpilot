"""
国内大模型预设连通性测试（真实网络 + 真实 API Key）

逐个验证 PROVIDER_PRESETS 中 7 家厂商（DeepSeek / Kimi / 智谱 / 通义 / 豆包 / 千帆 / 星火）
的最新模型能否通过 OpenAI 兼容端点正常连接，并返回结构化 JSON。

用法（在 agent-rpa 目录下运行，PowerShell）：
    py -3.11 examples/check_llm_connectivity.py                     # 检查全部已配置 Key 的厂商
    py -3.11 examples/check_llm_connectivity.py deepseek zhipu      # 只检查指定厂商

说明：
- 每家厂商各发起一次最小 JSON 请求，验证「网络连通 + API Key 有效 + 模型名可用」。
- 未配置 Key 的厂商自动跳过（SKIP），不会报错。
- 任何情况下都不打印 API Key 或敏感信息。
- 退出码：存在失败项返回 1；全部通过返回 0。

运行前请先配置对应 Key 环境变量，例如：
    $env:DEEPSEEK_API_KEY = "sk-xxx"
也可统一用 OPENAI_API_KEY 回退（部分厂商需专属变量）。
"""

import asyncio
import os
import sys

from agent.llm import (
    LLMError,
    OpenAILLMClient,
    PROVIDER_PRESETS,
    load_env_files,
    resolve_api_key_env,
)

# 最小结构化输出 schema：只要求 {"ok": true}
MIN_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


async def check_provider(name: str) -> tuple[str, str, str]:
    """对单个 provider 发起一次最小请求，返回 (provider, 状态, 详情)。"""
    key_env = resolve_api_key_env(name)
    if not (os.environ.get(key_env) or os.environ.get("OPENAI_API_KEY")):
        return name, "SKIP", f"未配置 {key_env}（或 OPENAI_API_KEY）"
    try:
        client = OpenAILLMClient(provider=name)
        data = await client.complete_json(
            system_prompt="你是连通性测试助手，只输出要求的 JSON。",
            user_prompt="输出 JSON 对象：{\"ok\": true}",
            schema=MIN_SCHEMA,
            timeout=15_000,
        )
        return name, "OK", f"模型 {client.model} 返回 {data}"
    except LLMError as e:
        # 可重试错误（超时/网络/限流）与不可重试错误（认证/模型不存在等）分别归类展示
        return name, "FAIL", f"{type(e).__name__}: {e}"
    except Exception as e:  # 未分类异常（依赖缺失、SDK 意外错误等）
        return name, "FAIL", f"未分类异常 {type(e).__name__}: {e}"


async def main() -> int:
    # 先加载本地 .env（当前目录或 ~/.browserpilot/.env），再检查各家 Key
    load_env_files()
    names = sys.argv[1:] or list(PROVIDER_PRESETS)
    print(f"共 {len(names)} 家厂商待检查：{', '.join(names)}")
    print("-" * 78)
    results = []
    for name in names:
        provider, status, detail = await check_provider(name)
        results.append((provider, status, detail))
        print(f"[{status:4}] {provider:<10} {detail}")
        print("-" * 78)

    ok = [r for r in results if r[1] == "OK"]
    fail = [r for r in results if r[1] == "FAIL"]
    skip = [r for r in results if r[1] == "SKIP"]
    print(f"汇总：OK {len(ok)} / FAIL {len(fail)} / SKIP {len(skip)}")
    if fail:
        print("存在失败项，请检查对应厂商的 Key / 模型名 / 网络后重试。")
        return 1
    if not ok:
        print("未配置任何 API Key，无法进行连接测试。")
        return 1
    print("所有已配置 Key 的厂商连接正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
