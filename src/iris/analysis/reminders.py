"""知识库主动提醒引擎 — 零 LLM 成本的异常信号检测。

基于 SOURCE 目录的文件名日期（YYYYMMDD 前缀）与文件 mtime，主动发现三类信号：

1. **栏目断供**（category_inactive）：某 SOURCE 分类目录超过阈值天数无新增/更新文档，
   提示该方向的知识供给可能中断（如目标管理长期没有新材料）。
2. **成员周报缺失**（weekly_report_missing）：活跃成员（近期有周报记录）的最新一份
   周报距今超过阈值天数，提示周报断档。
3. **项目停滞**（project_stalled）：项目 Wiki 页 source_fingerprint 引用的源文档
   全部超过阈值天数未更新，提示项目可能停滞或知识断供。

全部检测只读文件系统 + 已有元数据，不产生任何 LLM 调用，适合集成到 daily-start。
阈值可通过 config/app.json 的 "reminders" 段覆盖。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 文件名日期前缀：20260721-周报-w30-某人.md / 20260715-方案-xxx.md
_DATE_PREFIX_RE = re.compile(r"^(\d{8})[-_]")
# 成员周报命名：{YYYYMMDD}-周报-w{week}-{name}.md
_WEEKLY_REPORT_RE = re.compile(r"^(\d{8})-周报-w\d+-(.+)\.md$")
# SOURCE 顶级分类目录：01-目标管理 / 05-会议纪要 ...
_CATEGORY_DIR_RE = re.compile(r"^\d{2}-")

_DEFAULTS: Dict[str, Any] = {
    # 栏目断供：默认阈值 + 高频栏目单独收紧
    "category_inactive_days": 30,
    "category_overrides": {
        "05-会议纪要": 14,
        "06-我的周报": 10,
        "07-成员周报": 10,
    },
    # 成员周报：仅统计 roster_window_days 内出现过的活跃成员
    "weekly_roster_window_days": 45,
    "weekly_report_gap_days": 14,
    # 项目停滞：引用源文档全部超过该天数未更新
    "project_stall_days": 21,
    # 每类信号上限（防止输出爆炸）
    "max_signals_per_type": 15,
}


class ReminderEngine:
    """扫描知识库，产出主动提醒信号列表。"""

    def __init__(self, config):
        self._config = config
        raw = {}
        try:
            raw = (config.app or {}).get("reminders", {}) or {}
        except Exception:
            raw = {}
        self._cfg = {**_DEFAULTS, **raw}
        # overrides 为嵌套 dict，需单独合并
        overrides = dict(_DEFAULTS["category_overrides"])
        overrides.update(raw.get("category_overrides", {}) or {})
        self._cfg["category_overrides"] = overrides

    # ── 主入口 ──────────────────────────────────────────────

    def collect(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """汇总全部提醒信号。now 参数仅供测试注入。"""
        now = now or datetime.now()
        source_root = self._source_root()
        if source_root is None:
            return {"status": "skipped", "reason": "source_root_unavailable",
                    "signal_count": 0, "signals": []}

        signals: List[Dict[str, Any]] = []
        signals.extend(self._check_category_activity(source_root, now))
        signals.extend(self._check_weekly_reports(source_root, now))
        signals.extend(self._check_project_stall(source_root, now))
        signals.sort(key=lambda s: -int(s.get("days", 0)))
        return {
            "status": "ok",
            "generated_at": now.strftime("%Y-%m-%d %H:%M"),
            "signal_count": len(signals),
            "signals": signals,
        }

    # ── 信号 1：栏目断供 ─────────────────────────────────────

    def _check_category_activity(self, source_root: Path, now: datetime) -> List[Dict[str, Any]]:
        default_days = int(self._cfg["category_inactive_days"])
        overrides: Dict[str, int] = self._cfg["category_overrides"]
        limit = int(self._cfg["max_signals_per_type"])
        signals: List[Dict[str, Any]] = []
        try:
            subdirs = sorted(p for p in source_root.iterdir()
                             if p.is_dir() and _CATEGORY_DIR_RE.match(p.name))
        except OSError as exc:
            logger.warning("提醒引擎无法读取 SOURCE 目录: %s", exc)
            return []
        for subdir in subdirs:
            latest = self._latest_doc_date(subdir)
            if latest is None:
                continue  # 空目录不告警（可能是刚建的栏目）
            threshold = int(overrides.get(subdir.name, default_days))
            days = (now.date() - latest.date()).days
            if days >= threshold:
                signals.append({
                    "type": "category_inactive",
                    "target": subdir.name,
                    "days": days,
                    "threshold": threshold,
                    "detail": f"「{subdir.name}」已 {days} 天无文档更新（阈值 {threshold} 天）",
                })
        return signals[:limit]

    # ── 信号 2：成员周报缺失 ─────────────────────────────────

    def _check_weekly_reports(self, source_root: Path, now: datetime) -> List[Dict[str, Any]]:
        roster_window = int(self._cfg["weekly_roster_window_days"])
        gap_days = int(self._cfg["weekly_report_gap_days"])
        limit = int(self._cfg["max_signals_per_type"])
        reports_dir = self._find_category_dir(source_root, "07-")
        if reports_dir is None:
            return []
        last_seen: Dict[str, datetime] = {}
        for path in reports_dir.rglob("*.md"):
            match = _WEEKLY_REPORT_RE.match(path.name)
            if not match:
                continue
            date = _parse_yyyymmdd(match.group(1))
            if date is None:
                continue
            name = match.group(2).strip()
            if name and (name not in last_seen or date > last_seen[name]):
                last_seen[name] = date
        signals: List[Dict[str, Any]] = []
        for name, last_date in sorted(last_seen.items()):
            days = (now.date() - last_date.date()).days
            # 超出 roster 窗口视为已离开统计范围（离职/转岗），不告警
            if gap_days <= days <= roster_window:
                signals.append({
                    "type": "weekly_report_missing",
                    "target": name,
                    "days": days,
                    "threshold": gap_days,
                    "detail": f"{name} 最近一份周报是 {last_date.strftime('%Y-%m-%d')}（已 {days} 天）",
                })
        signals.sort(key=lambda s: -int(s["days"]))
        return signals[:limit]

    # ── 信号 3：项目停滞 ─────────────────────────────────────

    def _check_project_stall(self, source_root: Path, now: datetime) -> List[Dict[str, Any]]:
        stall_days = int(self._cfg["project_stall_days"])
        limit = int(self._cfg["max_signals_per_type"])
        project_dir = self._project_wiki_dir()
        if project_dir is None:
            return []
        from iris.wiki.discovery_utils import parse_wiki_source_fingerprint
        signals: List[Dict[str, Any]] = []
        for page in sorted(project_dir.glob("*.md")):
            fingerprint = parse_wiki_source_fingerprint(str(page))
            if not fingerprint:
                continue  # 无指纹（旧页面）无法廉价判定，跳过
            freshest: Optional[datetime] = None
            for rel_path in fingerprint:
                doc_date = self._doc_date(source_root / rel_path)
                if doc_date and (freshest is None or doc_date > freshest):
                    freshest = doc_date
            if freshest is None:
                continue  # 引用源全部缺失，属于断链问题，交给 wiki-lint
            days = (now.date() - freshest.date()).days
            if days >= stall_days:
                signals.append({
                    "type": "project_stalled",
                    "target": page.stem,
                    "days": days,
                    "threshold": stall_days,
                    "detail": f"「{page.stem}」引用的源文档最近更新于 {freshest.strftime('%Y-%m-%d')}（已 {days} 天）",
                })
        signals.sort(key=lambda s: -int(s["days"]))
        return signals[:limit]

    # ── 工具方法 ─────────────────────────────────────────────

    def _source_root(self) -> Optional[Path]:
        try:
            ds = self._config.data_source or {}
            default = ds.get("default_source", "")
            path_str = (ds.get("sources", {}).get(default, {}) or {}).get("path", "")
            if not path_str:
                return None
            root = Path(path_str).expanduser()
            return root if root.exists() else None
        except Exception:
            return None

    def _project_wiki_dir(self) -> Optional[Path]:
        try:
            wiki = self._config.wiki
            if not wiki or not wiki.get("wiki_root"):
                return None
            from iris.wiki._constants import get_wiki_dir
            project_dir = Path(wiki["wiki_root"]) / get_wiki_dir("project")
            return project_dir if project_dir.exists() else None
        except Exception:
            return None

    @staticmethod
    def _find_category_dir(source_root: Path, prefix: str) -> Optional[Path]:
        try:
            for p in source_root.iterdir():
                if p.is_dir() and p.name.startswith(prefix):
                    return p
        except OSError:
            pass
        return None

    def _latest_doc_date(self, directory: Path) -> Optional[datetime]:
        latest: Optional[datetime] = None
        try:
            for path in directory.rglob("*.md"):
                date = self._doc_date(path)
                if date and (latest is None or date > latest):
                    latest = date
        except OSError as exc:
            logger.warning("提醒引擎遍历目录失败 %s: %s", directory, exc)
        return latest

    @staticmethod
    def _doc_date(path: Path) -> Optional[datetime]:
        """文档日期：优先文件名 YYYYMMDD 前缀，其次文件 mtime。"""
        match = _DATE_PREFIX_RE.match(path.name)
        if match:
            date = _parse_yyyymmdd(match.group(1))
            if date is not None:
                return date
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None


def _parse_yyyymmdd(text: str) -> Optional[datetime]:
    # strptime 对不足 8 位的输入宽容解析（"2026072" → 2026-07-02），需先严格校验
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
