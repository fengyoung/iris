"""双周报数据收集器 — 文件扫描、OP 文档加载、历史双周报加载。

将 AnalysisReportService 中的数据层职责独立出来，使其可以在不依赖
LLM 的情况下单独测试文件收集逻辑。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from iris.config.loader import ConfigBundle
from iris.utils.paths import resolve_source_root

import logging
logger = logging.getLogger(__name__)


class BiweeklyCollector:
    """文件收集 + OP 文档加载 + 历史双周报加载。"""

    def __init__(self, config: ConfigBundle) -> None:
        self._config = config
        self._op_text_cache: Optional[str] = None

    # ── OP 文档 ────────────────────────────────────────────────

    # 默认团队名单：文件名含「-团队名-人名-OKR」时排除（避免误取个人/团队 OKR）
    # 可通过 app.biweekly_report.team_okr_patterns 配置覆盖
    _DEFAULT_TEAM_OKR_NAMES = [
        "智能引擎组", "质检研发", "图验算法", "搜索推荐部", "价格策略",
        "大模型算法组", "推荐算法", "搜索算法", "搜推工程", "硬件专项", "软硬一体",
    ]

    def _build_team_okr_pattern(self) -> re.Pattern:
        """根据配置动态构建团队 OKR 排除正则。"""
        biweekly_cfg = self._config.app.get("biweekly_report", {})
        names = biweekly_cfg.get("team_okr_patterns", self._DEFAULT_TEAM_OKR_NAMES)
        if not names:
            # 配置为空列表时：不排除任何文件
            return re.compile(r'(?!)')  # 永不匹配
        escaped = "|".join(re.escape(n) for n in names)
        return re.compile(rf'-(?:{escaped})-[一-鿿]{{2,4}}-OKR')

    def load_op_document(self) -> str:
        """加载 SOURCE/01-目标管理/ 中最新的部门级 OP/OKR 规划文档（内存缓存）。

        按文件名中嵌入的 YYYYMMDD 日期降序排列，优先级：
        1. 含「数据智能部」且非个人/团队 OKR 的文件（部门级 OP/OKR）
        2. 目录中第一个可用文件（兜底，记录 warning）
        """
        if self._op_text_cache is not None:
            return self._op_text_cache

        team_okr_re = self._build_team_okr_pattern()
        biweekly_cfg = self._config.app.get("biweekly_report", {})
        dept_keyword = biweekly_cfg.get("dept_op_keyword", "数据智能部")

        sources = self._config.data_source.get("sources", {})
        for cfg in sources.values():
            src_path = Path(cfg.get("path", "")).resolve()
            if not src_path.exists():
                continue
            op_dir = src_path / "01-目标管理"
            if not op_dir.exists():
                continue

            # 收集部门级文件（含 dept_keyword 且非个人/团队 OKR）
            candidates: list[Path] = []
            for f in op_dir.rglob("*.md"):
                fname = f.name
                if dept_keyword not in fname:
                    continue
                if team_okr_re.search(fname):
                    continue
                candidates.append(f)

            if not candidates:
                # 兜底：取目录中所有文件，记录 warning 方便排查
                fallback_all = sorted(op_dir.rglob("*.md"), reverse=True)
                if fallback_all:
                    logger.warning(
                        "未找到含「%s」的部门级 OP 文档，兜底使用: %s",
                        dept_keyword, fallback_all[0].name,
                    )
                candidates = fallback_all

            # 按嵌入日期降序
            candidates.sort(
                key=lambda p: (
                    int(self._extract_date_from_path(p.name).strftime("%Y%m%d"))
                    if self._extract_date_from_path(p.name) else 0
                ),
                reverse=True,
            )

            for f in candidates:
                try:
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    text = parts[2].strip() if len(parts) >= 3 else text
                self._op_text_cache = text
                logger.info("  加载 OP 文档: %s", f.name)
                return self._op_text_cache

        self._op_text_cache = ""
        logger.warning("未找到 OP 规划文档（01-目标管理/*.md），Stage 0a 将返回空方向")
        return ""

    # ── 历史双周报 ─────────────────────────────────────────────

    def load_recent_biweeklies(self, since_days: int = 35) -> list[dict]:
        """加载近 N 天内所有双周报，用于多期去重。

        Returns:
            [{week, date, date_str, content, path}, ...] 按日期降序排列。
            35 天覆盖「本月 + 上月最后一期」，确保去重充分。
        """
        source_root = resolve_source_root(self._config)
        if not source_root:
            return []
        report_dir = source_root / "06-我的周报"
        if not report_dir.exists():
            return []

        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - \
                 timedelta(days=since_days)
        results: list[dict] = []
        for f in sorted(report_dir.rglob("双周报-*.md"), reverse=True):
            date_match = re.search(r'(\d{8})', f.name)
            if not date_match:
                continue
            try:
                file_date = datetime.strptime(date_match.group(1), "%Y%m%d")
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            content = re.sub(r'\n*> This report was.*$', '', content, flags=re.MULTILINE)
            week_match = re.search(r'w(\d{2})', f.name)
            week = int(week_match.group(1)) if week_match else 0
            results.append({
                "week": week,
                "date": file_date,
                "date_str": file_date.strftime("%Y.%m.%d"),
                "content": content,
                "path": str(f),
            })

        logger.info("  加载近 %d 天历史双周报: %d 份", since_days, len(results))
        return results

    def load_previous_biweekly(self) -> str:
        """加载上期双周报内容（兼容旧接口）。"""
        reports = self.load_recent_biweeklies(since_days=35)
        if not reports:
            return ""
        return reports[0]["content"][:5000]

    # ── 文件收集 ───────────────────────────────────────────────

    def collect_recent_files(self, since_date: datetime) -> list[dict]:
        """收集近两周的数据源文件。

        扫描目录由 config.app.biweekly_report.data_sources 控制，
        默认：成员周报、会议纪要、讨论思考、方案报告。
        按文件名 YYYYMMDD 过滤（fallback 到 frontmatter 日期）。
        成员周报每人只保留最新一份。
        """
        _DEFAULT_DIR_MAP = {
            "方案报告": ("03-方案报告", "方案报告"),
            "讨论思考": ("04-讨论思考", "讨论思考"),
            "会议纪要": ("05-会议纪要", "会议纪要"),
            "成员周报": ("07-成员周报", "成员周报"),
        }

        biweekly_cfg = self._config.app.get("biweekly_report", {})
        cfg_dir_map = biweekly_cfg.get("dir_map", {})
        dir_map = {**_DEFAULT_DIR_MAP, **{k: tuple(v) for k, v in cfg_dir_map.items()}}
        enabled_sources = biweekly_cfg.get("data_sources",
            ["成员周报", "会议纪要", "讨论思考", "方案报告"])

        target_dirs = []
        for src_name in enabled_sources:
            if src_name in dir_map:
                target_dirs.append(dir_map[src_name])
            else:
                logger.warning("  未知数据源: %s，已跳过", src_name)

        source_root = resolve_source_root(self._config)
        if not source_root:
            return []

        all_files = []
        for dir_name, dir_label in target_dirs:
            dir_path = source_root / dir_name
            if not dir_path.exists():
                logger.warning("  双周报数据目录不存在: %s", dir_path)
                continue
            for f in sorted(dir_path.rglob("*.md")):
                try:
                    raw_content = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                d = self._extract_date_from_path(f.name)
                if d is None:
                    d = self._extract_date_from_frontmatter(raw_content)
                if d is None or d < since_date:
                    continue

                content = raw_content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    content = parts[2].strip() if len(parts) >= 3 else content
                label = self._build_citation_label(f.name, dir_label)
                author = ""
                if dir_label == "成员周报":
                    author = self._extract_person_from_filename(f.name) or ""
                all_files.append({
                    "date": d,
                    "dir": dir_label,
                    "filename": f.name,
                    "label": label,
                    "author": author,
                    "content": content,
                    "char_count": len(content),
                })

        # 成员周报去重：同一人只保留最新一份
        all_files.sort(key=lambda x: (-x["date"].timestamp(), x["dir"]))
        seen_persons: set = set()
        deduped = []
        for f in all_files:
            if f["dir"] == "成员周报":
                person = self._extract_person_from_filename(f["filename"])
                person_key = person or f["label"].replace("周报", "").rsplit("-", 1)[0]
                if person_key in seen_persons:
                    continue
                seen_persons.add(person_key)
            deduped.append(f)

        return deduped

    # ── 静态工具（与 AnalysisReportService 保持向后兼容的引用路径）──

    @staticmethod
    def _extract_date_from_path(relative_path: str) -> Optional[datetime]:
        """从文件路径中提取日期（优先 YYYYMMDD 格式）。"""
        m = re.search(r"(\d{8})", relative_path)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            return None

    @staticmethod
    def _extract_date_from_frontmatter(content: str) -> Optional[datetime]:
        """从 Markdown frontmatter 中提取日期（fallback）。"""
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        fm = parts[1]
        m = re.search(r'date:\s*(\d{4}-\d{2}-\d{2})', fm)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        m = re.search(r'日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日', fm)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_person_from_filename(filename: str) -> Optional[str]:
        """从成员周报文件名提取人名。格式: YYYYMMDD-周报-w{week}-{name}.md"""
        m = re.match(r'\d{8}-周报-w\d{1,2}-(.+)\.md$', filename)
        if m:
            return m.group(1)
        m = re.search(r'[-—]([一-鿿]{2,4})(?:\.|$)', filename)
        return m.group(1) if m else None

    @staticmethod
    def _build_citation_label(filename: str, dir_label: str) -> str:
        """构建简化引用标签。"""
        m = re.match(r'(\d{4})(\d{2})(\d{2})', filename)
        mmdd = f"{m.group(2)}{m.group(3)}" if m else ""

        name = re.sub(r'^\d{8}-?', '', filename).replace('.md', '')

        if dir_label == "成员周报":
            person_m = re.search(r'[-—]([一-鿿]{2,3})(?:\.|$)', name)
            if person_m:
                return f"{person_m.group(1)}周报-{mmdd}"
            return f"{name[:10]}-{mmdd}"

        if dir_label in ("会议纪要", "讨论思考", "方案报告"):
            return f"{name}-{mmdd}"

        return f"{dir_label}/{name}-{mmdd}" if mmdd else f"{dir_label}/{name}"
