"""CLI 命令的可读输出格式化 — 步骤 1 版本。"""

from __future__ import annotations

from typing import Any, Dict, List


def format_payload(command: str, payload: Dict[str, Any]) -> str:
    """将命令输出 payload 格式化为人类可读文本。"""
    handler = _FORMATTERS.get(command)
    if handler is None:
        return ""
    return handler(payload)


def _fmt_diagnose(p: Dict[str, Any]) -> str:
    lines = ["## 诊断结果"]
    _add_kv(lines, "项目根目录", p.get("project_root"))
    _add_kv(lines, "数据源存在", p.get("data_source_exists"))
    _add_kv(lines, "默认路由角色", p.get("default_route_role"))
    _add_kv(lines, "Base Model 密钥", p.get("base_model_has_key"))
    _add_kv(lines, "Adv Model 密钥", p.get("adv_model_has_key"))
    if p.get("log_file"):
        lines.append(f"  日志文件：{p['log_file']}")
    return "\n".join(lines)


def _fmt_status(p: Dict[str, Any]) -> str:
    lines = ["## 项目状态"]
    _add_kv(lines, "数据源存在", p.get("data_source_exists"))
    _add_kv(lines, "Base Model", p.get("base_model_has_key"))
    _add_kv(lines, "Adv Model", p.get("adv_model_has_key"))
    if p.get("suggested_next_action"):
        lines.append(f"\n建议操作：{p['suggested_next_action']}")
    return "\n".join(lines)


def _fmt_agent_spec(p: Dict[str, Any]) -> str:
    lines = [f"## Agent 协议 v{p.get('protocol_version', p.get('version', ''))}"]
    for name, spec in p.get("commands", {}).items():
        lines.append(f"\n### {name}")
        lines.append(f"  目的：{spec.get('purpose', '')}")
        inputs = spec.get("inputs", [])
        if inputs:
            lines.append(f"  输入：{', '.join(inputs)}")
        outputs = spec.get("outputs", [])
        if outputs:
            lines.append(f"  输出：{', '.join(outputs)}")
    return "\n".join(lines)


def _fmt_memory_status(p: Dict[str, Any]) -> str:
    lines = ["## 记忆状态"]
    _add_kv(lines, "画像更新", p.get("profile_updated_at", "无"))
    _add_kv(lines, "偏好-喜欢", p.get("likes_count", 0))
    _add_kv(lines, "偏好-不喜欢", p.get("dislikes_count", 0))
    _add_kv(lines, "风格偏好", p.get("style_preferences_count", 0))
    _add_kv(lines, "备注数", p.get("notes_count", 0))
    _add_kv(lines, "纠正规则数", p.get("correction_count", 0))
    if p.get("sample_corrections"):
        lines.append("最近纠正：")
        for item in p["sample_corrections"]:
            lines.append(f"  · {item.get('concept', '')} => {item.get('preferred', '')}")
    return "\n".join(lines)


def _fmt_memory_list(p: Dict[str, Any]) -> str:
    lines = ["## 记忆列表"]
    profile = p.get("profile")
    if profile:
        lines.append("\n### 用户画像")
        prefs = profile.get("user_preferences", {})
        for key in ("likes", "dislikes", "style_preferences", "notes"):
            items = prefs.get(key, [])
            if items:
                lines.append(f"  {key}：{' | '.join(str(i) for i in items[:5])}")
    corrections = p.get("corrections", {})
    if isinstance(corrections, dict):
        items = corrections.get("items", [])
        if items:
            lines.append(f"\n### 纠正规则（{corrections.get('correction_count', len(items))} 条）")
            for item in items[:10]:
                lines.append(f"  · {item.get('concept', '')} → {item.get('preferred', '')}（{item.get('update_count', 0)} 次）")
    return "\n".join(lines)


def _fmt_memory_delete(p: Dict[str, Any]) -> str:
    deleted = p.get("deleted", False)
    return f"删除纠正规则「{p.get('concept', '')}」：{'成功' if deleted else '未找到'}"


def _fmt_memory_export(p: Dict[str, Any]) -> str:
    return f"记忆已导出到：{p.get('output_file', '')}"


def _fmt_memory_import(p: Dict[str, Any]) -> str:
    lines = ["## 记忆导入"]
    _add_kv(lines, "替换模式", p.get("replace", False))
    _add_kv(lines, "画像已更新", p.get("profile_updated", False))
    _add_kv(lines, "纠正规则已更新", p.get("corrections_updated", False))
    _add_kv(lines, "纠正规则数", p.get("correction_count", 0))
    return "\n".join(lines)


def _fmt_working(p: Dict[str, Any]) -> str:
    lines = ["## 工作上下文"]
    if p.get("current_task"):
        _add_kv(lines, "当前任务", p["current_task"])
    if p.get("pending_items"):
        _add_kv(lines, "待办", " | ".join(p["pending_items"]))
    if p.get("recent_changes"):
        _add_kv(lines, "最近变更", " | ".join(p["recent_changes"]))
    if p.get("notes"):
        _add_kv(lines, "备注", p["notes"])
    if p.get("updated_at"):
        _add_kv(lines, "更新时间", p["updated_at"])
    return "\n".join(lines)


def _fmt_route_model(p: Dict[str, Any]) -> str:
    lines = ["## 模型路由"]
    _add_kv(lines, "选中的角色", p.get("selected_role", ""))
    _add_kv(lines, "回退角色", p.get("fallback_role", ""))
    _add_kv(lines, "匹配规则", p.get("matched_rule", ""))
    return "\n".join(lines)


def _fmt_check_config(p: Dict[str, Any]) -> str:
    return "配置检查通过"


def _fmt_process(p: Dict[str, Any]) -> str:
    lines = ["## 复杂输入处理"]
    _add_kv(lines, "查询", p.get("query", ""))
    _add_kv(lines, "是否为复杂输入", p.get("is_complex", False))
    if p.get("stage1_prompt"):
        lines.append(f"\n阶段 1（指令生成）：\n{p['stage1_prompt'][:500]}")
    if p.get("stage2_output"):
        lines.append(f"\n阶段 2（多模态理解）：\n{p['stage2_output'][:1000]}")
    if p.get("stage3_output"):
        lines.append(f"\n阶段 3（整合润色）：\n{p['stage3_output'][:1000]}")
    return "\n".join(lines)


def _fmt_search(p: Dict[str, Any]) -> str:
    lines = ["## 检索结果"]
    lines.append(f"查询：{p.get('query', '')}")
    plan = p.get("query_plan", {})
    lines.append(f"问题类型：{plan.get('question_type', '')}")
    lines.append(f"意图：{plan.get('query_intent', '')}")
    lines.append(f"总命中：{p.get('total_hits', 0)}")
    for expl in p.get("explanations", []):
        lines.append(f"  · {expl}")
    hits = p.get("hits", [])
    if hits:
        lines.append(f"\nTop {len(hits)} 命中：")
        for i, hit in enumerate(hits, 1):
            lines.append(f"  {i}. {hit.get('title', '')}（{hit.get('relative_path', '')}:{hit.get('line_start', '')}）score={hit.get('score', 0)}")
            if hit.get("matched_terms"):
                lines.append(f"     命中词：{','.join(hit['matched_terms'][:5])}")
    wiki = p.get("wiki_hits", [])
    if wiki:
        lines.append(f"\nWiki 页面（{len(wiki)} 条）：")
        for hit in wiki:
            lines.append(f"  · {hit.get('title', '')}（{hit.get('relative_path', '')}）")
    return "\n".join(lines)


def _fmt_ask(p: Dict[str, Any]) -> str:
    lines = ["## 问答结果"]
    lines.append(f"问题：{p.get('question', '')}")
    lines.append(f"模式：{p.get('mode', '')}")
    answer = p.get("answer", "")
    if answer:
        lines.append(f"\n{answer}")
    return "\n".join(lines)


def _fmt_build_report(p: Dict[str, Any]) -> str:
    lines = ["## 分析报告"]
    lines.append(f"主题：{p.get('query', '')}")
    lines.append(f"模式：{p.get('mode', '')}")
    md = p.get("markdown", "")
    if md:
        lines.append(f"\n{md[:3000]}")
    return "\n".join(lines)


def _fmt_build_mindmap(p: Dict[str, Any]) -> str:
    lines = ["## 思维导图"]
    lines.append(f"主题：{p.get('query', '')}")
    lines.append(f"模式：{p.get('mode', '')}")
    lines.append(f"格式：{p.get('format', 'mermaid')}")
    if p.get("output_file"):
        lines.append(f"输出文件：{p['output_file']}")
    if p.get("xmind_file"):
        lines.append(f"XMind 文件：{p['xmind_file']}")
    md = p.get("markdown", "")
    if md:
        lines.append(f"\n{md[:3000]}")
    return "\n".join(lines)


def _fmt_scan_source(p: Dict[str, Any]) -> str:
    sources = p.get("sources", [p])
    lines = ["## 扫描摘要"]
    for s in sources:
        lines.append(f"数据源：{s.get('source_name', '')} | 扫描时间：{s.get('scanned_at', '')} | 文档数：{s.get('document_count', 0)}")
        if s.get("summary_path"):
            lines.append(f"  摘要文件：{s['summary_path']}")
    return "\n".join(lines)


def _fmt_build_chunks(p: Dict[str, Any]) -> str:
    sources = p.get("sources", [p])
    lines = ["## 切块摘要"]
    for s in sources:
        stats = s.get("build_stats", {})
        lines.append(f"数据源：{s.get('source_name', '')} | 文档数：{s.get('document_count', 0)} | Chunk 数：{s.get('chunk_count', 0)}")
        lines.append(f"  复用：{stats.get('reused_documents', 0)} | 重建：{stats.get('rebuilt_documents', 0)}")
        if s.get("summary_path"):
            lines.append(f"  摘要文件：{s['summary_path']}")
    return "\n".join(lines)


def _fmt_build_vector_index(p: Dict[str, Any]) -> str:
    results = p.get("results", [])
    lines = ["## 向量索引"]
    for r in results:
        icon = "✅" if r.get("status") == "ok" else ("❌" if r.get("status") == "error" else "⏭")
        detail = f"索引 {r.get('indexed', 0)} 条" if r.get("status") == "ok" else r.get("reason", "")
        lines.append(f"  {icon} {r.get('source', '')}：{detail}")
    return "\n".join(lines)


def _fmt_discover_wiki(p: Dict[str, Any]) -> str:
    items = p.get("items", [])
    type_names = {"domain": "领域", "concept": "概念", "project": "项目", "person": "人物"}
    lines = [f"## Wiki 候选（共 {len(items)} 条）"]
    for ptype in ("domain", "concept", "project", "person"):
        pt_items = [i for i in items if i.get("page_type") == ptype]
        if not pt_items:
            continue
        lines.append(f"\n### {type_names.get(ptype, ptype)}")
        for item in pt_items:
            has = "✓" if item.get("has_wiki") else " "
            stale = " ⚠️" if item.get("wiki_stale") else ""
            lines.append(f"  [{has}] {item.get('title', '')} (score={item.get('score', 0)}){stale}")
    for key in ("export_jsonl", "export_review", "export_review_md"):
        if p.get(key):
            lines.append(f"\n导出文件：{p[key]}")
    return "\n".join(lines)


def _fmt_build_wiki(p: Dict[str, Any]) -> str:
    lines = ["## Wiki 页面草稿"]
    lines.append(f"标题：{p.get('title', '')}")
    lines.append(f"类型：{p.get('page_type', '')}")
    lines.append(f"输出路径：{p.get('output_path', '')}")
    wr = p.get("write_result")
    if wr:
        lines.append(f"写入动作：{wr.get('action', '')}")
        if wr.get("backup_path"):
            lines.append(f"备份路径：{wr['backup_path']}")
    markdown = p.get("markdown", "")
    if markdown:
        lines.append(f"\n--- Markdown 预览 ---\n{markdown[:1500]}")
    return "\n".join(lines)


def _fmt_build_wiki_nav(p: Dict[str, Any]) -> str:
    lines = ["## Wiki 导航页"]
    _add_kv(lines, "页面数", p.get("pages_written", 0))
    if p.get("nav_path"):
        _add_kv(lines, "生成路径", p["nav_path"])
    return "\n".join(lines)


def _fmt_wiki_pipeline(p: Dict[str, Any]) -> str:
    lines = ["## Wiki 流水线"]
    _add_kv(lines, "候选数", p.get("candidate_count"))
    for key in ("export_jsonl", "export_review"):
        if p.get(key):
            _add_kv(lines, key, p[key])
    if p.get("next_step"):
        lines.append(f"\n下一步：{p['next_step']}")
    return "\n".join(lines)


def _fmt_wiki_lint(p: Dict[str, Any]) -> str:
    lines = ["## Wiki 健康检查"]
    _add_kv(lines, "页面总数", p.get("page_count", 0))
    by_type = p.get("by_type", {})
    if by_type:
        parts = [f"{k}={v}页" for k, v in by_type.items()]
        lines.append(f"  分类：{' / '.join(parts)}")

    # 页面质量
    lines.append("")
    lines.append("### 页面质量")
    _add_kv(lines, "缺少 Frontmatter", p.get("no_frontmatter_count", 0))
    _add_kv(lines, "缺少摘要", p.get("no_summary_count", 0))
    _add_kv(lines, "零出链（未链接任何页面）", p.get("zero_outbound_count", 0))
    _add_kv(lines, "草稿/过时", p.get("stale_count", 0))
    _add_kv(lines, "超过 90 天未更新", p.get("old_page_count", 0))

    # 链接质量
    lines.append("")
    lines.append("### 链接质量")
    raw = p.get("raw_broken_count", 0)
    real = p.get("broken_count", 0)
    excluded = p.get("excluded_broken", 0)
    _add_kv(lines, "断裂链接（原始）", f"{raw}（已排除 {excluded} 个技术术语/源文档引用）")
    _add_kv(lines, "真正断裂的 Wiki 链接", real)
    _add_kv(lines, "孤立页", p.get("orphan_count", 0))
    for key, label in [("orphan_pages", "孤立页"), ("broken_links", "断裂链接"),
                        ("stale_pages", "草稿页"), ("old_pages", ">90天未更新"),
                        ("no_frontmatter", "缺少Frontmatter"), ("no_summary", "缺少摘要"),
                        ("zero_outbound", "未链接其他页面")]:
        items = p.get(key, [])
        if items:
            lines.append(f"\n{label}（前{min(len(items),5)}条）：")
            for item in items[:5]:
                lines.append(f"  - {item}")

    # 索引质量
    iq = p.get("index_quality", {})
    if iq:
        lines.append("")
        lines.append("### 索引质量")
        _add_kv(lines, "SOURCE 文档数", iq.get("source_documents", 0))
        _add_kv(lines, "已切块文档", iq.get("chunked_documents", 0))
        _add_kv(lines, "覆盖比例", f"{iq.get('chunk_coverage_pct', 0)}%")
        _add_kv(lines, "Chunk 总数", iq.get("total_chunks", 0))
        _add_kv(lines, "向量索引", "✅ 存在" if iq.get("vector_index_exists") else "❌ 未构建")
        if iq.get("vector_index_size_kb"):
            _add_kv(lines, "向量索引大小", f"{iq['vector_index_size_kb']} KB")
        _add_kv(lines, "上次扫描", iq.get("last_scanned", "")[:19])

    # 内容质量
    cq = p.get("content_quality", {})
    if cq:
        lines.append("")
        lines.append("### 内容质量")
        density = cq.get("info_density", {})
        if density:
            _add_kv(lines, "均章节字数", density.get("avg_words_per_section", 0))
            thin = density.get("thin_pages", [])
            if thin:
                lines.append(f"  偏薄页面 (<500字): {', '.join(thin[:5])}")
        dup_count = cq.get("duplicate_count", 0)
        _add_kv(lines, "高相似度页面对", f"{dup_count} 对 (Jaccard>30%)")
        for d in cq.get("duplicates", [])[:3]:
            lines.append(f"  - {d['pair'][0]} ↔ {d['pair'][1]} ({d['similarity']})")

    return "\n".join(lines)


def _fmt_transcribe_meeting(p: Dict[str, Any]) -> str:
    lines = ["## 会议纪要"]
    if p.get("audio_file"):
        lines.append(f"  录音文件：{p['audio_file']}")
    if p.get("transcript_file"):
        lines.append(f"  转写文本：{p['transcript_file']}")
    lines.append(f"  字数：{p.get('word_count', 0)}")
    lines.append(f"  Wiki 页面：{p.get('wiki_pages_loaded', 0)}")
    lines.append(f"  输出文件：{p.get('output_file', '')}")
    return "\n".join(lines)


def _fmt_batch_transcribe(p: Dict[str, Any]) -> str:
    lines = ["## 批量会议纪要"]
    lines.append(f"  总数：{p.get('total', 0)}")
    lines.append(f"  成功：{p.get('succeeded', 0)}")
    lines.append(f"  失败：{p.get('failed', 0)}")
    for r in p.get("results", []):
        icon = "✅" if r.get("status") == "ok" else "❌"
        lines.append(f"  {icon} {r.get('file', '')}")
    return "\n".join(lines)


def _fmt_daily_start(p: Dict[str, Any]) -> str:
    scan_list = p.get("scan", [])
    chunks_list = p.get("chunks", [])
    lines = ["## 日常启动"]
    total_docs = sum(s.get("document_count", 0) for s in (scan_list if isinstance(scan_list, list) else []))
    total_chunks = sum(c.get("chunk_count", 0) for c in (chunks_list if isinstance(chunks_list, list) else []))
    lines.append(f"扫描文档数：{total_docs}")
    lines.append(f"Chunk 数：{total_chunks}")
    reminders = p.get("reminders", {})
    if isinstance(reminders, dict) and reminders.get("signal_count", 0) > 0:
        lines.append("")
        lines.append(_fmt_reminders(reminders))
    return "\n".join(lines)


_REMINDER_TYPE_NAMES = {
    "category_inactive": "栏目断供",
    "weekly_report_missing": "周报缺失",
    "project_stalled": "项目停滞",
}


def _fmt_reminders(p: Dict[str, Any]) -> str:
    lines = ["## 主动提醒"]
    if p.get("status") == "skipped":
        lines.append(f"  ⏭ 已跳过：{p.get('reason', '')}")
        return "\n".join(lines)
    if p.get("status") == "error":
        lines.append(f"  ❌ 采集失败：{p.get('reason', '')}")
        return "\n".join(lines)
    signals = p.get("signals", [])
    if not signals:
        lines.append("  ✅ 无异常信号")
        return "\n".join(lines)
    lines.append(f"  ⚠️ 共 {len(signals)} 条信号")
    for s in signals:
        type_name = _REMINDER_TYPE_NAMES.get(s.get("type", ""), s.get("type", ""))
        lines.append(f"  - [{type_name}] {s.get('detail', '')}")
    return "\n".join(lines)


def _fmt_build_asr_prompt(p: Dict[str, Any]) -> str:
    lines = ["## ASR 校正提示词"]
    _add_kv(lines, "Wiki 页面数", p.get("page_count", 0))
    _add_kv(lines, "提示词长度（字符）", p.get("prompt_chars", 0))
    sections = p.get("sections", [])
    if sections:
        lines.append(f"  包含 {len(sections)} 个页面：{' / '.join(sections[:10])}")
        if len(sections) > 10:
            lines.append(f"    ... 共 {len(sections)} 个")
    return "\n".join(lines)


def _add_kv(lines: List[str], key: str, value: Any) -> None:
    if value is None:
        return
    lines.append(f"  {key}：{value}")


_FORMATTERS: Dict[str, Any] = {
    "transcribe-meeting": _fmt_transcribe_meeting,
    "batch-transcribe": _fmt_batch_transcribe,
    "daily-start": _fmt_daily_start,
    "search": _fmt_search,
    "ask": _fmt_ask,
    "build-report": _fmt_build_report,
    "build-mindmap": _fmt_build_mindmap,
    "scan-source": _fmt_scan_source,
    "build-chunks": _fmt_build_chunks,
    "build-vector-index": _fmt_build_vector_index,
    "discover-wiki": _fmt_discover_wiki,
    "discover-wiki-auto": _fmt_discover_wiki,
    "build-wiki": _fmt_build_wiki,
    "build-wiki-nav": _fmt_build_wiki_nav,
    "wiki-pipeline": _fmt_wiki_pipeline,
    "wiki-lint": _fmt_wiki_lint,
    "build-asr-prompt": _fmt_build_asr_prompt,
    "reminders": _fmt_reminders,
    "diagnose": _fmt_diagnose,
    "status": _fmt_status,
    "agent-spec": _fmt_agent_spec,
    "memory-status": _fmt_memory_status,
    "memory-list": _fmt_memory_list,
    "memory-delete": _fmt_memory_delete,
    "memory-export": _fmt_memory_export,
    "memory-import": _fmt_memory_import,
    "working-set": _fmt_working,
    "working-show": _fmt_working,
    "working-clear": _fmt_working,
    "route-model": _fmt_route_model,
    "check-config": _fmt_check_config,
    "process": _fmt_process,
}
