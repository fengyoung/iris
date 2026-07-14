"""问答服务，支持本地模式与 LLM 模式。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError, LLMService
from iris.memory import CorrectionMemoryStore, SessionMemoryStore, UserProfileMemoryStore, WorkingContextStore
from iris.retrieval import EnhancedRetriever, RetrievalHit
from iris.utils.logging import IrisLogger
from iris.utils.prompting import PromptTemplateLoader

from .context import PromptContextPacker
from .helpers import infer_evidence_type, intent_title, group_title, infer_question_type, block_bonus, is_memory_only_instruction
from .memory_updater import MemoryUpdater
from .models import AnswerBlock, Citation, QAResponse


class QAService:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._retriever = EnhancedRetriever(config)
        self._llm = LLMService(config)
        self._context_packer = PromptContextPacker(config)
        self._prompt_loader = PromptTemplateLoader(config)
        self._memory = SessionMemoryStore(config)
        self._profile_memory = UserProfileMemoryStore(config)
        self._correction_memory = CorrectionMemoryStore(config)
        self._working_context = WorkingContextStore(config)
        self._memory_updater = MemoryUpdater(config)
        self._logger = IrisLogger(config)
        self._graph_cache: Any = None      # 惰性加载并缓存 WikiGraph（None=未尝试, False=不可用）

    def ask(self, question: str, *, top_k: int = 5, mode: str = "local") -> QAResponse:
        memory_updates = self._memory_updater.apply_updates(question)
        if memory_updates and is_memory_only_instruction(question):
            response = QAResponse(question=question, answer="已更新记忆：\n" + "\n".join(f"- {item}" for item in memory_updates),
                                  retrieval_total_hits=0, mode="memory_update", blocks=[],
                                  structured={"memory_updates": memory_updates}, llm={"memory_updates": memory_updates})
            session_state = self._memory.save_interaction(question=question, mode=response.mode, blocks=[], wiki_hits=[])
            response.llm.setdefault("session_memory", session_state)
            self._logger.log("qa_ask", response.to_dict())
            return response

        retrieval_mode = "llm" if mode == "llm" else "local"
        result = self._retriever.search(question, top_k=top_k, mode=retrieval_mode)
        blocks = [self._to_block(hit) for hit in result.hits]
        blocks = self._organize_blocks(question, blocks, question_type=result.query_plan.get("question_type", "topic"))
        structured = self._build_structured_payload(question, blocks, result.wiki_hits, result.query_plan)

        if mode == "llm":
            response = self._ask_with_llm(question, blocks, result.total_hits, result.llm, result.wiki_hits, result.query_plan, structured)
        else:
            answer = self._compose_local_answer(question, blocks, result.total_hits, result.wiki_hits, result.query_plan, structured)
            response = QAResponse(question=question, answer=answer, retrieval_total_hits=result.total_hits, mode="local",
                                  blocks=blocks, structured=structured,
                                  llm={"retrieval": result.llm, "wiki_hits": result.wiki_hits,
                                       "query_intent": result.query_intent, "query_plan": result.query_plan,
                                       "explanations": result.explanations})

        session_state = self._memory.save_interaction(question=question, mode=response.mode, blocks=response.blocks, wiki_hits=result.wiki_hits)
        if response.llm is None:
            response = QAResponse(question=response.question, answer=response.answer, retrieval_total_hits=response.retrieval_total_hits,
                                  mode=response.mode, blocks=response.blocks, structured=response.structured, llm={"session_memory": session_state})
        else:
            response.llm.setdefault("session_memory", session_state)
            if memory_updates:
                response.llm.setdefault("memory_updates", memory_updates)
        if memory_updates:
            response = QAResponse(question=response.question, answer=response.answer + "\n\n已同步记忆更新：\n" + "\n".join(f"- {item}" for item in memory_updates),
                                  retrieval_total_hits=response.retrieval_total_hits, mode=response.mode,
                                  blocks=response.blocks, structured=response.structured, llm=response.llm)
        self._logger.log("qa_ask", response.to_dict())
        return response

    def _ask_with_llm(self, question, blocks, total_hits, retrieval_llm, wiki_hits, query_plan, structured):
        packed_context = self._context_packer.pack(blocks, wiki_hits)
        question_type = query_plan.get("question_type", infer_question_type(question, wiki_hits))
        route_context = {"input_type": "text", "task_type": "qa", "complexity": "standard", "use_case": "qa"}
        prompt = self._build_llm_prompt(question, question_type, packed_context.blocks, packed_context.wiki_hits,
                                         packed_context.metadata, structured)
        try:
            result = self._llm.generate(prompt, route_context=route_context)
            answer = result.text.strip()
            llm_payload = {"retrieval": retrieval_llm, "wiki_hits": wiki_hits, "selected_role": result.selected_role,
                           "provider": result.provider, "model": result.model,
                           "api_base_url": result.api_base_url, "matched_rule": result.matched_rule,
                           "prompt_context": packed_context.metadata, "question_type": question_type,
                           "query_intent": query_plan.get("query_intent", "general"), "query_plan": query_plan, "fallback_used": False}
            mode = "llm"
        except LLMProviderError as exc:
            self._logger.log("qa_llm_fallback", {"question": question, "reason": str(exc)})
            answer = self._compose_fallback_answer(question, blocks, total_hits, str(exc), wiki_hits, query_plan, structured)
            llm_payload = {"retrieval": retrieval_llm, "wiki_hits": wiki_hits, "prompt_context": packed_context.metadata,
                           "question_type": question_type, "query_intent": query_plan.get("query_intent", "general"),
                           "query_plan": query_plan, "fallback_used": True, "reason": str(exc)}
            mode = "local_fallback"
        return QAResponse(question=question, answer=answer, retrieval_total_hits=total_hits, mode=mode,
                          blocks=blocks, structured=structured, llm=llm_payload)

    def _to_block(self, hit: RetrievalHit) -> AnswerBlock:
        evidence_type = infer_evidence_type(hit)
        return AnswerBlock(title=hit.title, summary=hit.content_preview,
                           citation=Citation(relative_path=hit.relative_path, section_path=hit.section_path,
                                             line_start=hit.line_start, line_end=hit.line_end),
                           score=hit.score, evidence_type=evidence_type, tags=hit.structural_tags,
                           extracted_fields=hit.extracted_fields, explanation=hit.explanation)

    def _organize_blocks(self, question, blocks, *, question_type):
        if not blocks:
            return blocks
        return sorted(blocks, key=lambda b: -(b.score + block_bonus(b, question_type)))

    def _compose_local_answer(self, question, blocks, total_hits, wiki_hits, query_plan, structured):
        if not blocks and not wiki_hits:
            return f"结论：未在当前知识库中找到与“{question}”直接相关的内容。\n建议：改写问题、补充关键词，或先执行最新的数据扫描。"
        lines = []
        overview = structured.get("overview", "")
        if overview:
            lines.append(f"结论：{overview}")
        elif wiki_hits:
            lines.append(f"结论：关于“{question}”，已优先命中 Wiki 页面《{wiki_hits[0]['title']}》，并结合原始文档片段给出回答。")
        else:
            lines.append(f"结论：针对“{question}”，当前先从本地知识库召回了 {total_hits} 条候选片段，以下整理最相关的 {len(blocks)} 条作为回答依据。")
        if wiki_hits:
            lines.extend(["", "Wiki 参考："])
            for index, hit in enumerate(wiki_hits[:3], start=1):
                matched = "/".join(hit.get("matched_terms") or [])
                suffix = f"；命中词：{matched}" if matched else ""
                lines.append(f"{index}. {hit['title']}（{hit['relative_path']}）- {hit['summary']}{suffix}")
        lines.extend(["", f"{intent_title(query_plan.get('query_intent', 'general'))}："])
        for group_name in structured.get("ordered_groups", []):
            group = structured["groups"].get(group_name, [])
            if not group:
                continue
            lines.append(f"- {group_title(group_name)}")
            for idx, item in enumerate(group[:2], start=1):
                lines.append(f"  {idx}. {item['summary']}")
        extra_blocks = blocks[:min(5, len(blocks))]
        if extra_blocks:
            lines.append("补充依据：")
            for idx, block in enumerate(extra_blocks, start=1):
                lines.append(f"{idx}. {block.summary}")
        lines.extend(["", "检索说明："])
        for item in query_plan.get("explain", [])[:4]:
            lines.append(f"- {item}")
        if blocks and blocks[0].explanation:
            lines.append(f"- Top1 解释：{blocks[0].explanation}")
        lines.extend(["", "来源："])
        for idx, block in enumerate(blocks[:5], start=1):
            section = " > ".join(block.citation.section_path) if block.citation.section_path else block.title
            lines.append(f"{idx}. {block.citation.relative_path}:{block.citation.line_start}（章节：{section}）")
        return "\n".join(lines)

    def _compose_fallback_answer(self, question, blocks, total_hits, reason, wiki_hits, query_plan, structured):
        return (f"说明：LLM 模式当前不可用，已安全回退到本地问答模式。原因：{reason}\n\n"
                + self._compose_local_answer(question, blocks, total_hits, wiki_hits, query_plan, structured))

    def _build_llm_prompt(self, question, question_type, blocks, wiki_hits, context_meta, structured):
        session_state = self._memory.load()
        template_name = {"project": "qa_project.md", "term": "qa_term.md", "topic": "qa_topic.md"}.get(question_type, "qa_topic.md")
        wiki_lines = "\n".join(f"[W{idx}] 标题：{hit['title']}；路径：{hit['relative_path']}；摘要：{hit['summary']}"
                               for idx, hit in enumerate(wiki_hits, start=1)) or "无"
        evidence_lines = "\n".join(f"[{idx}] 类型：{b.evidence_type}；标题：{b.title}；"
                                   f"来源：{b.citation.relative_path}:{b.citation.line_start}-{b.citation.line_end}；"
                                   f"内容：{b.summary}" for idx, b in enumerate(blocks, start=1)) or "无"
        structured_context = "\n".join(f"- {name}: " + " | ".join(item["summary"] for item in structured.get("groups", {}).get(name, [])[:2])
                                       for name in structured.get("ordered_groups", []) if structured.get("groups", {}).get(name)) or "无"
        session_context = self._render_session_context(session_state)
        profile_context = self._profile_memory.render_for_prompt()
        correction_context = self._correction_memory.render_for_prompt(question)
        working_context = self._working_context.render_for_prompt()
        graph_context = self._render_graph_context(wiki_hits)
        return self._prompt_loader.render(template_name, {"question": question, "context_summary": str(context_meta),
                                                          "session_context": session_context, "profile_context": profile_context,
                                                          "correction_context": correction_context, "working_context": working_context,
                                                          "wiki_lines": wiki_lines, "evidence_lines": evidence_lines,
                                                          "structured_context": structured_context,
                                                          "graph_context": graph_context})

    def _get_graph(self) -> "Any":
        """惰性加载并缓存 WikiGraph 实例（会话内只读一次磁盘）。"""
        if self._graph_cache is None:
            try:
                from iris.wiki.graph import WikiGraph
                g = WikiGraph(self._config)
                self._graph_cache = g if g.load() else False
            except Exception:
                self._graph_cache = False
        return self._graph_cache if self._graph_cache is not False else None

    def _render_graph_context(self, wiki_hits: list) -> str:
        """从知识图谱加载相关实体上下文（静默失败，图谱不存在时返回空）。"""
        if not wiki_hits:
            return "无"
        try:
            graph = self._get_graph()
            if graph is None:
                return "无"
            related_parts: List[str] = []
            for hit in wiki_hits[:3]:
                title = hit.get("title", "")
                if not title:
                    continue
                entities = graph.related_entities(title)
                if not entities:
                    continue
                lines = [f"{title} 的相关实体："]
                for ptype, items in entities.items():
                    item_strs = [f"{it['title']}({it['relation']})" for it in items[:4]]
                    lines.append(f"  {ptype}: {', '.join(item_strs)}")
                related_parts.append("\n".join(lines))
            return "\n".join(related_parts) if related_parts else "无"
        except Exception:
            return "无"

    def _render_session_context(self, session_state):
        questions = session_state.get("recent_questions", [])[:3]
        topics = session_state.get("recent_topics", [])[:5]
        summary = str(session_state.get("recent_summary", "")).strip()
        if not questions and not topics and not summary:
            return "无"
        parts = [f"最近问题：{' | '.join(questions) if questions else '无'}；最近主题：{' | '.join(topics) if topics else '无'}"]
        if summary:
            parts.append(f"会话摘要：{summary}")
        return " ".join(parts)

    def _build_structured_payload(self, question, blocks, wiki_hits, query_plan):
        groups = defaultdict(list)
        ordered_groups = []
        def register(name, payload):
            groups[name].append(payload)
            if name not in ordered_groups:
                ordered_groups.append(name)
        for block in blocks:
            bucket = block.evidence_type if block.evidence_type != "general" else "supporting"
            register(bucket, {"title": block.title, "summary": block.summary,
                              "citation": {"relative_path": block.citation.relative_path,
                                           "line_start": block.citation.line_start,
                                           "section_path": block.citation.section_path},
                              "score": block.score, "tags": block.tags})
        if wiki_hits:
            overview = f"问题“{question}”可先参考 Wiki《{wiki_hits[0]['title']}》，再由原始文档证据补强。"
        elif blocks:
            overview = blocks[0].summary
        else:
            overview = f"未找到与“{question}”直接相关的稳定证据。"
        next_steps = []
        if query_plan.get("question_type") == "project":
            next_steps.append("继续追踪最新进展与下一步动作")
        if query_plan.get("query_intent") == "risk":
            next_steps.append("补充风险责任人与缓解方案")
        if not blocks:
            next_steps.append("先执行 scan-source / build-chunks 更新索引")
        return {"question_type": query_plan.get("question_type", "topic"), "query_intent": query_plan.get("query_intent", "general"),
                "overview": overview, "groups": dict(groups), "ordered_groups": ordered_groups or ["supporting"],
                "wiki_titles": [item["title"] for item in wiki_hits[:3]], "recommended_next_steps": next_steps}
