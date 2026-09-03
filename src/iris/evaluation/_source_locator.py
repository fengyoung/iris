"""源文档片段定位器 — 从 chunk 摘要索引中定位源文档内容。"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SourceLocator:
    """从 chunk 摘要索引中定位源文档片段。

    支持多个 chunk 摘要（主数据源 + 共享数据源）。
    """

    def __init__(self, chunk_summary_paths: List[str]):
        self._chunk_summary_paths = [Path(p) for p in chunk_summary_paths]
        self._chunks_by_file: Dict[str, List[dict]] = {}
        self._loaded = False
        # 每文档的分词索引缓存：relative_path -> (chunk_token_sets, df, n)
        self._rel_cache: Dict[str, Tuple[List[set], Dict[str, int], int]] = {}

    @staticmethod
    def _normalize_path(relative_path: str) -> str:
        """规范化路径：统一斜杠、去除前导 ./ 和 /，消除 .. 等冗余段。"""
        normalized = str(PurePosixPath(relative_path.replace("\\", "/")))
        # PurePosixPath 会把 "./foo" 变成 "foo"，但保留 "/foo"
        return normalized.lstrip("/")

    def load(self) -> None:
        """加载所有 chunk 摘要并建立 relative_path → chunks 索引。"""
        for csp in self._chunk_summary_paths:
            if not csp.exists():
                continue
            with open(csp, "r", encoding="utf-8") as f:
                data = json.load(f)
            chunks = data["chunks"]
            for c in chunks:
                rp = self._normalize_path(c["relative_path"])
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

        rp = self._normalize_path(relative_path)
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            return None

        if line_number is not None:
            for c in chunks:
                ls = c.get("line_start", 0)
                le = c.get("line_end", 0)
                if ls <= line_number <= le:
                    return c["content"]
            logger.warning("行号 %d 在 %s 中无精确匹配，回退到末尾 chunk", line_number, rp)
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def lookup_with_context(self, relative_path: str, line_number: Optional[int] = None,
                            context_extend: int = 1) -> Optional[str]:
        """定位源 chunk 并扩展上下文（前后各 context_extend 个 chunk）。"""
        if not self._loaded:
            self.load()

        rp = self._normalize_path(relative_path)
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
            logger.warning("行号 %d 在 %s 中无精确匹配，回退到末尾 chunk", line_number, rp)
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def lookup_relevant(self, relative_path: str, description: str,
                        top_k: int = 3, max_chars: int = 2400) -> Optional[str]:
        """按引用描述的关键词相关性，返回源文档中最匹配的若干 chunk。

        用于行号定位失真的兜底：Wiki 引用的行号常落在 frontmatter/引言区，
        按行号取到的是元数据而非正文。此方法用 TF-IDF 加权（稀有词权重更高）
        对全文 chunk 评分，返回与描述最相关的 top_k 段拼接（限长）。

        Returns:
            相关 chunk 拼接文本；文档不存在 / 描述无有效 token 时返回 None。
        """
        if not self._loaded:
            self.load()

        rp = self._normalize_path(relative_path)
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            return None

        desc_tokens = self._match_tokens(description or "")
        if not desc_tokens:
            return None

        chunk_token_sets, df, n = self._doc_token_index(rp, chunks)

        scored = self._score_chunks(chunks, chunk_token_sets, df, n,
                                    desc_tokens, skip_frontmatter=True)
        if not scored:
            # 全部被跳过（极端情况，如文档只有元数据）→ 放宽不跳过
            scored = self._score_chunks(chunks, chunk_token_sets, df, n,
                                        desc_tokens, skip_frontmatter=False)
        if not scored:
            return None

        scored.sort(key=lambda x: (-x[0], x[1]))
        parts: List[str] = []
        total = 0
        for _, idx in scored[:top_k]:
            content = (chunks[idx].get("content") or "").strip()
            if not content:
                continue
            parts.append(content)
            total += len(content)
            if total >= max_chars:
                break

        return "\n\n".join(parts)[:max_chars] if parts else None

    def _doc_token_index(self, rp: str, chunks: List[dict]) -> Tuple[List[set], Dict[str, int], int]:
        """构建/取缓存：文档各 chunk 的分词集合 + 词元文档频率 + chunk 数。"""
        if rp in self._rel_cache:
            return self._rel_cache[rp]
        chunk_token_sets: List[set] = []
        df: Dict[str, int] = {}
        for c in chunks:
            ts = self._match_tokens(c.get("content") or "")
            chunk_token_sets.append(ts)
            for t in ts:
                df[t] = df.get(t, 0) + 1
        result = (chunk_token_sets, df, len(chunks))
        self._rel_cache[rp] = result
        return result

    @staticmethod
    def _score_chunks(chunks: List[dict], chunk_token_sets: List[set],
                      df: Dict[str, int], n: int, desc_tokens: set,
                      skip_frontmatter: bool) -> List[Tuple[float, int]]:
        """对 chunk 按描述相关性评分，返回 (分数, 索引) 列表（未排序）。

        评分 = Σ IDF²（IDF = log((n+1)/(df+1)) + 1）。IDF 平方使稀有词
        （人名、数字等只出现在个别 chunk 的词）主导排序，避免 frontmatter/
        引言因命中大量泛化词而挤占证据 chunk。
        """
        scored: List[Tuple[float, int]] = []
        for idx, ts in enumerate(chunk_token_sets):
            if skip_frontmatter:
                content = (chunks[idx].get("content") or "").lstrip()
                # YAML frontmatter 元数据块几乎不会承载内容性证据，跳过
                if content.startswith("---"):
                    continue
            score = 0.0
            for t in desc_tokens:
                if t in ts:
                    idf = math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0
                    score += idf * idf
            if score > 0:
                scored.append((score, idx))
        return scored

    @staticmethod
    def _match_tokens(text: str) -> set:
        """抽取匹配用词元：字母数字序列（含小数/百分号）+ 中文二元组。

        中文无空格分词，采用二元组滑窗近似；稀有词由调用方按 IDF 加权。
        """
        tokens: set = set()
        for m in re.finditer(r"[A-Za-z0-9]+(?:\.\d+)?%?", text):
            tok = m.group().lower()
            if tok.isdigit() or len(tok) >= 2:
                tokens.add(tok)
        for seg in re.findall(r"[一-鿿]+", text):
            for i in range(len(seg) - 1):
                tokens.add(seg[i:i + 2])
        return tokens

    def find_sibling_sources(self, relative_path: str, max_count: int = 3) -> List[str]:
        """通过路径相似度发现同目录下的相关源文件。"""
        if not self._loaded:
            self.load()

        rp = self._normalize_path(relative_path)
        parts = rp.split("/")
        if len(parts) <= 1:
            return []

        parent_dir = "/".join(parts[:-1])
        siblings = [p for p in self._chunks_by_file if p.startswith(parent_dir) and p != rp]
        siblings.sort(key=lambda p: sum(len(c["content"]) for c in self._chunks_by_file[p]), reverse=True)
        return siblings[:max_count]

    def search_sources_by_keywords(self, keywords: List[str], exclude_path: Optional[str] = None,
                                   max_results: int = 3) -> List[str]:
        """通过关键词匹配文件名或路径，发现相关源文件。"""
        if not self._loaded:
            self.load()

        normalized_exclude = self._normalize_path(exclude_path) if exclude_path else None
        scored: List[Tuple[int, str]] = []
        for rp in self._chunks_by_file:
            if normalized_exclude and rp == normalized_exclude:
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
