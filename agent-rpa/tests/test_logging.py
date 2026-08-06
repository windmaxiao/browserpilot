"""
日志配置工具测试（V0.3）

覆盖 setup_logging：
- 写入按天滚动文件（UTF-8，含实际日志内容）
- console-only 模式不生成文件、返回 None
"""

import pytest


def test_setup_logging_writes_file(tmp_path):
    from loguru import logger

    from agent.logging import setup_logging

    try:
        log_dir = setup_logging(log_dir=tmp_path, level="DEBUG")
        assert log_dir == tmp_path
        logger.info("测试日志写入")
        logger.complete()  # 等待异步（enqueue）写入完成
    finally:
        logger.remove()

    files = list(tmp_path.glob("*.log"))
    assert files, "应生成日志文件"
    content = files[0].read_text(encoding="utf-8")
    assert "测试日志写入" in content


def test_setup_logging_console_only_returns_none():
    from agent.logging import setup_logging

    assert setup_logging(console=True, file=False) is None


def test_setup_logging_defaults_creates_logs_dir(tmp_path, monkeypatch):
    from pathlib import Path

    from loguru import logger

    from agent.logging import setup_logging

    monkeypatch.chdir(tmp_path)
    try:
        log_dir = setup_logging()
        assert log_dir == Path("logs")  # 相对启动目录的默认日志目录
        assert (tmp_path / "logs").is_dir()
    finally:
        logger.remove()
