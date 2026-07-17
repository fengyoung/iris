"""向后兼容 shim — 请直接从 iris.wiki.asr 导入。"""
from iris.wiki.asr.hotwords import (  # noqa: F401
    LLMHotwordExtractor,
    hotwords_to_terms,
    _build_page_batches,
    _build_hotwords_prompt,
    _clean_text_term,
    _is_valid_hotword,
    _parse_hotwords_response,
    _HOTWORD_BATCH_SIZE,
)
