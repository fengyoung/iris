"""飞书通讯录人物信息丰富器 — 从飞书组织架构补充人物 Wiki 页面的部门和邮箱信息。

用法:
    enricher = PersonEnricher(bundle)
    result = enricher.enrich(dry_run=False)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.feishu.client import FeishuClient, FeishuClientError

logger = logging.getLogger(__name__)

# YAML frontmatter 正则
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 批量查询每批最多 names 数（lark-cli --queries 无硬性限制，保守分批）
_BATCH_SIZE = 20


@dataclass
class EnrichResult:
    """单个人物的丰富结果。"""
    name: str
    status: str  # "updated" | "not_found" | "ambiguous" | "skipped" | "no_change"
    department: str = ""
    email: str = ""
    message: str = ""


@dataclass
class EnrichSummary:
    """丰富任务汇总。"""
    total: int = 0
    updated: int = 0
    not_found: int = 0
    ambiguous: int = 0
    skipped: int = 0
    no_change: int = 0
    errors: int = 0
    details: List[EnrichResult] = field(default_factory=list)


class PersonEnricher:
    """从飞书通讯录补充人物 Wiki 页面的部门/邮箱信息。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"]) if config.wiki else Path()
        self._person_dir = self._wiki_root / "04-人物"
        self._client = FeishuClient(as_user=True)

    # ── 公开接口 ──────────────────────────────────────────

    def enrich(self, *, dry_run: bool = False) -> EnrichSummary:
        """执行全量人物丰富流程。

        Args:
            dry_run: 为 True 时仅预览不写入。

        Returns:
            EnrichSummary 汇总结果。
        """
        import time as _time

        if not self._person_dir.exists():
            logger.warning("人物目录不存在: %s", self._person_dir)
            return EnrichSummary(total=0)

        pages = self._scan_person_pages()
        if not pages:
            return EnrichSummary(total=0)

        summary = EnrichSummary(total=len(pages))
        logger.info("扫描到 %d 个人物页面", len(pages))

        # 分批搜索飞书通讯录
        name_to_page = {name: path for name, path in pages}
        all_names = list(name_to_page.keys())
        feishu_map: Dict[str, List[dict]] = {}  # name -> matched users

        for i in range(0, len(all_names), _BATCH_SIZE):
            batch = all_names[i:i + _BATCH_SIZE]
            logger.info("搜索飞书通讯录第 %d 批（%d/%d）",
                        i // _BATCH_SIZE + 1, i + len(batch), len(all_names))
            # 批间延迟，避免触发 API 频率限制
            if i > 0:
                _time.sleep(3.0)
            try:
                users = self._batch_search(batch)
            except FeishuClientError as e:
                logger.error("飞书搜索失败: %s", e)
                for name in batch:
                    summary.details.append(EnrichResult(
                        name=name, status="error", message=str(e)))
                    summary.errors += 1
                continue

            for name in batch:
                matched = [u for u in users if u.get("localized_name", "") == name]
                if matched:
                    feishu_map[name] = matched
                else:
                    # 尝试用 match_segments 模糊匹配
                    fallback = [u for u in users if any(
                        seg == name for seg in u.get("match_segments", [])
                    )]
                    if fallback:
                        feishu_map[name] = fallback
                    else:
                        summary.details.append(EnrichResult(
                            name=name, status="not_found"))
                        summary.not_found += 1

        # 逐页更新
        for name, page_path in name_to_page.items():
            if name not in feishu_map:
                continue

            matched_users = feishu_map[name]
            if len(matched_users) > 1:
                summary.details.append(EnrichResult(
                    name=name, status="ambiguous",
                    message=f"找到 {len(matched_users)} 个同名用户: "
                            f"{', '.join(u.get('department', '?') for u in matched_users)}"))
                summary.ambiguous += 1
                continue

            user = matched_users[0]
            department = user.get("department", "") or ""
            email = user.get("email", "") or ""

            if not department and not email:
                summary.details.append(EnrichResult(
                    name=name, status="not_found"))
                summary.not_found += 1
                continue

            # 检查是否需要更新
            if self._has_fields(page_path, department, email):
                summary.details.append(EnrichResult(
                    name=name, status="no_change"))
                summary.no_change += 1
                continue

            if dry_run:
                summary.details.append(EnrichResult(
                    name=name, status="updated",
                    department=department, email=email,
                    message="[DRY RUN] 将写入"))
                summary.updated += 1
                continue

            # 执行更新
            try:
                self._update_page(page_path, name, department, email)
                summary.details.append(EnrichResult(
                    name=name, status="updated",
                    department=department, email=email))
                summary.updated += 1
            except (OSError, ValueError) as e:
                summary.details.append(EnrichResult(
                    name=name, status="error", message=str(e)))
                summary.errors += 1

        logger.info("丰富完成: updated=%d, not_found=%d, ambiguous=%d, no_change=%d, errors=%d",
                    summary.updated, summary.not_found, summary.ambiguous,
                    summary.no_change, summary.errors)
        return summary

    # ── 内部方法 ──────────────────────────────────────────

    def _scan_person_pages(self) -> List[Tuple[str, Path]]:
        """扫描人物目录，返回 [(name, path), ...]。"""
        pages: List[Tuple[str, Path]] = []
        if not self._person_dir.exists():
            return pages
        for fpath in sorted(self._person_dir.iterdir()):
            if not fpath.is_file() or fpath.suffix != ".md":
                continue
            if ".bak." in fpath.name:
                continue
            # 从 frontmatter 或文件名提取 title
            name = self._extract_title(fpath)
            if name:
                pages.append((name, fpath))
        return pages

    @staticmethod
    def _extract_title(path: Path) -> str:
        """从 frontmatter 提取 title，fallback 到文件名。"""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        # 尝试 frontmatter
        m = _FRONTMATTER_RE.match(text)
        if m:
            for line in m.group(1).splitlines():
                if line.startswith("title:"):
                    raw = line.split(":", 1)[1].strip().strip("\"'")
                    if raw:
                        return raw
        # fallback: 文件名
        stem = path.stem
        if stem.startswith("人物-"):
            return stem[len("人物-"):]
        return stem

    def _batch_search(self, names: List[str], retries: int = 4) -> List[dict]:
        """批量搜索飞书通讯录，带退避重试。

        使用 --queries 并行搜索，遇到 rate_limit 时等待后重试。
        """
        import time as _time

        queries = ",".join(names)
        for attempt in range(retries):
            try:
                raw = self._client._run([
                    "contact", "+search-user",
                    "--queries", queries,
                    "--as", "user",
                ], timeout=60)
                return raw.get("data", {}).get("users", [])
            except FeishuClientError as e:
                if "rate_limit" in str(e).lower() or "frequency limit" in str(e):
                    wait = 5.0 * (2 ** attempt)
                    logger.warning("飞书 API 频率限制，等待 %.1f 秒后重试 (attempt %d/%d)",
                                   wait, attempt + 1, retries)
                    _time.sleep(wait)
                    continue
                raise
        raise FeishuClientError(f"飞书搜索重试耗尽 ({retries}次): 频率限制")

    def _has_fields(self, path: Path, department: str, email: str) -> bool:
        """检查页面 frontmatter 是否已包含目标字段。"""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return False
        fm_text = m.group(1)
        existing_dept = ""
        existing_email = ""
        for line in fm_text.splitlines():
            if line.startswith("department:"):
                existing_dept = line.split(":", 1)[1].strip().strip("\"'")
            if line.startswith("email:"):
                existing_email = line.split(":", 1)[1].strip().strip("\"'")
        return existing_dept == department and existing_email == email

    def _update_page(self, path: Path, name: str, department: str, email: str) -> None:
        """更新页面 frontmatter 中的 department 和 email 字段。

        1. 备份原文件
        2. 插入/更新 frontmatter 字段
        3. 原子写入
        """
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            logger.warning("跳过 %s: 无有效 frontmatter", name)
            return

        fm_text = m.group(1)
        body = text[m.end():]

        # 更新或插入 department
        lines = fm_text.splitlines()
        has_department = False
        has_email = False
        new_lines: List[str] = []
        for line in lines:
            if line.startswith("department:"):
                new_lines.append(f"department: {department}")
                has_department = True
            elif line.startswith("email:"):
                new_email = email or ""
                new_lines.append(f"email: {new_email}")
                has_email = True
            else:
                new_lines.append(line)

        # 在 sync 后插入（若无则追加在最后）
        if not has_department:
            # 在 updated 后插入
            inserted = False
            final_lines: List[str] = []
            for line in new_lines:
                final_lines.append(line)
                if line.startswith("updated:") and not inserted:
                    final_lines.append(f"department: {department}")
                    inserted = True
            if not inserted:
                final_lines.append(f"department: {department}")
            new_lines = final_lines

        if not has_email:
            inserted = False
            final_lines = []
            for line in new_lines:
                final_lines.append(line)
                if line.startswith("department:") and not inserted:
                    final_lines.append(f"email: {email or ''}")
                    inserted = True
            if not inserted:
                final_lines.append(f"email: {email or ''}")
            new_lines = final_lines

        new_fm = "\n".join(new_lines)
        new_content = f"---\n{new_fm}\n---\n{body}"

        # 备份（使用 .bak.enrich 后缀，避免 .md 结尾被 Nav 计入）
        bak_path = path.with_name(f"{path.stem}.bak.enrich")
        if not bak_path.exists():
            path.rename(bak_path)
        else:
            import shutil
            shutil.copy2(str(path), str(bak_path))

        # 原子写入
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        tmp_path.replace(path)
