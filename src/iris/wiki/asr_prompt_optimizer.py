"""向后兼容 shim — 请直接从 iris.wiki.asr 导入。"""
import warnings
warnings.warn(
    "iris.wiki.asr_prompt_optimizer 已废弃，请使用 iris.wiki.asr.prompt_optimizer",
    DeprecationWarning, stacklevel=2,
)
from iris.wiki.asr.prompt_optimizer import *  # noqa: F403 E402
