"""会话日志：为 meeting-live-assistant 配置结构化 logging（文件 + 控制台双输出）。"""

from __future__ import annotations

import logging
from pathlib import Path

_SESSION_LOGGER_NAME = "iris.assistant"


def get_session_logger() -> logging.Logger:
    """获取 assistant 专用 logger（模块级单例）。"""
    return logging.getLogger(_SESSION_LOGGER_NAME)


def setup_session_logger(output_dir: Path, session_id: str) -> logging.Logger:
    """配置双输出 logger：文件（DEBUG 级别，持久化）+ 控制台（WARNING 级别，stderr）。

    仅首次调用生效（重复调用幂等）。
    """
    logger = logging.getLogger(_SESSION_LOGGER_NAME)
    if logger.handlers:
        return logger  # 已配置，幂等

    logger.setLevel(logging.DEBUG)

    # 文件 handler：完整日志（DEBUG+），写入过程文档同目录
    log_path = output_dir / f"{session_id}.log"
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # 控制台 handler：INFO+（启动/状态通知走 stderr，不污染终端面板 stdout）
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[Iris] %(message)s"))
    logger.addHandler(ch)

    return logger
