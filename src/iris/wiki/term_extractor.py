"""向后兼容 shim — 请直接从 iris.wiki.asr 导入。"""
from iris.wiki.asr.extractor import (
    AsrTerm,
    AsrPromptVersion,
    TermExtractor,
    _is_noise_term,
    _clean_markup,
    _truncate_context,
)
from iris.wiki.asr.formatter import (
    render_asr_prompt,
    format_hotwords_file,
    format_replace_dict,
)
from iris.wiki.asr.hotwords import (
    LLMHotwordExtractor,
    hotwords_to_terms,
    _build_page_batches,
    _build_hotwords_prompt,
    _parse_hotwords_response,
)
from iris.wiki.asr.prompt_optimizer import LLMPromptOptimizer
from iris.wiki.asr.version import (
    load_version,
    save_version,
    bump_version,
    determine_new_version,
    compute_fingerprint,
)
