"""批量 Frontmatter 补全处理器。

对 SOURCE 目录下的 Markdown 文档批量注入 YAML frontmatter 元数据，
支持正则快速通道（date/title/type）+ LLM 深度通道（participants/author 等），
并可选注入 [[wikilink]] 交叉链接。

用法:
    from iris.core.frontmatter_batch import FrontmatterBatchProcessor, BatchConfig

    config = BatchConfig(use_llm=True, use_wikilink=True)
    processor = FrontmatterBatchProcessor(llm_service, wiki_root, config)
    result = processor.process_directory(source_dir / "04-讨论思考", dry_run=True)
    print(f"成功 {result.success}, 跳过 {result.skipped}, 失败 {result.failed}")
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.core.frontmatter import (
    DOC_TYPES,
    has_frontmatter,
    inject_frontmatter,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

# ── 文件名日期模式 ──────────────────────────────────────────
# 匹配 YYYYMMDD 开头的文件名（支持 YYYYMMDD- / YYYYMMDD_ 前缀）
_FILENAME_DATE_RE = re.compile(r"^(\d{8})[-_]")

# ── 类别字段配置 ───────────────────────────────────────────
# key: SOURCE 子目录名, value: (type值, [LLM 提取字段及中文描述])

CATEGORY_FIELDS: Dict[str, Tuple[str, Dict[str, str]]] = {
    "01-目标管理": ("okr", {
        "period": "OKR周期，如 2026Q3",
        "author": "作者/负责人姓名",
        "source_url": "来源飞书文档URL",
    }),
    "02-部门管理": ("dept_mgmt", {
        "source": "数据来源，如「飞书表格《2026-核心Leader盘点》」",
        "author": "作者/整理人",
    }),
    "03-方案报告": ("proposal", {
        "author": "作者姓名",
        "version": "版本号，如 v1.0",
    }),
    "04-讨论思考": ("discussion", {
        "participants": "参会人员姓名列表",
        "duration": "时长，如 37分",
    }),
    "05-会议纪要": ("meeting_minutes", {
        "participants": "参会人员姓名列表",
        "duration": "时长，如 1小时1分",
        "source_file": "来源转录文件名",
    }),
    "06-我的周报": ("my_weekly", {
        "period": "周报周期，如 2026.04.13～2026.04.26",
    }),
    "07-成员周报": ("weekly_report", {
        "author": "周报作者姓名",
        "email": "作者邮箱地址",
    }),
    "08-参考资料": ("reference", {
        "author": "作者/演讲者",
        "event": "来源活动/会议/峰会名称",
        "source_url": "来源URL",
    }),
    "09-工作简报": ("work_briefing", {
        "source": "信息来源，如群聊名称或飞书文档",
    }),
}


# ── LLM 提取 Prompt 模板 ──────────────────────────────────

_EXTRACTION_PROMPT = """从 Markdown 文档中提取元数据字段。只输出 JSON，不要解释。

文档类型：{doc_type_label}
文件：{file_path}

提取字段：
{field_specs}

规则：
1. 信息不存在时值为 null
2. 日期格式 YYYY-MM-DD
3. 多值用列表
4. 输出必须是合法 JSON 对象，一行即可，禁止输出解释、推理过程、markdown 标记

文档：
---
{body}
---

JSON:"""


# ── 数据类 ────────────────────────────────────────────────


@dataclass
class BatchConfig:
    """批量处理配置。

    Attributes:
        use_llm: 是否使用 LLM 提取深度字段（False 时仅 regex）
        use_wikilink: 是否注入 [[wikilink]] 交叉链接
        force_overwrite: 是否覆盖已有 frontmatter（默认跳过）
        no_backup: 是否跳过备份（默认会备份）
        llm_max_tokens: LLM 提取最大输出 token
    """
    use_llm: bool = True
    use_wikilink: bool = False
    force_overwrite: bool = False
    no_backup: bool = False
    llm_max_tokens: int = 512


@dataclass
class FileResult:
    """单文件处理结果。"""
    path: str
    status: str  # "injected" | "skipped" | "failed"
    fields_injected: Dict[str, object] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class BatchResult:
    """批量处理结果。"""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    backup_path: Optional[str] = None
    per_file: List[FileResult] = field(default_factory=list)


# ── 处理器 ────────────────────────────────────────────────


class FrontmatterBatchProcessor:
    """批量 Frontmatter 补全处理器。

    对 SOURCE 目录下的 Markdown 文件批量注入 frontmatter 元数据，
    支持正则提取 + LLM 深度提取 + wikilink 注入三阶段流水线。
    """

    def __init__(self, llm, wiki_root: str, config: Optional[BatchConfig] = None):
        """初始化处理器。

        Args:
            llm: LLMService 实例（仅 use_llm=True 时需要）
            wiki_root: Wiki 根目录路径（仅 use_wikilink=True 时需要）
            config: 批量处理配置，默认全开
        """
        self._llm = llm
        self._wiki_root = wiki_root
        self._config = config or BatchConfig()
        self._wikilink_injector: Any = None  # 懒初始化

    # ── 公共 API ──────────────────────────────────────────

    def process_directory(
        self, dir_path: Path, dry_run: bool = False
    ) -> BatchResult:
        """处理一个 SOURCE 子目录下的所有 .md 文件（递归）。

        Args:
            dir_path: SOURCE 子目录路径（如 SOURCE/04-讨论思考）
            dry_run: True 时仅计算 diff 不写入磁盘

        Returns:
            BatchResult 含处理统计和逐文件详情
        """
        category = dir_path.name  # e.g. "04-讨论思考"
        if category not in CATEGORY_FIELDS:
            logger.warning("未识别的目录 %s，跳过", category)
            return BatchResult()

        md_files = sorted(dir_path.rglob("*.md"))
        if not md_files:
            logger.info("%s: 无 .md 文件，跳过", category)
            return BatchResult()

        result = BatchResult(total=len(md_files))

        # ── 备份 ─────────────────────────────────────────
        backup_path: Optional[Path] = None
        if not dry_run and not self._config.no_backup:
            backup_path = self.backup_directory(dir_path)
            result.backup_path = str(backup_path)

        # ── 逐文件处理 ───────────────────────────────────
        for md_file in md_files:
            try:
                fr = self.process_file(md_file, dry_run=dry_run)
            except Exception as exc:
                fr = FileResult(
                    path=str(md_file),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            result.per_file.append(fr)
            if fr.status == "injected":
                result.success += 1
            elif fr.status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1

        logger.info(
            "%s: 总计 %d, 注入 %d, 跳过 %d, 失败 %d%s",
            category,
            result.total,
            result.success,
            result.skipped,
            result.failed,
            f" (备份: {backup_path})" if backup_path else "",
        )
        return result

    def process_file(self, file_path: Path, dry_run: bool = False) -> FileResult:
        """处理单个 .md 文件。

        流水线：读 → 幂等检查 → 正则提取 → LLM 提取 → inject frontmatter → wikilink → 写回

        Args:
            file_path: .md 文件绝对路径
            dry_run: True 时返回结果但不写入磁盘

        Returns:
            FileResult
        """
        # ── Step 1: 读取 ──────────────────────────────────
        raw = file_path.read_text(encoding="utf-8")

        # ── Step 2: 判定类别 ─────────────────────────────
        category = self._infer_category(file_path)
        if category is None or category not in CATEGORY_FIELDS:
            return FileResult(path=str(file_path), status="skipped")

        # ── Step 3: 幂等检查 ─────────────────────────────
        if has_frontmatter(raw) and not self._config.force_overwrite:
            return FileResult(path=str(file_path), status="skipped")

        # ── Step 4: 正则提取 ─────────────────────────────
        fields = self._extract_by_regex(raw, file_path, category)

        # ── Step 5: LLM 提取（按需） ─────────────────────
        type_value, llm_fields_spec = CATEGORY_FIELDS[category]
        if self._config.use_llm and llm_fields_spec:
            body = raw
            if has_frontmatter(raw):
                _, body = parse_frontmatter(raw)
            try:
                llm_fields = self._extract_by_llm(
                    body, category, type_value, llm_fields_spec, file_path
                )
                # LLM 字段不覆盖已有值
                for k, v in llm_fields.items():
                    if v is not None and (k not in fields or not fields[k]):
                        fields[k] = v
            except Exception as exc:
                logger.warning("LLM 提取失败 %s: %s", file_path.name, exc)

        if not fields:
            return FileResult(path=str(file_path), status="skipped")

        # ── Step 6: 注入 frontmatter ─────────────────────
        body = raw
        if has_frontmatter(raw):
            _, body = parse_frontmatter(raw)
        content = inject_frontmatter(body, fields)

        # ── Step 7: 注入 wikilink ────────────────────────
        if self._config.use_wikilink and self._wiki_root:
            try:
                if self._wikilink_injector is None:
                    from iris.wiki.wikilink_injector import WikilinkInjector
                    self._wikilink_injector = WikilinkInjector(self._wiki_root)
                content = self._wikilink_injector.inject(content)
            except Exception as exc:
                logger.debug("wikilink 注入跳过 %s: %s", file_path.name, exc)

        # ── Step 8: 写回 ─────────────────────────────────
        if dry_run:
            logger.info("(dry-run) %s: %s", file_path.name, fields)
        else:
            file_path.write_text(content, encoding="utf-8")

        return FileResult(
            path=str(file_path),
            status="injected",
            fields_injected=fields,
        )

    # ── 备份与恢复 ──────────────────────────────────────

    def backup_directory(self, dir_path: Path) -> Path:
        """备份目录下所有 .md 文件。

        备份路径: {SOURCE}/_frontmatter_backup/{timestamp}/{dir_name}/
        保持原始目录结构。

        Args:
            dir_path: 要备份的 SOURCE 子目录

        Returns:
            备份目标路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = dir_path.parent / "_frontmatter_backup" / timestamp
        backup_dir = backup_root / dir_path.name
        backup_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for md_file in dir_path.rglob("*.md"):
            rel = md_file.relative_to(dir_path)
            target = backup_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(md_file, target)
            count += 1

        logger.info("已备份 %s → %s (%d 文件)", dir_path.name, backup_dir, count)
        return backup_root

    @staticmethod
    def list_backups(source_root: Path) -> List[Path]:
        """列出所有备份目录。

        Args:
            source_root: SOURCE 根目录

        Returns:
            备份目录路径列表（按时间倒序）
        """
        backup_root = source_root / "_frontmatter_backup"
        if not backup_root.exists():
            return []
        return sorted(backup_root.iterdir(), reverse=True)

    @staticmethod
    def restore_directory(source_root: Path, backup_timestamp: str) -> int:
        """从指定备份恢复所有文件。

        Args:
            source_root: SOURCE 根目录
            backup_timestamp: 备份时间戳目录名（如 20260731_140000）

        Returns:
            恢复的文件数
        """
        backup_root = source_root / "_frontmatter_backup" / backup_timestamp
        if not backup_root.exists():
            raise FileNotFoundError(f"备份目录不存在: {backup_root}")

        count = 0
        for sub_dir in backup_root.iterdir():
            if not sub_dir.is_dir():
                continue
            for backup_file in sub_dir.rglob("*.md"):
                rel = backup_file.relative_to(backup_root)
                target = source_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, target)
                count += 1

        logger.info("已从 %s 恢复 %d 个文件", backup_timestamp, count)
        return count

    @staticmethod
    def remove_backup(source_root: Path, backup_timestamp: str) -> None:
        """删除指定备份目录。

        Args:
            source_root: SOURCE 根目录
            backup_timestamp: 备份时间戳目录名
        """
        backup_dir = source_root / "_frontmatter_backup" / backup_timestamp
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
            logger.info("已删除备份: %s", backup_timestamp)

    # ── 内部：正则提取 ──────────────────────────────────

    def _extract_by_regex(
        self, raw: str, file_path: Path, category: str
    ) -> Dict[str, object]:
        """从文件名和正文中正则提取基础字段。

        提取: date（文件名）、title（首个 # 标题）、type（目录映射）、updated。
        """
        fields: Dict[str, object] = {}

        # type
        if category in CATEGORY_FIELDS:
            type_key, _ = CATEGORY_FIELDS[category]
            fields["type"] = DOC_TYPES.get(type_key, type_key)

        # date
        date_str = self._extract_date_from_filename(file_path.name)
        if date_str:
            fields["date"] = date_str

        # title
        title = self._extract_title(raw)
        if title:
            fields["title"] = title

        # updated
        fields["updated"] = datetime.now().strftime("%Y-%m-%d")

        # ── 正则尝试提取目录特有字段 ────────────────────
        if category == "07-成员周报":
            author = self._extract_weekly_author(raw, file_path)
            if author:
                fields["author"] = author
            email = self._extract_weekly_email(raw)
            if email:
                fields["email"] = email

        elif category == "06-我的周报":
            period = self._extract_period(raw)
            if period:
                fields["period"] = period

        elif category in ("04-讨论思考", "05-会议纪要"):
            participants = self._extract_participants(raw)
            if participants:
                fields["participants"] = participants

        return fields

    # ── 内部：LLM 提取 ──────────────────────────────────

    def _extract_by_llm(
        self,
        body: str,
        category: str,
        type_value: str,
        llm_fields_spec: Dict[str, str],
        file_path: Path,
    ) -> Dict[str, object]:
        """使用 LLM 从正文中提取类别特有字段。

        Args:
            body: Markdown 正文（可能已去除 frontmatter）
            category: 目录名
            type_value: 类别 type 键
            llm_fields_spec: 字段名 → 中文描述
            file_path: 文件路径（用于 prompt 上下文）

        Returns:
            提取的字段字典（仅含 LLM 成功提取的非空字段）
        """
        doc_type_label = DOC_TYPES.get(type_value, type_value)
        field_specs = "\n".join(
            f"- {name}: {desc}" for name, desc in llm_fields_spec.items()
        )
        prompt = _EXTRACTION_PROMPT.format(
            doc_type_label=doc_type_label,
            file_path=str(file_path),
            field_specs=field_specs,
            body=body[:6000],  # 截断，元数据通常在前部
        )

        result = self._llm.generate(
            prompt=prompt,
            route_context={
                "input_type": "text",
                "task_type": "extraction",
                "complexity": "simple",
            },
            temperature=0,
            max_tokens=self._config.llm_max_tokens,
            use_cache=False,  # 批处理每文件仅一次调用，无需缓存
        )

        raw_text = result.text.strip()
        if not raw_text:
            logger.debug("LLM 返回空响应 %s", file_path.name)
            return {}
        # 剥离可能的 markdown 代码块包裹
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)
        # 尝试提取 JSON 对象（处理 LLM 在 JSON 前后附加文字的情况）
        # 查找第一个 { 到最后一个 } 的区间，尝试解析
        json_start = raw_text.find("{")
        json_end = raw_text.rfind("}")
        if json_start >= 0 and json_end > json_start:
            candidate = raw_text[json_start:json_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        # 最终尝试原文本
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            logger.debug("LLM 返回非 JSON 响应 %s: %s...", file_path.name, raw_text[:100])
            return {}

    # ── 静态提取器 ──────────────────────────────────────

    @staticmethod
    def _extract_date_from_filename(name: str) -> Optional[str]:
        """从文件名提取日期。例如 20260624-xxx.md → 2026-06-24。"""
        m = _FILENAME_DATE_RE.match(name)
        if not m:
            return None
        d = m.group(1)
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return None

    @staticmethod
    def _extract_title(raw: str) -> str:
        """从正文首个 # 标题行提取。"""
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                title = stripped[2:].strip()
                # 去掉可能的开头「会议纪要 - 」等前缀（保留完整标题）
                return title
        return ""

    @staticmethod
    def _extract_period(raw: str) -> str:
        """从我的周报中提取时间周期。模式: *时间周期：2026.04.13～2026.04.26*"""
        m = re.search(r"时间周期[：:]\s*([\d.]+[～~][\d.]+)", raw)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_participants(raw: str) -> List[str]:
        """从正文中提取参会人员列表。"""
        names: List[str] = []
        # 优先匹配「## 参会人员」块
        section_m = re.search(
            r"#{1,3}\s*参会人员[：:]*\s*\n(.*?)(?=\n#|\n\n##|\Z)",
            raw, re.DOTALL,
        )
        section = section_m.group(1) if section_m else raw[:2000]
        # 从行中提取名字
        for line in section.splitlines():
            stripped = line.strip()
            # 匹配 **名字** 格式（粗体）
            for m in re.finditer(r"\*\*([^*]+)\*\*", line):
                name = m.group(1).strip()
                if name and len(name) <= 10 and not name.startswith("参会"):
                    names.append(name)
            # 匹配 * 名字（...）或 - 名字（...） 格式
            m2 = re.match(r"[\*\-]\s*(\S+?)[（(]", stripped)
            if m2 and not names:
                name = m2.group(1).strip()
                if name and 2 <= len(name) <= 10 and not name.startswith("参会"):
                    names.append(name)
            # 匹配「张三、赵六」顿号分隔格式（非列表行）
            if not names and "、" in line and not stripped.startswith(("#", "-", "|", "*")):
                for part in line.split("、"):
                    part = part.strip().rstrip(",")
                    if len(part) <= 5:
                        names.append(part)
        return names

    @staticmethod
    def _extract_weekly_author(raw: str, file_path: Path) -> str:
        """从周报中提取作者。

        优先级: frontmatter author > 正文「发件人」行 > 文件名推断。
        """
        # 从正文「发件人」行提取
        m = re.search(r"发件人[：:]\s*(\S+)", raw)
        if m:
            return m.group(1).split("<")[0].strip()
        # 从文件名推断: 20260725-周报-w30-赵六.md
        parts = file_path.stem.split("-")
        if len(parts) >= 4:
            return parts[-1]
        return ""

    @staticmethod
    def _extract_weekly_email(raw: str) -> str:
        """从周报中提取邮箱。"""
        m = re.search(r"<([^>]+@[^>]+)>", raw)
        return m.group(1) if m else ""

    @staticmethod
    def _infer_category(file_path: Path) -> Optional[str]:
        """从文件路径推断所属 SOURCE 类别目录。

        文件路径形如 .../SOURCE/04-讨论思考/202606/xxx.md
        逐级向上查找匹配的类别目录名。
        """
        for parent in file_path.parents:
            if parent.name in CATEGORY_FIELDS:
                return parent.name
        return None
