"""Wikilink 自动注入器 — 基于 Wiki 页面索引为文档正文注入 [[wikilink]]。

在文档写入前，对正文中的已知实体名称（人名、项目名、概念名）进行匹配，
将首次出现替换为 ``[[wikilink]]``。零 LLM 成本，纯字符串匹配 + 索引查找。

用法:
    from iris.wiki.wikilink_injector import WikilinkInjector

    injector = WikilinkInjector(wiki_root)
    content = injector.inject(content)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from iris.core.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)

# ── 正则常量 ────────────────────────────────────────────────

# 匹配代码块（```...```），使用非贪婪匹配处理内含反引号的情况
_CODE_BLOCK_RE = re.compile(r'```.*?```', re.DOTALL)
# 匹配行内代码 — 单反引号（`...`）和双反引号（``...``）
_INLINE_CODE_RE = re.compile(r'``[^`]+``|`[^`]+`')
# 匹配已有的 wikilink [[...]] 或 [[...|...]]
_EXISTING_WIKILINK_RE = re.compile(r'\[\[(?:[^\[\]]*?\|)?([^\[\]]*?)\]\]')
# 匹配 markdown 链接 [text](url)
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\([^\)]*\)')
# 匹配图片 ![alt](url)
_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^\)]*\)')
# 匹配 URL（允许括号、中文等）
_URL_RE = re.compile(r'https?://[^\s\[\]<>"]+')
# 匹配 YAML frontmatter 块
_FRONTMATTER_RE = re.compile(r'^---\s*\n.*?\n---\s*\n', re.DOTALL)

# 最少标题长度（太短的标题容易误匹配）
_MIN_TITLE_LENGTH = 2

# 占位符：用于保护区域（代码块、已有链接、URL 等）
_PROTECT_PLACEHOLDER = "\x00"
# 标记：新创建的 wikilink 的开始/结束（防止后续短标题嵌套替换）
_WL_START = "\x01"
_WL_END = "\x02"


class WikilinkInjector:
    """基于 Wiki 索引的 wikilink 自动注入器。

    在文档写入前对正文中的已知实体名称进行匹配，将首次出现替换为
    ``[[wikilink]]``。

    设计约束：
    - 仅替换正文中每个实体的首次出现
    - 仅匹配 Wiki 中已存在的页面标题
    - 跳过代码块、行内代码、已有链接、URL、图片、frontmatter
    - 按标题长度降序匹配（长标题优先，避免子串误匹配）
    - 零 LLM 成本
    """

    def __init__(self, wiki_root: Path):
        """初始化注入器，从 Wiki 目录扫描所有页面建立标题索引。

        Args:
            wiki_root: LLM-WIKI 根目录路径
        """
        self._wiki_root = Path(wiki_root).resolve()
        self._title_to_target: Dict[str, str] = {}  # display_title → relative_path
        self._titles_sorted: List[str] = []          # 按长度降序排列的标题列表
        self._build_index()

    # ── 公共 API ──────────────────────────────────────────

    def inject(self, content: str, exclude_titles: Optional[Set[str]] = None) -> str:
        """在正文中为已知实体首次出现注入 [[wikilink]]。

        Args:
            content: Markdown 正文（不应包含 frontmatter，如有则 frontmatter 会被保护）
            exclude_titles: 要跳过的标题集合（如已在 frontmatter 中的标题）

        Returns:
            注入 wikilink 后的正文。如无匹配则返回原内容。
        """
        if not self._titles_sorted:
            return content

        exclude = exclude_titles or set()

        # Step 1: 标记保护区（代码块、已有链接、图片、URL、frontmatter）
        protected = self._find_protected_regions(content)

        # Step 2: 用占位符替换保护区，正文部分用于匹配
        safe_content = self._mask_regions(content, protected, _PROTECT_PLACEHOLDER)

        # Step 3: 按标题长度降序匹配，每个标题仅替换首次出现
        replaced: Dict[str, str] = {}  # title → target（已处理过的）
        for title in self._titles_sorted:
            if title in exclude:
                continue
            if title in replaced:
                continue
            target = self._title_to_target.get(title, "")
            if not target:
                continue
            # 在安全内容中查找标题（跳过 \x01...\x02 标记区域）
            idx = self._find_safe(safe_content, title)
            if idx == -1:
                continue
            # 用临时标记包裹 wikilink（使用 _find_safe 返回的位置，避免匹配到标记内部）
            safe_content = (
                safe_content[:idx]
                + f"{_WL_START}{target}{_WL_END}"
                + safe_content[idx + len(title):]
            )
            replaced[title] = target

        # Step 4: 还原 wikilink 标记为真正的 [[]]
        safe_content = safe_content.replace(_WL_START, "[[").replace(_WL_END, "]]")

        # Step 5: 还原保护区
        result = self._unmask_regions(safe_content, protected, _PROTECT_PLACEHOLDER)

        return result

    def get_title_index(self) -> Dict[str, str]:
        """返回 title → relative_path 索引（只读）。"""
        return dict(self._title_to_target)

    def refresh(self) -> None:
        """重新扫描 Wiki 目录，刷新标题索引。

        在 ``build-wiki-nav`` 或 ``daily-start`` 等 Wiki 更新后调用。
        """
        self._title_to_target.clear()
        self._titles_sorted.clear()
        self._build_index()

    # ── 索引构建 ──────────────────────────────────────────

    def _build_index(self) -> None:
        """扫描 Wiki 目录，建立标题 → 相对路径索引。"""
        if not self._wiki_root.exists():
            logger.warning("Wiki 根目录不存在，跳过 wikilink 索引构建: %s",
                           self._wiki_root)
            return

        type_dirs = ["01-领域", "02-概念", "03-项目", "04-人物"]
        for type_dir_name in type_dirs:
            type_dir = self._wiki_root / type_dir_name
            if not type_dir.is_dir():
                continue
            for md_file in type_dir.glob("*.md"):
                self._index_page(md_file, type_dir_name)

        # 按标题长度降序排列
        self._titles_sorted = sorted(
            self._title_to_target.keys(),
            key=len,
            reverse=True,
        )
        logger.info("Wikilink 索引构建完成: %d 个标题", len(self._titles_sorted))

    def _index_page(self, filepath: Path, type_dir_name: str) -> None:
        """索引单个 Wiki 页面，提取标题并建立映射。"""
        try:
            text = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        # 相对路径（作为 wikilink 的 target），去掉 .md 后缀
        rel_path = f"{type_dir_name}/{filepath.name}"
        if rel_path.endswith(".md"):
            rel_path = rel_path[:-3]

        # 从 frontmatter 提取 title
        fields, _body = parse_frontmatter(text)
        fm_title = fields.get("title", "").strip()

        # 从文件名提取标题（去掉类型前缀如 "项目-"）
        stem = filepath.stem
        file_title = self._extract_title_from_filename(stem)

        # 收集候选标题
        titles: set = set()
        if fm_title:
            titles.add(fm_title)
        if file_title and file_title != fm_title:
            titles.add(file_title)

        # 也加入完整文件名作为备选（用于精确匹配场景）
        titles.add(stem)

        for t in titles:
            t = t.strip()
            if len(t) < _MIN_TITLE_LENGTH:
                continue
            # 如果已存在同名标题，保留较短的 target 路径
            if t in self._title_to_target:
                existing = self._title_to_target[t]
                if len(rel_path) < len(existing):
                    self._title_to_target[t] = rel_path
                continue
            self._title_to_target[t] = rel_path

    @staticmethod
    def _extract_title_from_filename(stem: str) -> str:
        """从文件名中去掉类型前缀（如 ``项目-``、``人物-``）。

        Example:
            "项目-XRay手机拆修检测项目" → "XRay手机拆修检测项目"
            "人物-冯扬" → "冯扬"
        """
        for prefix in ["领域-", "概念-", "项目-", "人物-"]:
            if stem.startswith(prefix):
                return stem[len(prefix):]
        return stem

    # ── 保护区处理 ────────────────────────────────────────

    def _find_protected_regions(self, content: str) -> List[Tuple[int, int, str]]:
        """找到所有保护区（代码块、已有链接、图片、URL、frontmatter 等）的位置。

        Returns:
            [(start, end, original_text), ...] 按位置排序
        """
        regions: List[Tuple[int, int, str]] = []

        # YAML frontmatter
        for m in _FRONTMATTER_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # 代码块
        for m in _CODE_BLOCK_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # 已有的 wikilink
        for m in _EXISTING_WIKILINK_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # Markdown 链接 [text](url)
        for m in _MD_LINK_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # 图片 ![alt](url)
        for m in _IMAGE_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # 行内代码（在链接之后，避免保护链接内部的 `）
        for m in _INLINE_CODE_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # URL
        for m in _URL_RE.finditer(content):
            regions.append((m.start(), m.end(), m.group()))

        # 合并重叠区域，按位置排序
        regions.sort(key=lambda r: r[0])
        return self._merge_regions(regions)

    @staticmethod
    def _merge_regions(
        regions: List[Tuple[int, int, str]]
    ) -> List[Tuple[int, int, str]]:
        """合并重叠的保护区。"""
        if not regions:
            return []
        merged: List[Tuple[int, int, str]] = []
        cur_start, cur_end, cur_text = regions[0]
        for start, end, text in regions[1:]:
            if start <= cur_end:
                # 重叠 → 合并，保留外层文本
                cur_end = max(cur_end, end)
            else:
                merged.append((cur_start, cur_end, cur_text))
                cur_start, cur_end, cur_text = start, end, text
        merged.append((cur_start, cur_end, cur_text))
        return merged

    @staticmethod
    def _mask_regions(
        content: str,
        regions: List[Tuple[int, int, str]],
        placeholder: str,
    ) -> str:
        """用占位符替换保护区，返回安全内容。"""
        # 从后往前替换，保持位置不变
        result = content
        for start, end, _original in reversed(regions):
            result = result[:start] + placeholder + result[end:]
        return result

    @staticmethod
    def _unmask_regions(
        masked: str,
        regions: List[Tuple[int, int, str]],
        placeholder: str,
    ) -> str:
        """将占位符还原为原始文本。

        从前往后依次替换（str.replace 总是匹配第一个占位符）。
        """
        result = masked
        for _start, _end, original in regions:
            result = result.replace(placeholder, original, 1)
        return result

    # ── 字符串替换 ────────────────────────────────────────

    @staticmethod
    def _find_safe(text: str, needle: str) -> int:
        """在文本中查找 needle，跳过 ``\\x01...\\x02`` 标记区域。

        已注入的 wikilink 用 ``\\x01 target \\x02`` 包裹，
        target 路径本身可能包含短标题子串，需跳过以避免嵌套匹配。
        """
        search_start = 0
        while True:
            idx = text.find(needle, search_start)
            if idx == -1:
                return -1
            # 检查该位置是否在 \x01...\x02 区域内
            # 向前查找最近的 \x01 和 \x02
            last_wl_start = text.rfind(_WL_START, 0, idx)
            if last_wl_start != -1:
                last_wl_end = text.find(_WL_END, last_wl_start)
                if last_wl_end != -1 and idx < last_wl_end:
                    # needle 在已注入的 wikilink 区域内部，跳过
                    search_start = last_wl_end + 1
                    continue
            return idx

    @staticmethod
    def _replace_first(text: str, old: str, new: str) -> str:
        """替换字符串中首次出现的 old（仅在非占位符区域）。"""
        idx = text.find(old)
        if idx == -1:
            return text
        return text[:idx] + new + text[idx + len(old):]
