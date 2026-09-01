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
# 中英/数字边界切分：XRay手机拆修检测 → ["XRay", "手机拆修检测"]
_TOKEN_SPLIT_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")
# SOURCE 顶级分类目录：01-目标管理 / 05-会议纪要 ...
_CATEGORY_DIR_RE = re.compile(r"^\d{2}-")
# 项目名常见业务后缀：匹配源文档时逐级剥离（项目-XXX项目 → XXX）
_PROJECT_NAME_SUFFIXES = (
    "项目", "方案", "规则", "计划", "机制", "体系", "框架", "算法",
    "讨论", "推进", "进展", "报告", "盘点", "定义", "里程碑", "系统",
    "提效", "研发", "端到端", "规则与方案", "AI",
)
# 项目名中可忽略的连词：生成变体关键词（视频稽查与在线审核 → 视频稽查在线审核）
_IGNORABLE_CONJUNCTIONS = ("与", "和", "及")
# 项目名通用前缀（按长度降序取最长匹配，仅剥一层）：
# 「数据标注平台」→「标注平台」（周报正文常省略「数据」前缀）
_PROJECT_PREFIX_WORDS = ("数据", "质检", "在线", "自动", "智能", "AI")
# 名单类文档（提及项目名但非项目活动证据，如人员盘点/组织架构）不参与内容匹配
_NON_ACTIVITY_HINTS = ("人员盘点", "组织架构", "通讯录")
# 项目名匹配源文档的最小关键词长度（防「数据」「质检」等短通用词误匹配）。
# 剥离链/前缀/连词变体允许 3 字符——「质检作业域」剥前缀后为「作业域」，
# 与「履约作业域」同义（质检产品按域划分，作业域=质检履约作业域）。
_PROJECT_KEYWORD_MIN_LEN = 3
# 中英/数字切分 token 的最小长度（英文 token 更易误匹配，要求 ≥4）
_SPLIT_TOKEN_MIN_LEN = 4

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
    # 停滞提醒忽略的项目（页面 stem，如「项目-多模态OCR在包袋AI鉴定中的应用」）：
    # 已完结/已移交/常态化维护的项目不再重复告警
    "project_stall_ignore": (),
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
        ignore = set(self._cfg.get("project_stall_ignore") or ())
        signals: List[Dict[str, Any]] = []
        for page in sorted(project_dir.glob("*.md")):
            if ".bak." in page.stem:
                continue  # 跳过 wiki-update 备份文件（*.bak.1.md），避免重复信号
            if page.stem in ignore:
                continue  # 已完结/已移交/常态化维护的项目不再重复告警
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
            # 兜底：指纹可能陈旧（生成时的证据快照），按项目名在 SOURCE 目录
            # 扫描同名文档，取真实最新日期——指纹滞后时避免误报「项目停滞」。
            dir_freshest = self._source_dir_freshest(source_root, page.stem)
            if dir_freshest is not None and dir_freshest > freshest:
                freshest = dir_freshest
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

    def _source_dir_freshest(self, source_root: Path, page_stem: str) -> Optional[datetime]:
        """SOURCE 目录中按项目名匹配文档的最新日期（指纹兜底）。

        项目活动常散落在周报/会议纪要等文档中（文件名仅含人名/日期），
        因此分两级匹配：
        1. **文件名匹配**：文件名含关键词（如「软硬一体」）——快路径
        2. **内容匹配**：文件名不含关键词时，读取文档内容匹配
           （如「数据标注平台」在周报正文中写作「标注平台」）
        取匹配文档的最新日期；关键词过短或无匹配时返回 None（沿用指纹判定）。
        """
        keywords = self._project_keywords(page_stem)
        if not keywords:
            return None
        freshest: Optional[datetime] = None
        try:
            for path in source_root.rglob("*.md"):
                if ".bak." in path.name:
                    continue
                if not self._doc_matches(path, keywords):
                    continue
                doc_date = self._doc_date(path)
                if doc_date and (freshest is None or doc_date > freshest):
                    freshest = doc_date
        except OSError as exc:
            logger.warning("提醒引擎扫描源目录失败 %s: %s", source_root, exc)
            return None
        return freshest

    @classmethod
    def _doc_matches(cls, path: Path, keywords: List[str]) -> bool:
        """文档是否与项目关键词匹配：先比文件名（快），再读内容比。

        内容匹配跳过名单类文档（人员盘点/组织架构等仅提及项目名，
        不构成项目活动证据，避免把「名字出现在名单里」误判为活跃）。
        """
        if any(kw in path.name for kw in keywords):
            return True
        if any(hint in path.name for hint in _NON_ACTIVITY_HINTS):
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return any(kw in text for kw in keywords)

    @classmethod
    def _project_keywords(cls, page_stem: str) -> List[str]:
        """从项目页文件名提取源文档匹配关键词。

        「项目-软硬一体项目」→ 剥离前缀 →「软硬一体项目」→ 剥离业务后缀
        →「软硬一体」。逐级剥离产出候选关键词集合，任一命中即可。
        """
        core = page_stem
        if core.startswith("项目-"):
            core = core[len("项目-"):]
        candidates: List[str] = [core]
        cur = core
        for _ in range(4):  # 最多剥离 4 层（如「数据智能部2026调薪规则与方案」）
            stripped = cur
            for suffix in _PROJECT_NAME_SUFFIXES:
                if stripped.endswith(suffix) and len(stripped) > len(suffix):
                    stripped = stripped[: -len(suffix)]
                    break
            else:
                break
            if len(stripped) < _PROJECT_KEYWORD_MIN_LEN:
                break
            cur = stripped
            candidates.append(cur)
        # 补充中英/数字边界切分的特征 token（如「XRay手机拆修检测」→ XRay），
        # 覆盖项目名与文档命名措辞不一致的场景；统一过滤过短关键词
        seen: set = set()
        result: List[str] = []
        tokens: List[str] = []
        for c in candidates:
            tokens.extend(cls._split_tokens(c))
        # 连词变体：视频稽查与在线审核 → 视频稽查在线审核（文档命名常省略连词）
        variants: List[str] = []
        for c in candidates + tokens:
            for conj in _IGNORABLE_CONJUNCTIONS:
                if conj in c:
                    variants.append(c.replace(conj, ""))
        # 通用前缀变体：数据标注平台 → 标注平台（周报正文常省略「数据」等前缀）
        for c in candidates + tokens:
            for prefix in _PROJECT_PREFIX_WORDS:
                if c.startswith(prefix) and len(c) > len(prefix):
                    variants.append(c[len(prefix):])
        for kw in candidates + tokens + variants:
            if kw in seen:
                continue
            seen.add(kw)
            if len(kw) >= _PROJECT_KEYWORD_MIN_LEN:
                result.append(kw)
        return result

    @classmethod
    def _split_tokens(cls, text: str) -> List[str]:
        """按中英/数字边界切分特征 token（仅含英文字母的词参与）。

        中文 token（如「数据智能部」「质检研发」）过于通用，会误匹配部门
        全量文档掩盖真实停滞；英文特征（如 XRay）与文档命名一致性高。
        """
        tokens: List[str] = []
        for piece in _TOKEN_SPLIT_RE.findall(text):
            piece = piece.strip(" -_·()（）《》「」")
            if piece and len(piece) >= _SPLIT_TOKEN_MIN_LEN and re.search(r"[A-Za-z]", piece):
                tokens.append(piece)
        return tokens

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
