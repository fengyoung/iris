"""macOS 密钥链集成：安全存储 API Key，替代 .env 明文。

设计：
  - 优先级链：OS 环境变量 > .env 文件 > macOS Keychain
  - Keychain 作为 .env 的安全替代品，不要求根权限
  - 纯 macOS 方案，跨平台后续扩展（Linux secret-tool / Windows Credential Manager）

CLI 命令：
  python scripts/run_cli.py secrets-set <key>
  python scripts/run_cli.py secrets-list
  python scripts/run_cli.py secrets-delete <key>
"""

from __future__ import annotations

import re
import subprocess
from typing import List, Optional

# Keychain 服务标识符（所有 Iris 密钥存储在同一 service 下）
KEYCHAIN_SERVICE = "com.iris.assistant"


class KeychainError(RuntimeError):
    """密钥链操作相关错误。"""


def get_secret(key: str) -> Optional[str]:
    """从 macOS Keychain 读取密钥。

    Args:
        key: 密钥名称（如 DEEPSEEK_API_KEY）

    Returns:
        密钥值，未找到返回 None
    """
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", key,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def set_secret(key: str, value: str) -> None:
    """写入/更新 Keychain 中的密钥。

    Args:
        key: 密钥名称
        value: 密钥值

    Raises:
        KeychainError: 写入失败
    """
    try:
        # -U 允许覆盖已有条目，无需先删后加（避免删除成功但写入失败导致数据丢失）
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", key,
                "-w", value,
                "-U",  # 允许更新
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise KeychainError(f"写入 Keychain 失败: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise KeychainError("Keychain 操作超时")
    except FileNotFoundError:
        raise KeychainError("macOS security 命令不可用，请在 macOS 上运行")


def delete_secret(key: str) -> bool:
    """从 Keychain 删除密钥。

    Args:
        key: 密钥名称

    Returns:
        True 表示删除成功，False 表示条目不存在
    """
    try:
        result = subprocess.run(
            [
                "security", "delete-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", key,
            ],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def list_secrets() -> List[str]:
    """列出 Keychain 中所有 Iris 密钥名称（不显示值）。

    macOS security 命令一次只能查询单个 -a（account），所以需要遍历已知键名验证存在性。
    这不如全量枚举优雅，但 Keychain API 层面不支持 service 下所有 account 的批量列表。

    Returns:
        密钥名称列表
    """
    # 常见键名列表（新增键名时追加）
    _KNOWN_KEYS = [
        "DEEPSEEK_API_KEY", "BAILIAN_API_KEY",
        "TRELLO_API_KEY", "TRELLO_TOKEN",
        "LARK_APP_ID", "LARK_APP_SECRET",
    ]
    names: List[str] = []
    for key in _KNOWN_KEYS:
        if get_secret(key):
            names.append(key)
    return names
