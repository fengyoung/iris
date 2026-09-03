"""Iris 统一异常基类。

层级：
    IrisError(Exception)                         ← 捕获「一切 Iris 自身抛出的错误」
    ├── IrisRuntimeError(IrisError, RuntimeError)  ← 运行期/外部依赖失败（LLM、飞书、I/O…）
    └── IrisValueError(IrisError, ValueError)      ← 输入/配置不合法

各模块异常同时保留原标准库父类（RuntimeError / ValueError / PermissionError）在 MRO 中，
既有 ``except RuntimeError`` 的调用方不受影响；新调用方可统一 ``except IrisError``。

本模块零依赖，可被任何层导入（含 core/__init__.py 的可选导入回退路径）。
"""

from __future__ import annotations


class IrisError(Exception):
    """所有 Iris 自定义异常的根。"""


class IrisRuntimeError(IrisError, RuntimeError):
    """运行期错误：外部服务失败、资源不可用、状态不一致等。"""


class IrisValueError(IrisError, ValueError):
    """输入/配置错误：字段缺失、格式非法、取值越界等。"""


class StorageError(IrisRuntimeError):
    """存储层相关错误（SQLite / 索引文件）。"""


__all__ = ["IrisError", "IrisRuntimeError", "IrisValueError", "StorageError"]
