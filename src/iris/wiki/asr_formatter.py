"""向后兼容 shim — 请直接从 iris.wiki.asr 导入。"""
import warnings
warnings.warn(
    "iris.wiki.asr_formatter 已废弃，请使用 iris.wiki.asr.formatter",
    DeprecationWarning, stacklevel=2,
)
from iris.wiki.asr.formatter import *  # noqa: F403 E402
