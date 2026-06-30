"""Markdown 文档切分与 chunk 元数据生成。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

from iris.config.loader import ConfigBundle
from iris.ingest.scanner import DocumentRecord, MarkdownScanner, ScanSummary
from iris.utils.validation import safe_int

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-一-鿿]+")
FIELD_KEYWORDS = {
    "goal": ("目标", "希望", "计划", "实现", "达成", "覆盖"),
    "progress": ("进展", "阶段", "上线", "试点", "推进", "完成", "当前"),
    "decision": ("结论", "决议", "决定", "明确", "定为"),
    "risk": ("风险", "问题", "阻塞", "挑战", "困难"),
    "definition": ("定义", "含义", "术语", "缩写", "指的是"),
    "timeline": ("时间", "里程碑", "阶段", "本周", "下周", "季度", "日期", "计划"),
}
PATH_TAGS = {
    "周报": "weekly", "会议": "meeting", "纪要": "meeting",
    "方案": "proposal", "汇报": "report", "项目": "project",
}


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    source_name: str
    document_path: str
    relative_path: str
    document_hash: str
    title: str
    section_path: List[str]
    level: int
    content: str
    content_preview: str
    line_start: int
    line_end: int
    word_count: int
    token_count: int
    chunk_type: str = "section"
    segment_index: int = 1
    segment_count: int = 1
    structural_tags: List[str] = field(default_factory=list)
    extracted_fields: Dict[str, List[str]] = field(default_factory=dict)
    token_freq: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkSlim:
    relative_path: str
    title: str
    section_path: List[str]
    content_preview: str
    content: str = ""

    @classmethod
    def from_chunk_record(cls, chunk: ChunkRecord) -> "ChunkSlim":
        return cls(relative_path=chunk.relative_path, title=chunk.title,
                   section_path=chunk.section_path, content_preview=chunk.content_preview,
                   content=chunk.content)

    @classmethod
    def from_dict(cls, data: dict) -> "ChunkSlim":
        return cls(relative_path=data.get("relative_path", ""), title=data.get("title", ""),
                   section_path=data.get("section_path", []), content_preview=data.get("content_preview", ""),
                   content=data.get("content", ""))


@dataclass(frozen=True)
class ChunkSummary:
    source_name: str
    scanned_at: str
    document_count: int
    chunk_count: int
    chunks: List[ChunkRecord]
    build_stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"source_name": self.source_name, "scanned_at": self.scanned_at,
                "document_count": self.document_count, "chunk_count": self.chunk_count,
                "chunks": [asdict(item) for item in self.chunks], "build_stats": self.build_stats}


class MarkdownChunker:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._scanner = MarkdownScanner(config)
        ingestion = config.data_source.get("ingestion", {})
        self._max_chunk_chars = safe_int(ingestion.get("max_chunk_chars", 1200), 1200)
        self._max_preview_chars = safe_int(ingestion.get("max_preview_chars", 180), 180)
        self._metadata_dir = config.root / "data" / "metadata"

    def build_default_source_chunks(self) -> ChunkSummary:
        return self._build_chunks_from_scan(self._scanner.scan_default_source())

    def build_source_chunks(self, source_name: str) -> ChunkSummary:
        return self._build_chunks_from_scan(self._scanner.scan_source_by_name(source_name))

    def build_all_enabled_sources_chunks(self) -> List[ChunkSummary]:
        summaries: List[ChunkSummary] = []
        for source_name, cfg in self._config.data_source["sources"].items():
            if cfg.get("enabled", True):
                summaries.append(self.build_source_chunks(source_name))
        return summaries

    def _build_chunks_from_scan(self, scan_summary: ScanSummary) -> ChunkSummary:
        previous = self._load_previous_chunks_for_source(scan_summary.source_name)
        reused_documents = 0
        rebuilt_documents = 0
        rebuilt_paths: List[str] = []
        all_chunks: List[ChunkRecord] = []

        for document in scan_summary.documents:
            cached_chunks = previous.get(document.relative_path)
            if cached_chunks and all(chunk.document_hash == document.file_hash for chunk in cached_chunks):
                all_chunks.extend(cached_chunks)
                reused_documents += 1
                continue
            all_chunks.extend(_chunk_document(document, max_chunk_chars=self._max_chunk_chars,
                                              max_preview_chars=self._max_preview_chars))
            rebuilt_documents += 1
            rebuilt_paths.append(document.relative_path)

        all_chunks.sort(key=lambda item: (item.relative_path, item.line_start, item.segment_index))
        return ChunkSummary(source_name=scan_summary.source_name, scanned_at=scan_summary.scanned_at,
                            document_count=scan_summary.document_count, chunk_count=len(all_chunks),
                            chunks=all_chunks, build_stats={"reused_documents": reused_documents,
                                                            "rebuilt_documents": rebuilt_documents,
                                                            "rebuilt_paths": rebuilt_paths})

    def write_summary(self, summary: ChunkSummary) -> Path:
        self._metadata_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self._metadata_dir / f"{summary.source_name}_chunk_summary.json"
        summary_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.write_hash_index(summary)
        return summary_path

    def write_hash_index(self, summary: ChunkSummary) -> Path:
        index: Dict[str, Dict[str, str]] = {}
        existing_path = self._metadata_dir / "chunk_hash_index.json"
        if existing_path.exists():
            try:
                index = json.loads(existing_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                pass
        scan_modified: Dict[str, str] = {}
        scan_path = self._metadata_dir / f"{summary.source_name}_scan_summary.json"
        if scan_path.exists():
            try:
                scan_data = json.loads(scan_path.read_text(encoding="utf-8"))
                for doc in scan_data.get("documents", []):
                    scan_modified[doc["relative_path"]] = doc.get("modified_at", "")
            except (json.JSONDecodeError, KeyError):
                pass
        for chunk in summary.chunks:
            rp = chunk.relative_path
            if rp not in index:
                index[rp] = {"hash": chunk.document_hash, "modified_at": scan_modified.get(rp, summary.scanned_at)}
        index_path = self._metadata_dir / "chunk_hash_index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return index_path

    @staticmethod
    def load_hash_index(config: ConfigBundle) -> Dict[str, Dict[str, str]]:
        index_path = config.root / "data" / "metadata" / "chunk_hash_index.json"
        if not index_path.exists():
            return {}
        return json.loads(index_path.read_text(encoding="utf-8"))

    def _load_previous_chunks_for_source(self, source_name: str) -> Dict[str, List[ChunkRecord]]:
        summary_path = self._metadata_dir / f"{source_name}_chunk_summary.json"
        if not summary_path.exists():
            return {}
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        grouped: Dict[str, List[ChunkRecord]] = {}
        for item in payload.get("chunks", []):
            try:
                chunk = ChunkRecord(**item)
            except TypeError:
                return {}
            grouped.setdefault(chunk.relative_path, []).append(chunk)
        return grouped


def _chunk_pdf_document(document: DocumentRecord, *, max_chunk_chars: int, max_preview_chars: int) -> Iterable[ChunkRecord]:
    try:
        from iris.ingest.pdf_extractor import PDFExtractor
        extractor = PDFExtractor()
        markdown_text = extractor.extract_as_markdown(Path(document.path))
    except Exception:
        return []
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmpf:
        tmpf.write(markdown_text)
        tmp_path = Path(tmpf.name)
    pdf_doc_tmp = DocumentRecord(source_name=document.source_name, path=str(tmp_path),
                                  relative_path=document.relative_path, size_bytes=len(markdown_text),
                                  modified_at=document.modified_at, file_hash=document.file_hash, title=document.title)
    try:
        yield from _chunk_document(pdf_doc_tmp, max_chunk_chars=max_chunk_chars, max_preview_chars=max_preview_chars)
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _chunk_document(document: DocumentRecord, *, max_chunk_chars: int, max_preview_chars: int) -> Iterable[ChunkRecord]:
    path = Path(document.path)
    if path.suffix.lower() == ".pdf":
        return _chunk_pdf_document(document, max_chunk_chars=max_chunk_chars, max_preview_chars=max_preview_chars)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    sections: List[dict[str, Any]] = []
    current_content: List[str] = []
    current_section_path: List[str] = []
    current_level = 0
    current_line_start = 1

    def flush(end_line: int) -> None:
        nonlocal current_content, current_line_start
        content = "\n".join(current_content).strip()
        if not content:
            current_content = []
            current_line_start = end_line + 1
            return
        lines_in = content.split("\n")
        if len(lines_in) == 1 and HEADING_RE.match(lines_in[0].strip()):
            current_content = []
            current_line_start = end_line + 1
            return
        sections.append({"title": current_section_path[-1] if current_section_path else document.title,
                         "section_path": current_section_path.copy() or [document.title],
                         "level": current_level, "content": content,
                         "line_start": current_line_start, "line_end": end_line})
        current_content = []
        current_line_start = end_line + 1

    for index, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line.strip())
        if match:
            flush(index - 1)
            level = len(match.group(1))
            heading = match.group(2).strip()
            current_section_path = current_section_path[:level - 1]
            current_section_path.append(heading)
            current_level = level
            current_line_start = index
            current_content = [line]
        else:
            current_content.append(line)
    flush(len(lines))

    if not sections and lines:
        sections.append({"title": document.title, "section_path": [document.title],
                         "level": 0, "content": "\n".join(lines).strip(),
                         "line_start": 1, "line_end": len(lines)})

    chunk_index = 0
    built: List[ChunkRecord] = []
    for section in sections:
        segments = _split_content(section["content"], max_chunk_chars=max_chunk_chars)
        for segment_offset, segment in enumerate(segments, start=1):
            chunk_index += 1
            preview = " ".join(segment.split())[:max_preview_chars]
            token_freq = _build_token_freq(segment)
            built.append(ChunkRecord(
                chunk_id=f"{document.relative_path}::chunk-{chunk_index}",
                source_name=document.source_name, document_path=document.path,
                relative_path=document.relative_path, document_hash=document.file_hash,
                title=section["title"], section_path=section["section_path"],
                level=section["level"], content=segment, content_preview=preview,
                line_start=section["line_start"], line_end=section["line_end"],
                word_count=len(segment.split()), token_count=sum(token_freq.values()),
                chunk_type="segment" if len(segments) > 1 else "section",
                segment_index=segment_offset, segment_count=len(segments),
                structural_tags=_build_structural_tags(document.relative_path, section["section_path"], segment),
                extracted_fields=_extract_fields(segment), token_freq=token_freq))
    return built


def _split_content(content: str, *, max_chunk_chars: int) -> List[str]:
    normalized = content.strip()
    if len(normalized) <= max_chunk_chars:
        return [normalized]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if len(paragraphs) <= 1:
        return _split_hard(normalized, max_chunk_chars=max_chunk_chars)
    chunks: List[str] = []
    current = []
    current_len = 0
    for paragraph in paragraphs:
        extra = len(paragraph) + (2 if current else 0)
        if current and current_len + extra > max_chunk_chars:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
            continue
        current.append(paragraph)
        current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    split_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) > max_chunk_chars:
            split_chunks.extend(_split_hard(chunk, max_chunk_chars=max_chunk_chars))
        else:
            split_chunks.append(chunk)
    return split_chunks


def _split_hard(content: str, *, max_chunk_chars: int) -> List[str]:
    sentences = re.split(r"(?<=[。！？.!?])\s+", content)
    if len(sentences) <= 1:
        return [content[i:i + max_chunk_chars] for i in range(0, len(content), max_chunk_chars)]
    chunks: List[str] = []
    current = []
    current_len = 0
    for sentence in sentences:
        if not sentence:
            continue
        extra = len(sentence) + (1 if current else 0)
        if current and current_len + extra > max_chunk_chars:
            chunks.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
            continue
        current.append(sentence)
        current_len += extra
    if current:
        chunks.append(" ".join(current))
    return chunks


def _build_token_freq(text: str) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        freq[token] = freq.get(token, 0) + 1
    return freq


def _build_structural_tags(relative_path: str, section_path: List[str], content: str) -> List[str]:
    tags: List[str] = []
    for part in [relative_path, *section_path]:
        for marker, tag in PATH_TAGS.items():
            if marker in part and tag not in tags:
                tags.append(tag)
    for field, values in FIELD_KEYWORDS.items():
        if any(keyword in content for keyword in values) and field not in tags:
            tags.append(field)
    return tags


def _extract_fields(text: str) -> Dict[str, List[str]]:
    if len(text) < 20:
        return {}
    sentences = [item.strip() for item in re.split(r"[\n。；;]+", text) if item.strip()]
    extracted: Dict[str, List[str]] = {}
    for field, keywords in FIELD_KEYWORDS.items():
        matched = [sentence for sentence in sentences if any(keyword in sentence for keyword in keywords)]
        if matched:
            extracted[field] = matched[:2]
    return extracted
