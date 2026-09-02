"""
LLM Agent 模式 Demo —— LLM 驱动的完整 Agent Loop（V0.3）

运行环境：Playwright 浏览器 + 可用的 OpenAI 兼容 API
目标：让 LLMPlanner 自动完成一次搜索任务（在本地受控页面，不依赖外网）

执行流程（全部由 LLM 依据 Snapshot 决策）：
    goto(本地搜索页) → input(关键词) → click(搜索按钮) → done

前置条件：
    pip install -e ".[llm]"        # 安装 openai 依赖

API 配置（二选一）：
    1) 默认 OpenAI / 任意 OpenAI 兼容端点（中转、自部署）：
       $env:OPENAI_API_KEY = "sk-xxx"          # 必填
       $env:OPENAI_BASE_URL = "https://..."    # 可选
       $env:OPENAI_MODEL = "gpt-4o-mini"       # 可选
    2) 国内大模型预设（DeepSeek / Kimi / 智谱 / 通义 / 豆包 / 千帆 / 星火）：
       $env:LLM_PROVIDER = "deepseek"          # 预设名，见 agent/llm/openai_client.py PROVIDER_PRESETS
       $env:DEEPSEEK_API_KEY = "sk-xxx"        # 厂商专属变量（可用 OPENAI_API_KEY 回退）

可选环境变量：
    LLM_PROVIDER  国内大模型预设名（deepseek/moonshot/zhipu/qwen/doubao/ernie/spark）
    OPENAI_MODEL  模型名（不设置时使用 provider 预设的默认模型）
    OPENAI_BASE_URL  兼容 API 的 base_url（覆盖 provider 预设，用于中转/自部署）

说明：这是受控 Demo，不涉及上传、下载或外网导航等高危操作；
未配置 API Key 时会给出明确提示，不会发起任何网络请求。
"""

import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from agent.browser.playwright import BrowserManager
from agent.browser.snapshot import SnapshotGenerator
from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import LLMPlanner
from agent.llm import LLMError, OpenAILLMClient, resolve_api_key_env
from agent.logging import setup_logging

SCRIPT_DIR = Path(__file__).parent
SEARCH_PAGE_URL = (SCRIPT_DIR / "search_page.html").as_uri()

DEFAULT_GOAL = f"打开 {SEARCH_PAGE_URL} 查找 北京时间"


def print_config_hint(provider: str | None, error: LLMError) -> None:
    """打印 API 配置提示（不含任何密钥信息）。"""
    print(f"LLM 客户端初始化失败: {error}")
    print()
    print("API 配置方式：")
    print("  1) 默认 OpenAI / 任意 OpenAI 兼容端点：")
    print("     $env:OPENAI_API_KEY = 'sk-xxx'   # 可选 OPENAI_BASE_URL / OPENAI_MODEL")
    print("  2) 国内大模型预设（deepseek/moonshot/zhipu/qwen/doubao/ernie/spark）：")
    print("     $env:LLM_PROVIDER = 'deepseek'")
    print(f"     $env:{resolve_api_key_env(provider)} = 'sk-xxx'")
    print("  3) 写入 .env 文件（当前目录或 ~/.browserpilot/.env，KEY=VALUE 一行一个）：")
    print("     LLM_PROVIDER=deepseek")
    print(f"     {resolve_api_key_env(provider)}=sk-xxx")


async def main() -> int:
    # 控制台 + logs/ 目录按天滚动文件（level 可调；移除本行则回到默认 INFO 控制台）
    setup_logging(level="DEBUG")
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    logger.info("🎯 目标: {}", goal)

    provider = os.environ.get("LLM_PROVIDER")
    try:
        client = OpenAILLMClient(
            provider=provider,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            model=os.environ.get("OPENAI_MODEL"),
        )
    except LLMError as e:
        print_config_hint(provider, e)
        return 1

    manager = BrowserManager(headless=False)
    await manager.start()
    try:
        tool = manager.create_tool()
        # 跟随新标签页：点击 target=_blank 链接后 SnapshotGenerator 同步切换页面
        gen = SnapshotGenerator(
            manager.page,
            dialog_provider=tool.dialogs,  # 待解决问题 #50：把 JS 弹窗记录放入 Snapshot
        )
        manager.subscribe_page(gen.set_page)
        observer = Observer(gen)
        planner = LLMPlanner(client, model=client.model, timeout=30_000)
        agent = Agent(
            observer=observer,
            planner=planner,
            executor=Executor(tool),
            max_steps=20,
        )

        result = await agent.run(goal)

        logger.info("📋 任务结果: success={} | data={}", result.success, result.data)
        if result.is_error:
            logger.error("❌ 任务失败: {}", result.error)
            return 1
        logger.info("🎉 LLM Agent 端到端跑通！")
        return 0
    finally:
        # 待解决问题 #7：成对注销页面订阅，避免监听器与旧 Generator 泄漏
        manager.unsubscribe_page(gen.set_page)
        await manager.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
