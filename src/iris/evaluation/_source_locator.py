"""源文档片段定位器 — 从 chunk 摘要索引中定位源文档内容。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SourceLocator:
    """从 chunk 摘要索引中定位源文档片段。

    支持多个 chunk 摘要（work_docs_main + xiaolongxia_shared）。
    """

    def __init__(self, chunk_summary_paths: List[str]):
        self._chunk_summary_paths = [Path(p) for p in chunk_summary_paths]
        self._chunks_by_file: Dict[str, List[dict]] = {}
        self._loaded = False

    def load(self) -> None:
        """加载所有 chunk 摘要并建立 relative_path → chunks 索引。"""
        for csp in self._chunk_summary_paths:
            if not csp.exists():
                continue
            with open(csp, "r", encoding="utf-8") as f:
                data = json.load(f)
            chunks = data["chunks"]
            for c in chunks:
                rp = c["relative_path"]
                if rp not in self._chunks_by_file:
                    self._chunks_by_file[rp] = []
                self._chunks_by_file[rp].append(c)
        for rp in self._chunks_by_file:
            self._chunks_by_file[rp].sort(key=lambda x: x["line_start"])
        self._loaded = True

    def lookup(self, relative_path: str, line_number: Optional[int] = None) -> Optional[str]:
        """根据相对路径和行号定位源 chunk 内容。"""
        if not self._loaded:
            self.load()

        rp = relative_path.replace("\\", "/")
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            for prefix in ("/", "./"):
                if rp.startswith(prefix):
                    rp = rp[len(prefix):]
                    break
            chunks = self._chunks_by_file.get(rp)

        if not chunks:
            return None

        if line_number is not None:
            for c in chunks:
                ls = c.get("line_start", 0)
                le = c.get("line_end", 0)
                if ls <= line_number <= le:
                    return c["content"]
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def lookup_with_context(self, relative_path: str, line_number: Optional[int] = None,
                            context_extend: int = 1) -> Optional[str]:
        """定位源 chunk 并扩展上下文（前后各 context_extend 个 chunk）。"""
        if not self._loaded:
            self.load()

        rp = relative_path.replace("\\", "/")
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            for prefix in ("/", "./"):
                if rp.startswith(prefix):
                    rp = rp[len(prefix):]
                    break
            chunks = self._chunks_by_file.get(rp)
        if not chunks:
            return None

        if line_number is not None:
            for idx, c in enumerate(chunks):
                ls = c.get("line_start", 0)
                le = c.get("line_end", 0)
                if ls <= line_number <= le:
                    start = max(0, idx - context_extend)
                    end = min(len(chunks), idx + context_extend + 1)
                    parts = [chunks[i]["content"] for i in range(start, end)]
                    return "\n\n".join(parts).strip() or None
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def find_sibling_sources(self, relative_path: str, max_count: int = 3) -> List[str]:
        """通过路径相似度发现同目录下的相关源文件。"""
        if not self._loaded:
            self.load()

        parts = relative_path.split("/")
        if len(parts) <= 1:
            return []

        parent_dir = "/".join(parts[:-1])
        siblings = [rp for rp in self._chunks_by_file if rp.startswith(parent_dir) and rp != relative_path]
        siblings.sort(key=lambda rp: sum(len(c["content"]) for c in self._chunks_by_file[rp]), reverse=True)
        return siblings[:max_count]

    def search_sources_by_keywords(self, keywords: List[str], exclude_path: Optional[str] = None,
                                   max_results: int = 3) -> List[str]:
        """通过关键词匹配文件名或路径，发现相关源文件。"""
        if not self._loaded:
            self.load()

        scored: List[Tuple[int, str]] = []
        for rp in self._chunks_by_file:
            if exclude_path and rp == exclude_path:
                continue
            rp_lower = rp.lower()
            score = sum(1 for kw in keywords if kw.lower() in rp_lower)
            if score > 0:
                scored.append((score, rp))

        scored.sort(key=lambda x: -x[0])
        return [rp for _, rp in scored[:max_results]]

    def get_all_source_paths(self) -> List[str]:
        """返回所有已知源文件路径。"""
        if not self._loaded:
            self.load()
        return list(self._chunks_by_file.keys())
