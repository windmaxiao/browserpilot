"""
LLM Agent 模式 Demo —— LLM 驱动真实百度搜索（V0.3）

目标：LLM 先把目标拆解为步骤队列，再依据脱敏 Snapshot 逐步执行一次真实百度搜索：
    goto(百度) → input(关键词) → click(搜索) → click(百科链接) → wait(框架直执行) → done

与 baidu_demo.py（规则引擎）的区别：本 Demo 的步骤拆解与每步操作决策均由大模型完成，
等待 / 验收类步骤由框架直接执行（不经过 LLM）；
LLM 只看到可交互元素摘要与 target_id，选择器由本地 Snapshot 映射注入。

前置条件：
    pip install -e ".[llm]"        # 安装 openai 依赖

API 配置（三选一，见 print_config_hint）：
    1) 默认 OpenAI / 任意 OpenAI 兼容端点（中转、自部署）
    2) 国内大模型预设：LLM_PROVIDER（deepseek/moonshot/zhipu/qwen/doubao/ernie/spark）
    3) 本地 .env 文件（当前目录或 ~/.browserpilot/.env）

用法（在 agent-rpa 目录下）：
    py -3.11 examples/llm_baidu_demo.py                                  # 默认目标
    py -3.11 examples/llm_baidu_demo.py "打开 百度 查找 大模型"           # 自定义目标

说明：真实网站布局可能变化，若一次未成功可调整 goal 措辞或重试；
未配置 API Key 时会给出明确提示，不会发起任何网络请求。
"""

import asyncio
import os
import sys

from loguru import logger

from agent.browser.playwright import BrowserManager
from agent.browser.snapshot import SnapshotGenerator
from agent.core.agent import Agent
from agent.core.executor import Executor
from agent.core.observer import Observer
from agent.core.planner import TaskPlanner
from agent.llm import LLMError, OpenAILLMClient, resolve_api_key_env
from agent.logging import setup_logging

DEFAULT_GOAL = (
    "打开 https://www.baidu.com 搜索 北京时间 "
    "然后点击搜索结果中的「北京时间 - 百度百科」链接，等待页面加载完成 "
    "等待五秒，最后关闭浏览器"
)


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
        gen = SnapshotGenerator(manager.page)
        manager.subscribe_page(gen.set_page)
        observer = Observer(gen)
        planner = TaskPlanner(client, model=client.model, timeout=30_000)
        agent = Agent(
            observer=observer,
            planner=planner,
            executor=Executor(tool),
            max_steps=25,
        )

        result = await agent.run(goal)

        logger.info("📋 任务结果: success={} | data={}", result.success, result.data)
        for step in agent.history:
            a = step["action"]
            o = step["observation"]
            logger.info(
                "  Step {}: {}({}) → success={}",
                step["step"], a.action, a.value or a.target_id, o.success,
            )
        if result.is_error:
            logger.error("❌ 任务失败: {}", result.error)
            return 1
        logger.info("🎉 LLM Agent 在真实百度网站跑通！")
        return 0
    finally:
        await manager.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
