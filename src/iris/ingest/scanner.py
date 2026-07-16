"""Markdown 数据源扫描与基础元数据索引。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentRecord:
    source_name: str
    path: str
    relative_path: str
    size_bytes: int
    modified_at: str
    file_hash: str
    title: str


@dataclass(frozen=True)
class ScanSummary:
    source_name: str
    source_path: str
    scanned_at: str
    document_count: int
    documents: List[DocumentRecord]
    latest_mtime: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_path": self.source_path,
            "scanned_at": self.scanned_at,
            "document_count": self.document_count,
            "documents": [asdict(item) for item in self.documents],
            "latest_mtime": self.latest_mtime,
        }


class MarkdownScanner:
    def __init__(self, config: ConfigBundle):
        self._config = config

    def scan_default_source(self) -> ScanSummary:
        data_source = self._config.data_source
        source_name = data_source["default_source"]
        return self.scan_source_by_name(source_name)

    def scan_all_enabled_sources(self) -> List[ScanSummary]:
        data_source = self._config.data_source
        summaries: List[ScanSummary] = []
        for name, cfg in data_source["sources"].items():
            if cfg.get("enabled", True):
                summaries.append(self.scan_source_by_name(name))
        return summaries

    def write_summary(self, summary: ScanSummary) -> Path:
        metadata_dir = self._config.root / "data" / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        output_path = metadata_dir / f"{summary.source_name}_scan_summary.json"
        output_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def scan_source_by_name(self, source_name: str, *, incremental: bool = False) -> ScanSummary:
        """扫描单个数据源。

        Args:
            source_name: 数据源名称（对应 data_source.json 中的 key）
            incremental: True=仅返回变更（新增/修改）的文件，跳过未变更文件

        Returns:
            ScanSummary
        """
        data_source = self._config.data_source
        source = data_source["sources"][source_name]
        source_root = Path(source["path"]).resolve()
        source_format = source.get("format", "markdown")

        if incremental:
            return self._scan_incremental(
                source_name=source_name, source_root=source_root,
                include_patterns=source.get("include_patterns", ["**/*.md"] if source_format == "markdown" else ["**/*.pdf"]),
                exclude_patterns=source.get("exclude_patterns", []),
                max_file_size_mb=data_source["ingestion"].get("max_file_size_mb", 20),
                extract_hash=data_source["ingestion"].get("store_file_hash", True),
                file_format=source_format,
            )

        documents = self._scan_source(
            source_name=source_name,
            source_root=source_root,
            include_patterns=source.get("include_patterns", ["**/*.md"] if source_format == "markdown" else ["**/*.pdf"]),
            exclude_patterns=source.get("exclude_patterns", []),
            max_file_size_mb=data_source["ingestion"].get("max_file_size_mb", 20),
            extract_hash=data_source["ingestion"].get("store_file_hash", True),
            file_format=source_format,
        )

        latest_mtime = 0.0
        for doc in documents:
            try:
                mtime = datetime.fromisoformat(doc.modified_at).timestamp()
                if mtime > latest_mtime:
                    latest_mtime = mtime
            except (ValueError, TypeError):
                pass

        return ScanSummary(source_name=source_name, source_path=str(source_root),
                           scanned_at=_utc_now_iso(), document_count=len(documents),
                           documents=documents, latest_mtime=latest_mtime)

    def _scan_incremental(self, *, source_name: str, source_root: Path,
                          include_patterns: Sequence[str], exclude_patterns: Sequence[str],
                          max_file_size_mb: int, extract_hash: bool,
                          file_format: str = "markdown") -> ScanSummary:
        """增量扫描：仅返回新增或修改的文件，同时返回已删除的文件列表。

        比较当前文件系统的状态与上次扫描摘要，减少后续 chunker 的工作量。
        """
        # 加载上次扫描摘要
        previous = self._load_previous_scan(source_name)

        # 全量扫描当前文件系统
        current = self._scan_source(
            source_name=source_name, source_root=source_root,
            include_patterns=include_patterns, exclude_patterns=exclude_patterns,
            max_file_size_mb=max_file_size_mb, extract_hash=extract_hash,
            file_format=file_format,
        )

        # 构建索引：relative_path → DocumentRecord
        current_by_path = {doc.relative_path: doc for doc in current}

        if previous is None:
            # 无历史摘要 → 全部视为新增
            return ScanSummary(source_name=source_name, source_path=str(source_root),
                               scanned_at=_utc_now_iso(), document_count=len(current),
                               documents=current, latest_mtime=max(
                                   (datetime.fromisoformat(d.modified_at).timestamp() for d in current), default=0.0,
                               ))

        # 构建历史索引
        prev_by_path: Dict[str, Dict[str, Any]] = {}
        for doc_data in previous.get("documents", []):
            rp = doc_data.get("relative_path", "")
            if rp:
                prev_by_path[rp] = doc_data

        changed: List[DocumentRecord] = []
        deleted_paths: List[str] = []

        # 找出新增和修改的文件
        for rp, doc in current_by_path.items():
            prev = prev_by_path.get(rp)
            if prev is None:
                # 新文件
                changed.append(doc)
            elif prev.get("file_hash") != doc.file_hash:
                # 文件内容已变更
                changed.append(doc)
            # 否则跳过（未变更）

        # 找出已删除的文件
        for rp in prev_by_path:
            if rp not in current_by_path:
                deleted_paths.append(rp)

        latest_mtime = 0.0
        for doc in changed:
            try:
                mtime = datetime.fromisoformat(doc.modified_at).timestamp()
                if mtime > latest_mtime:
                    latest_mtime = mtime
            except (ValueError, TypeError):
                pass

        logger_scan = __import__("logging").getLogger(__name__)
        if changed or deleted_paths:
            logger_scan.info(
                "增量扫描 %s: +%d 变更, -%d 删除, %d 未变更",
                source_name, len(changed), len(deleted_paths),
                len(current_by_path) - len(changed),
            )
        else:
            logger_scan.info("增量扫描 %s: 无变更 (%d 文件)", source_name, len(current_by_path))

        summary = ScanSummary(source_name=source_name, source_path=str(source_root),
                              scanned_at=_utc_now_iso(), document_count=len(changed),
                              documents=changed, latest_mtime=latest_mtime)

        # 将 deleted_paths 附加到 summary 的 dict 表示中（供 chunker 清理旧 chunk）
        # 通过动态属性存储（ScanSummary.to_dict() 之外）
        summary._deleted_paths = deleted_paths  # type: ignore[attr-defined]

        return summary

    def _load_previous_scan(self, source_name: str) -> Optional[Dict[str, Any]]:
        """加载上次扫描摘要（用于增量比较）。"""
        metadata_dir = self._config.root / "data" / "metadata"
        summary_path = metadata_dir / f"{source_name}_scan_summary.json"
        if not summary_path.exists():
            return None
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _scan_source(self, *, source_name: str, source_root: Path,
                     include_patterns: Sequence[str], exclude_patterns: Sequence[str],
                     max_file_size_mb: int, extract_hash: bool,
                     file_format: str = "markdown") -> List[DocumentRecord]:
        records: List[DocumentRecord] = []
        max_size_bytes = max_file_size_mb * 1024 * 1024
        seen_paths = set()

        for pattern in include_patterns:
            for candidate in source_root.glob(pattern):
                resolved = candidate.resolve()
                if resolved in seen_paths or not candidate.is_file():
                    continue
                if _matches_any_pattern(candidate, source_root, exclude_patterns):
                    continue
                seen_paths.add(resolved)
                stat = candidate.stat()
                if stat.st_size > max_size_bytes:
                    continue
                title = _extract_pdf_title(candidate) if file_format == "pdf" else _extract_markdown_title(candidate)
                file_hash = _compute_sha256(candidate) if extract_hash else ""
                records.append(DocumentRecord(source_name=source_name, path=str(candidate.resolve()),
                                              relative_path=str(candidate.resolve().relative_to(source_root)),
                                              size_bytes=stat.st_size,
                                              modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                                              file_hash=file_hash, title=title))

        records.sort(key=lambda item: item.relative_path)
        return records


def _matches_any_pattern(candidate: Path, source_root: Path, patterns: Sequence[str]) -> bool:
    relative = candidate.resolve().relative_to(source_root)
    for pattern in patterns:
        if relative.match(pattern) or candidate.match(pattern):
            return True
    return False


def _extract_markdown_title(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if stripped.startswith("# "):
                    return stripped[2:].strip()
    except UnicodeDecodeError:
        return path.stem
    return path.stem


def _extract_pdf_title(path: Path) -> str:
    try:
        import fitz
    except ImportError:
        return path.stem
    try:
        doc = fitz.open(str(path))
        try:
            meta = doc.metadata
            title = meta.get("title", "").strip()
            if title:
                return title
            if len(doc) > 0:
                page = doc[0]
                blocks = page.get_text("text").strip().split("\n")
                for line in blocks[:3]:
                    line = line.strip()
                    if line and len(line) >= 2:
                        return line[:80]
        finally:
            doc.close()
    except Exception:
        logger.debug("PyMuPDF 标题提取失败，使用文件名: %s", path.name)
        pass
    return path.stem


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
