"""飞书通讯录人物信息丰富器 — 从飞书组织架构补充人物 Wiki 页面的部门和邮箱信息。

用法:
    enricher = PersonEnricher(bundle)
    result = enricher.enrich(dry_run=False)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from iris.config.loader import ConfigBundle
from iris.feishu.client import FeishuClient, FeishuClientError
from .searcher import parse_frontmatter, FRONTMATTER_RE

logger = logging.getLogger(__name__)

# 批量查询每批最多 names 数（lark-cli --queries 无硬性限制，保守分批）
_BATCH_SIZE = 10
# 批间基础延迟（秒），自适应增长
_BASE_BATCH_DELAY = 3.0


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

        # 第一步：预扫描 frontmatter，只对缺少部门/邮箱的人名发起 API 查询
        name_to_page = {name: path for name, path in pages}
        names_need_enrich = []
        for name, path in name_to_page.items():
            if self._needs_enrichment(path):
                names_need_enrich.append(name)
            else:
                summary.details.append(EnrichResult(
                    name=name, status="no_change"))
                summary.no_change += 1

        if names_need_enrich:
            logger.info("需要飞书查询: %d 人（已跳过 %d 人）",
                        len(names_need_enrich), len(pages) - len(names_need_enrich))
        else:
            logger.info("所有人均无需更新，跳过飞书 API 查询")
            return summary

        # 第二步：分批搜索飞书通讯录（仅针对需要丰富的人）
        feishu_map: Dict[str, List[dict]] = {}  # name -> matched users
        batch_delay = _BASE_BATCH_DELAY  # 自适应批间延迟

        for i in range(0, len(names_need_enrich), _BATCH_SIZE):
            batch = names_need_enrich[i:i + _BATCH_SIZE]
            logger.info("搜索飞书通讯录第 %d 批（%d/%d）",
                        i // _BATCH_SIZE + 1, i + len(batch), len(names_need_enrich))
            # 自适应批间延迟
            if i > 0:
                _time.sleep(batch_delay)
            try:
                users = self._batch_search(batch)
                # 成功 → 逐步恢复延迟（最多恢复到基础值）
                batch_delay = max(_BASE_BATCH_DELAY, batch_delay - 1.0)
            except FeishuClientError as e:
                logger.error("飞书搜索失败: %s", e)
                for name in batch:
                    summary.details.append(EnrichResult(
                        name=name, status="error", message=str(e)))
                    summary.errors += 1
                # 失败 → 增大后续批间延迟
                batch_delay = min(batch_delay * 2, 30.0)
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

        # 第三步：逐页写入（只处理有飞书匹配结果的）
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

            # 检查是否需要更新（飞书数据可能与本地一致）
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
        fm, _ = parse_frontmatter(text)
        if "title" in fm:
            return fm["title"]
        # fallback: 文件名
        stem = path.stem
        if stem.startswith("人物-"):
            return stem[len("人物-"):]
        return stem

    @staticmethod
    def _needs_enrichment(path: Path) -> bool:
        """判断人物页面是否需要从飞书补充部门/邮箱。

        只检查 frontmatter 是否有非空的 department 和 email 字段，
        有则跳过飞书 API 查询（已丰富完成）。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return True  # 读不到就当需要
        fm, _ = parse_frontmatter(text)
        has_dept = bool(fm.get("department", ""))
        has_email = bool(fm.get("email", ""))
        return not (has_dept and has_email)

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
        fm, _ = parse_frontmatter(text)
        return fm.get("department", "").strip("\"'") == department and fm.get("email", "").strip("\"'") == email

    def _update_page(self, path: Path, name: str, department: str, email: str) -> None:
        """更新页面 frontmatter 中的 department 和 email 字段。

        1. 备份原文件
        2. 插入/更新 frontmatter 字段
        3. 原子写入
        """
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            logger.warning("跳过 %s: 无有效 frontmatter", name)
            return

        fm_text = m.group(1)
        body = text[m.end():]

        # 更新或插入 department / email。
        # v3.28.1：新值为空时保留原行不动——页面缺 department 但已有手工 email 时，
        # 飞书返回空 email 曾把已有值清空覆盖（email 是人工排歧过的关键字段）。
        lines = fm_text.splitlines()
        has_department = False
        has_email = False
        new_lines: List[str] = []
        for line in lines:
            if line.startswith("department:"):
                new_lines.append(f"department: {department}" if department else line)
                has_department = True
            elif line.startswith("email:"):
                new_lines.append(f"email: {email}" if email else line)
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

        # 备份（使用 .bak.enrich 后缀，避免 .md 结尾被 Nav 计入）。
        # v3.28.1：已有备份时不再覆盖——旧逻辑二次 enrich 会用当前内容
        # 覆盖首次备份，导致最原始的人工版本永久丢失。备份只保留最早版本。
        bak_path = path.with_name(f"{path.stem}.bak.enrich")
        if not bak_path.exists():
            import shutil
            shutil.copy2(str(path), str(bak_path))

        # 原子写入
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        tmp_path.replace(path)
