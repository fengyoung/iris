"""会话日志：为 meeting-live-assistant 配置结构化 logging（文件 + 控制台双输出）。"""

from __future__ import annotations

import logging
from pathlib import Path

_SESSION_LOGGER_NAME = "iris.assistant"


def get_session_logger() -> logging.Logger:
    """获取 assistant 专用 logger（模块级单例）。"""
    return logging.getLogger(_SESSION_LOGGER_NAME)


def setup_session_logger(output_dir: Path, session_id: str) -> logging.Logger:
    """添加文件 handler（DEBUG 级别，持久化到过程文档同目录）。

    控制台 handler 由 live.py 模块加载时统一添加，此处不再重复。
    """
    logger = logging.getLogger(_SESSION_LOGGER_NAME)
    log_path = output_dir / f"{session_id}.log"

    # 检查是否已有文件 handler（幂等）
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path):
            return logger

    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    return logger
