"""CLI 命令处理器 — 按功能域拆分到 _handlers/ 子模块。

所有 handler 函数和辅助函数通过此模块重新导出以保持向后兼容。
"""

from iris.app.cli._handlers._wiki import (
    WIKI_HANDLERS,
    handle_discover_wiki,
    handle_discover_wiki_auto,
    handle_build_wiki,
    handle_build_wiki_nav,
    handle_wiki_pipeline,
    handle_wiki_lint,
    handle_wiki_update,
    handle_enrich_persons,
    handle_build_asr_prompt,
    handle_deep_eval,
    _load_wiki_items_from_jsonl,
    _load_batch_items,
    _load_review_items,
    _strip_version_suffix,
)
from iris.app.cli._handlers._data import (
    DATA_HANDLERS,
    handle_check_config,
    handle_route_model,
    handle_scan_source,
    handle_build_chunks,
    handle_search,
    handle_ask,
    handle_build_vector_index,
    handle_build_graph,
    handle_graph_query,
    _print_graph_query_pretty,
)
from iris.app.cli._handlers._content import (
    CONTENT_HANDLERS,
    _build_biweekly_filename,
    handle_build_report,
    handle_build_mindmap,
    handle_build_biweekly_report,
    handle_transcribe_meeting,
    handle_batch_transcribe,
    handle_feishu_doc_convert,
    handle_chat_digest,
    handle_process,
    _expand_file_list,
)
from iris.app.cli._handlers._system import (
    SYSTEM_HANDLERS,
    handle_daily_start,
    _compute_daily_usage_summary,
    _daily_scan_and_chunk,
    _daily_vector_index,
    _auto_discover_wiki_for_daily,
    _daily_wiki_maintenance,
    handle_diagnose,
    handle_status,
    handle_agent_spec,
    handle_memory_status,
    handle_memory_list,
    handle_memory_delete,
    handle_memory_maintenance,
    handle_memory_export,
    handle_memory_import,
    handle_working_set,
    handle_working_show,
    handle_working_clear,
    handle_secrets_set,
    handle_secrets_list,
    handle_secrets_delete,
    handle_usage_stats,
)

# 聚合所有命令处理器（向后兼容：_cli_main.py 直接 import COMMAND_HANDLERS）
COMMAND_HANDLERS = {}
COMMAND_HANDLERS.update(WIKI_HANDLERS)
COMMAND_HANDLERS.update(DATA_HANDLERS)
COMMAND_HANDLERS.update(CONTENT_HANDLERS)
COMMAND_HANDLERS.update(SYSTEM_HANDLERS)
