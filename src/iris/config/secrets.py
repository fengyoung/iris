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

import subprocess
import sys
from ctypes import CDLL, POINTER, c_char_p, c_uint32, c_void_p, byref
from typing import List, Optional

from iris.core.exceptions import IrisRuntimeError

# Keychain 服务标识符（所有 Iris 密钥存储在同一 service 下）
KEYCHAIN_SERVICE = "com.iris.assistant"


class KeychainError(IrisRuntimeError):
    """密钥链操作相关错误。"""


def _set_secret_native(key: str, value: str) -> bool:
    """通过 Security.framework 写入密钥，避免 secret 出现在 argv。

    返回 True 表示已在 macOS 原生路径处理；非 macOS 返回 False。
    """
    if sys.platform != "darwin":
        return False
    try:
        security = CDLL("/System/Library/Frameworks/Security.framework/Security")
        add = security.SecKeychainAddGenericPassword
        add.restype = c_uint32
        add.argtypes = [c_void_p, c_uint32, c_char_p, c_uint32, c_char_p,
                        c_uint32, c_char_p, POINTER(c_void_p)]
        service = KEYCHAIN_SERVICE.encode()
        account = key.encode()
        secret = value.encode()
        item = c_void_p()
        status = add(None, len(service), service, len(account), account,
                     len(secret), secret, byref(item))
        if status == 0:
            return True

        # 已存在时只修改密码内容，避免先删除再添加造成短暂丢失。
        find = security.SecKeychainFindGenericPassword
        find.restype = c_uint32
        find.argtypes = [c_void_p, c_uint32, c_char_p, c_uint32, c_char_p,
                         POINTER(c_uint32), POINTER(c_void_p), POINTER(c_void_p)]
        password_len = c_uint32()
        password_data = c_void_p()
        existing = c_void_p()
        status = find(None, len(service), service, len(account), account,
                      byref(password_len), byref(password_data), byref(existing))
        if status != 0 or not existing:
            raise KeychainError(f"写入 Keychain 失败 (OSStatus={status})")
        modify = security.SecKeychainItemModifyContent
        modify.restype = c_uint32
        modify.argtypes = [c_void_p, c_void_p, c_uint32, c_char_p]
        status = modify(existing, None, len(secret), secret)
        free_content = security.SecKeychainItemFreeContent
        free_content.argtypes = [c_void_p, c_void_p]
        free_content(None, password_data)
        if status != 0:
            raise KeychainError(f"写入 Keychain 失败 (OSStatus={status})")
        return True
    except KeychainError:
        raise
    except (OSError, AttributeError) as exc:
        raise KeychainError(f"macOS Security.framework 不可用: {exc}") from exc


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
    if _set_secret_native(key, value):
        return
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

    使用 security dump-keychain 从钥匙串中提取所有匹配 service 名称的条目。
    macOS 原生不支持按 service 列表 accounts，退而遍历已知 API Key 环境变量名做存在性探测。

    Returns:
        密钥名称列表
    """
    # 方案 1：从已知 API key 环境变量名列表检测（常用路径，快且无副作用）
    names: List[str] = []
    for key in _COMMON_SECRET_NAMES:
        if get_secret(key):
            names.append(key)
    return names


# 常见 Iris 密钥名列表，按 .env.example 和环境变量约定同步维护
_COMMON_SECRET_NAMES = [
    "DEEPSEEK_API_KEY", "BAILIAN_API_KEY",
    "TRELLO_API_KEY", "TRELLO_TOKEN",
    "LARK_APP_ID", "LARK_APP_SECRET",
]
