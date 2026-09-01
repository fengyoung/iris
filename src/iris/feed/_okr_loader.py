"""信息汇聚管道 — 从 SOURCE/01-目标管理 加载 OKR 内容。

解析 Markdown 格式的 OKR 文件，提取 O1/O2/O3 及其 KR 的结构化描述。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class KR:
    """单个关键结果。"""
    kr_id: str          # "O1-KR1"
    title: str          # "【质量】自动质检模型优化，支撑新品全量入仓…"
    short_title: str    # "【质量】自动质检模型优化"
    owner: str = ""
    content: str = ""   # 完整内容（含描述、KP 表格等）


@dataclass
class Objective:
    """单个目标。"""
    obj_id: str         # "O1"
    title: str          # "检测技术升级…"
    content: str = ""   # 完整内容（含方向描述等）
    krs: Dict[str, KR] = field(default_factory=dict)


@dataclass
class OKRDocument:
    """完整的 OKR 文档，支持按标签查询。"""

    objectives: Dict[str, Objective] = field(default_factory=dict)
    source_file: str = ""

    def get_kr(self, tag: str) -> Optional[KR]:
        """按 'O1-KR1' 格式标签查找关键结果。"""
        for obj in self.objectives.values():
            if tag in obj.krs:
                return obj.krs[tag]
        return None

    def resolve_tags(self, tags: List[str]) -> Dict[str, str]:
        """将标签列表解析为 {标签: 实际描述} 映射。

        Example:
            ["O1"] → {"O1": "检测技术升级…"}
            ["O1-KR1"] → {"O1-KR1": "【质量】自动质检模型优化…"}
            ["O1"] → 包含该 Objective 下所有 KR 描述
        """
        result: Dict[str, str] = {}
        for tag in tags:
            parts = tag.split("-", 1)
            obj_id = parts[0]
            # 尝试匹配 KR
            kr = self.get_kr(tag)
            if kr:
                result[tag] = kr.title
                continue
            # 整 Objective
            if obj_id in self.objectives:
                result[tag] = self.objectives[obj_id].title
        return result

    def to_prompt_context(self) -> str:
        """格式化为 LLM Prompt 注入文本。"""
        lines = []
        for obj_id, obj in self.objectives.items():
            lines.append(f"## {obj_id}：{obj.title}")
            for kr_id, kr in obj.krs.items():
                lines.append(f"  {kr_id}：{kr.title}")
        return "\n".join(lines) if lines else "（无可用的 OKR 文档）"


# ── 解析器 ────────────────────────────────────────────────


def _find_latest_okr_file(source_root: Path, dept_keyword: str = "") -> Optional[Path]:
    """在 SOURCE/01-目标管理 中查找最新的部门级 OKR 文件。

    规则：
    - 目录按年分（2026/ 2027/ …）
    - 文件名含部门关键词（dept_keyword，空=不过滤；生产环境从 app.biweekly_report.dept_op_keyword 配置）
    - 不含「OP」「双周」「周报」「团队」「个人」「检查」等关键词
    - 按嵌入日期降序取最新
    """
    tm_dir = source_root / "01-目标管理"
    if not tm_dir.exists():
        logger.warning("01-目标管理 目录不存在: %s", tm_dir)
        return None

    candidates: list[Path] = []
    for year_dir in sorted(tm_dir.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        for f in year_dir.glob("*.md"):
            fname = f.name
            # 部门关键词过滤（为空则不过滤）
            if dept_keyword and dept_keyword not in fname:
                continue
            # 排除非 OKR 文件
            if any(kw in fname for kw in ("OP", "双周", "周报", "团队", "个人", "检查")):
                continue
            candidates.append(f)

    if not candidates:
        logger.warning("未找到符合条件的 OKR 文档")
        return None

    candidates.sort(reverse=True)  # 文件名含日期，降序取最新
    return candidates[0]


def extract_dept_keyword(bundle) -> str:
    """从配置 bundle 提取部门关键词（app.biweekly_report.dept_op_keyword）。

    兼容两种 bundle 形态：旧式 Dict 访问（ConfigBundle）与类型安全的 ConfigBundleV2。
    """
    app_cfg = getattr(bundle, "app", None)
    if app_cfg is None:
        return ""
    if isinstance(app_cfg, dict):
        biweekly = app_cfg.get("biweekly_report", {}) or {}
    else:
        biweekly = getattr(app_cfg, "biweekly_report", None) or {}
    # 防御：配置显式写 null 时按空串处理（空=不筛选）
    return biweekly.get("dept_op_keyword", "") or ""


def _parse_okr_file(filepath: Path) -> OKRDocument:
    """解析 OKR Markdown 文件。"""
    content = filepath.read_text(encoding="utf-8")

    # 去掉 frontmatter（如果有）
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]

    doc = OKRDocument(source_file=filepath.name)
    lines = content.split("\n")

    current_obj: Optional[Objective] = None
    current_kr: Optional[KR] = None

    # 正则：## O<数字>：标题
    obj_pat = re.compile(r'^##\s+(O\d+)[：:]\s*(.*)')
    # 正则：### KR<数字>：标题
    kr_pat = re.compile(r'^###\s+(KR\d+)[：:]\s*(.*)')
    # KR Owner
    owner_pat = re.compile(r'\*\*KR Owner：\*\*\s*(.*)')

    for line in lines:
        stripped = line.strip()

        # 检测 Objective
        m = obj_pat.match(stripped)
        if m:
            if current_kr and current_obj:
                current_obj.krs[current_kr.kr_id] = current_kr
            if current_obj:
                doc.objectives[current_obj.obj_id] = current_obj
            obj_id = m.group(1)
            current_obj = Objective(obj_id=obj_id, title=m.group(2).strip(), content=stripped)
            current_kr = None
            continue

        # 检测 KR
        m = kr_pat.match(stripped)
        if m:
            if current_kr and current_obj:
                current_obj.krs[current_kr.kr_id] = current_kr
            if not current_obj:
                logger.warning("KR 出现在 O 之前: %s", stripped[:50])
                continue
            kr_id = m.group(1)
            full_title = m.group(2).strip()
            # 提取短标题（【】内 + 后续简短内容）
            short_title = full_title
            bracket_m = re.match(r'^(【[^】]+】)', full_title)
            if bracket_m:
                short_title = bracket_m.group(1)
            full_kr_id = f"{current_obj.obj_id}-{kr_id}"
            current_kr = KR(
                kr_id=full_kr_id,
                title=full_title,
                short_title=short_title,
                content=stripped,
            )
            continue

        # 检测 KR Owner
        if current_kr:
            m = owner_pat.match(stripped)
            if m:
                current_kr.owner = m.group(1).strip()
                continue
            # 追加到 KR content
            if current_kr.content:
                current_kr.content += "\n" + stripped

        # 追加到 Objective content
        if current_obj and current_obj.content:
            current_obj.content += "\n" + stripped

    # 收尾
    if current_kr and current_obj:
        current_obj.krs[current_kr.kr_id] = current_kr
    if current_obj:
        doc.objectives[current_obj.obj_id] = current_obj

    logger.info("解析 OKR 文档: %s → %d 个目标, %d 个 KR",
                filepath.name, len(doc.objectives),
                sum(len(o.krs) for o in doc.objectives.values()))
    return doc


# ── 主加载器 ──────────────────────────────────────────────


class OKRLoader:
    """OKR 加载器 — 从 SOURCE/01-目标管理 加载并缓存。"""

    def __init__(self, source_root: Optional[Path] = None, dept_keyword: str = ""):
        if source_root is not None and not isinstance(source_root, Path):
            source_root = Path(source_root)
        self._source_root = source_root
        self._dept_keyword = dept_keyword
        self._cached: Optional[OKRDocument] = None

    def set_source_root(self, source_root: Path) -> None:
        if not isinstance(source_root, Path):
            source_root = Path(source_root)
        self._source_root = source_root
        self._cached = None  # 路径变了，清缓存

    def load(self) -> Optional[OKRDocument]:
        """加载最新 OKR 文档（带缓存）。"""
        if self._cached is not None:
            return self._cached

        if self._source_root is None:
            logger.warning("未设置 SOURCE 根目录，跳过 OKR 加载")
            return None

        filepath = _find_latest_okr_file(self._source_root, self._dept_keyword)
        if not filepath:
            return None

        self._cached = _parse_okr_file(filepath)
        return self._cached

    def resolve_tags(self, tags: List[str]) -> Dict[str, str]:
        """将标签列表解析为 {标签: 实际描述}。"""
        doc = self.load()
        if not doc:
            return {t: t for t in tags}
        return doc.resolve_tags(tags)
