"""向后兼容 shim — 请直接从 iris.wiki.asr 导入。"""
import warnings
warnings.warn(
    "iris.wiki.asr_hotwords 已废弃，请使用 iris.wiki.asr.hotwords",
    DeprecationWarning, stacklevel=2,
)
from iris.wiki.asr.hotwords import (  # noqa: F401 E402
    LLMHotwordExtractor,
    hotwords_to_terms,
    _build_page_batches,
    _build_hotwords_prompt,
    _clean_text_term,
    _is_valid_hotword,
    _parse_hotwords_response,
    _HOTWORD_BATCH_SIZE,
)
