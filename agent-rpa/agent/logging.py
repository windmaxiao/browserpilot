"""
日志配置工具（V0.3）

统一配置 loguru 日志输出：控制台 + 文件（按天滚动、UTF-8 编码、保留最近 7 天）。
demo 入口调用一次 :func:`setup_logging` 即可；不调用时保持 loguru 默认行为。

约定：
- 日志文件写入 ``<启动目录>/logs/browserpilot_YYYY-MM-DD.log``（可用 ``log_dir`` 覆盖）
- 日志内容不含 API Key / Cookie / 完整提示词等敏感信息
  （LLM 对话的 user_prompt 已由序列化层脱敏，URL 敏感参数已掩码）
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

DEFAULT_LOG_DIR = Path("logs")


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: Path | str | None = None,
    console: bool = True,
    file: bool = True,
) -> Path | None:
    """配置日志输出（移除 loguru 默认 handler，统一管理控制台与文件）。

    Args:
        level: 日志级别（DEBUG / INFO / WARNING 等）。
        log_dir: 日志目录；默认 ``<启动目录>/logs``。
        console: 是否输出到控制台。
        file: 是否输出到文件。

    Returns:
        启用文件日志时返回日志目录，否则返回 None。
    """
    logger.remove()
    if console:
        logger.add(sys.stderr, level=level)
    if not file:
        return None
    log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "browserpilot_{time:YYYY-MM-DD}.log",
        level=level,
        rotation="00:00",   # 每天零点滚动
        retention=7,        # 保留最近 7 天
        encoding="utf-8",
        enqueue=False,      # 同步写入：进程异常退出时不丢日志
    )
    return log_dir
