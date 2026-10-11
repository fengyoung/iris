"""ASR 覆盖审计 —— ``asr-audit`` 命令与 ``daily-start`` 第 7 阶段共用同一实现。

历史上这里是**两份实现**：CLI 的 ``asr-audit``（读 ``bundle.root/output``）与
daily-start 的 ``_daily_asr_audit``（读 CWD 相对的 ``Path("output")``），字段与
基准各不相同。2026-09-20 修 ``output/asr-modify/`` 那个「审计长期报告陈旧值」
的 bug 时只修了 CLI 那份，daily-start 那份继续漂——同一条链路两个真相源，注定
再次分家。现收敛为本模块的单一实现。

纯本地计算，零 LLM 成本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifacts import asr_bak_dir, latest_artifact
from .coverage import (
    analyze_coverage,
    analyze_dict_quality,
    render_coverage_text,
    render_dict_quality_text,
)
from ._types import AsrTerm

#: 产物家族 glob（与 build-asr-prompt 的写盘点一致）
HOTWORDS_GLOB = "asr-hotwords-*.txt"
REPLACE_DICT_GLOB = "asr-replace-dict-*.json"


@dataclass
class AuditResult:
    """审计结果：payload 供 JSON/渲染消费，text 供 --pretty 人类阅读。"""

    payload: Dict[str, Any]
    text: str = ""

    def __iter__(self):
        # 便于 ``payload, text = result`` 解包
        yield self.payload
        yield self.text


@dataclass
class _Sources:
    hotwords_file: Optional[Path] = None
    replace_dict_file: Optional[Path] = None
    hotwords: List[str] = field(default_factory=list)
    replace_map: Dict[str, str] = field(default_factory=dict)


def _load_sources(bak_dir: Path) -> _Sources:
    """读最新热词与替换词典（存档目录，非生效点）。"""
    src = _Sources()
    src.hotwords_file = latest_artifact(bak_dir, HOTWORDS_GLOB)
    if src.hotwords_file is not None:
        src.hotwords = [
            line.strip()
            for line in src.hotwords_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    src.replace_dict_file = latest_artifact(bak_dir, REPLACE_DICT_GLOB)
    if src.replace_dict_file is not None:
        import json

        try:
            data = json.loads(src.replace_dict_file.read_text(encoding="utf-8"))
            src.replace_map = data.get("replace_map", {}) or {}
        except (OSError, ValueError):
            # 存档损坏不该让审计整体失败：退化为「无词典」并继续覆盖分析。
            src.replace_map = {}
    return src


def _terms_from_replace_map(replace_map: Dict[str, str]) -> List[AsrTerm]:
    return [AsrTerm(term=right, category="domain_term", context="", mis_asr=[wrong])
            for wrong, right in replace_map.items()]


def run_asr_audit(bundle) -> AuditResult:
    """审计最新存档产物对当前 Wiki 的覆盖情况。

    无 Wiki 配置或无热词产物时返回 ``status: skipped``（不是「覆盖 0%」——
    「没有产物」与「产物不合格」必须区分，否则审计会把未运行渲染成不合格）。
    """
    wiki_root_str = (bundle.wiki or {}).get("wiki_root", "") if bundle.wiki else ""
    if not wiki_root_str:
        return AuditResult({"status": "skipped", "reason": "Wiki 未配置"})
    wiki_root = Path(wiki_root_str)
    if not wiki_root.is_dir():
        return AuditResult({"status": "skipped", "reason": "Wiki 目录不存在"})

    from iris.wiki.context_loader import WikiContextLoader

    bak_dir = asr_bak_dir(getattr(bundle, "root", None))
    sources = _load_sources(bak_dir)
    if sources.hotwords_file is None:
        return AuditResult({
            "status": "skipped",
            "reason": f"未找到热词文件（{bak_dir}），请先运行 build-asr-prompt",
        })

    pages = WikiContextLoader(wiki_root).load_pages(
        sort_order=["person", "concept", "project", "domain"])
    cov = analyze_coverage(sources.hotwords, pages)
    dict_report = analyze_dict_quality(_terms_from_replace_map(sources.replace_map))

    # 覆盖缺口 ⇒ 重新生成可能补上；噪音/超长词 ⇒ 需要人工清理，重新生成不一定
    # 解决。两者混在一个 needs_update 里，就分不出「该重跑」还是「该手工删」。
    issues: List[str] = []
    if cov.persons_missing:
        issues.append(f"{len(cov.persons_missing)} 人未覆盖")
    if cov.projects_missing:
        issues.append(f"{len(cov.projects_missing)} 项目未覆盖")
    if cov.concepts_missing:
        issues.append(f"{len(cov.concepts_missing)} 概念未覆盖")
    warnings: List[str] = []
    if cov.noise_words:
        warnings.append(f"{len(cov.noise_words)} 个噪音词")
    if cov.long_words:
        warnings.append(f"{len(cov.long_words)} 个超长词")

    payload: Dict[str, Any] = {
        "status": "ok",
        "hotword_count": cov.hotword_count,
        "persons": f"{cov.persons_covered}/{cov.persons_total}",
        "projects": f"{cov.projects_covered}/{cov.projects_total}",
        "concepts": f"{cov.concepts_covered}/{cov.concepts_total}",
        "slot_efficiency": f"{cov.slot_efficiency:.1%}",
        "noise_count": len(cov.noise_words),
        "issues": issues,
        "warnings": warnings,
        "needs_update": bool(issues),
        "dict_quality": {
            "total_rules": dict_report.total_rules,
            "format_errors": len(dict_report.format_errors),
            "conflicts": len(dict_report.conflicting_pairs),
        },
        "sources": {
            "hotwords_file": str(sources.hotwords_file),
            "replace_dict_file": str(sources.replace_dict_file) if sources.replace_dict_file else "",
        },
    }
    if issues:
        payload["suggestion"] = "建议运行 build-asr-prompt --deploy 更新 ASR 热词"

    text = "\n".join([render_coverage_text(cov), "", render_dict_quality_text(dict_report)])
    return AuditResult(payload, text)
