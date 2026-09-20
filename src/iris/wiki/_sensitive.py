"""敏感文档判定 — 调薪 / 人员盘点 / 绩效评估类内容的唯一真相源。

**策略**：敏感文档（调薪方案、人员盘点与评估过程记录、绩效评价、Leader 盘点）
只允许保留在 SOURCE 原件，不得进入任何下游衍生制品（Wiki 页面、知识图谱、
ASR 热词与替换词典、检索结果）。

**匹配纪律：只匹配「文档身份」（文件名 / 标题 / 术语），绝不匹配正文。**
实测依据（1001 篇语料）：``绩效`` 正文命中 44 篇、``校准`` 31 篇、``晋升`` 10 篇，
绝大多数是「模型绩效」「色卡校准」「晋升流程」这类正常业务表述。敏感与否取决于
**这篇文档是什么**，而不是**它提到了什么**。正文级匹配会造成大面积误伤。

本模块零依赖（仅 ``re`` + ``typing``），无模块级可变状态，可被 ``iris.retrieval``
在函数内延迟导入，也可被线程池并发调用。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, List, Optional

# ── 归一化 ────────────────────────────────────────────────

# 归一化时剥离的分隔符：空白、连字符、下划线、间隔号、破折号
_SEPARATOR_RE = re.compile(r"[\s\-_·・—–－]+")


def normalize_for_match(text: str) -> str:
    """归一化为可比较形式：剥离分隔符 + 转小写。

    使 ``2026-核心Leader盘点 — 冯扬团队`` 与 ``leader盘点`` 可互相匹配。
    """
    if not text:
        return ""
    return _SEPARATOR_RE.sub("", text).lower()


# ── 敏感词表（全部为归一化形式） ──────────────────────────
#
# A 类：HR 专属语境，任意位置连续出现即敏感
# B 类：必须作为连续组合词出现（裸词误伤率高，见下方禁用清单）
SENSITIVE_TERMS: tuple[str, ...] = (
    # ── A 类：语境无歧义 ──
    "调薪",
    "薪资",
    "薪酬",
    "绩效",
    "述职",
    # ── B 类：连续组合词 ──
    "人员盘点",
    "人才盘点",
    "leader盘点",
    "盘点落位",
    "盘点评估",
    "盘点校准",
    "盘点沟通",
    "评估过程记录",
    "职级定级",
    "晋升确认",
    "晋升评审",
    "930名单",
    "调薪名单",
)

# ── 禁用词清单 ────────────────────────────────────────────
# 以下词实测误伤率高，**禁止**加入 SENSITIVE_TERMS。仅作组合词的一部分使用：
#
#   盘点   9 篇文件名命中，其中 5 篇是正常业务
#          （首页推荐feeds系统性盘点 / 硬件团队下半年重点项目盘点 /
#            XRay项目数据盘点 ×2 / 搜推特征盘点对齐）→ 误伤 5/9
#   定级   24 篇文件名命中，24 篇全是「拍照3.0外观定级」业务 → 误伤 24/24
#   校准   31 篇正文命中，多为色卡校准白平衡 → 禁止
#   落位   8 篇命中，正常业务有（清洁方案落位 / 周报）→ 只能组合成「盘点落位」
#   晋升   职级晋升（如「2026年7月晋升至5级」）属正常组织信息，经确认不纳入敏感边界
#   组织/团队 + 盘点 共现判定会误杀「硬件团队下半年重点项目盘点与团队规划」
#          → 只做连续子串匹配，不做跨词共现判定


# ── 判定谓词 ──────────────────────────────────────────────


def matched_sensitive_term(text: str) -> Optional[str]:
    """返回文本命中的敏感词，未命中返回 None（供日志与审计使用）。"""
    normalized = normalize_for_match(text)
    if not normalized:
        return None
    for term in SENSITIVE_TERMS:
        if term in normalized:
            return term
    return None


def is_sensitive_path(relative_path: str) -> bool:
    """路径级判定：只看文件名（去扩展名），不含目录名。

    只看文件名是有意为之——``02-部门管理/2026/`` 这类目录本身不敏感，
    命中必须来自文档自身的名字。
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        return False
    name = relative_path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".md"):
        name = name[:-3]
    return matched_sensitive_term(name) is not None


def is_sensitive_title(title: str) -> bool:
    """标题级判定：候选主题 / Wiki 页面标题。

    拦的是「正常文档里的敏感章节标题」（例如某篇会议纪要里的 ``## 930 名单``），
    与 :func:`is_sensitive_path` 的「敏感文档全部内容」互为补充，不可替代。
    """
    return matched_sensitive_term(title) is not None


def is_sensitive_term(term: str) -> bool:
    """术语级判定：ASR 热词 / 替换词典条目。

    热词链路没有来源信息（LLM 只返回词面），词面匹配是唯一可行手段。
    """
    return matched_sensitive_term(term) is not None


# ── 集合过滤器 ────────────────────────────────────────────
# 调用点只需一行赋值，避免往大函数里加分支（C901 门禁 max-complexity=20）。


def filter_sensitive_terms(
    terms: Iterable[str], *, on_drop: Optional[Callable[[str, str], None]] = None
) -> List[str]:
    """剔除敏感术语，保持原有顺序。

    Args:
        terms: 待过滤术语。
        on_drop: 可选回调 ``(term, matched)``，用于审计日志。
    """
    kept: List[str] = []
    for term in terms:
        matched = matched_sensitive_term(term) if isinstance(term, str) else None
        if matched is None:
            kept.append(term)
            continue
        if on_drop is not None:
            on_drop(term, matched)
    return kept


def filter_sensitive_paths(paths: Iterable[str]) -> List[str]:
    """剔除敏感文档路径，保持原有顺序。"""
    return [p for p in paths if not is_sensitive_path(p)]


def _path_of(hit: Any) -> str:
    """从检索命中对象上安全取 relative_path。

    非 str 值一律视为「非敏感」（保留）。这不是防御性编程：既有测试用
    ``MagicMock()`` 构造命中对象，其任意属性访问都返回 MagicMock 而非抛
    ``AttributeError``，直接喂给正则会以 TypeError 崩溃。
    """
    value = getattr(hit, "relative_path", "")
    return value if isinstance(value, str) else ""


def filter_sensitive_hits(hits: Iterable[Any]) -> List[Any]:
    """按 ``hit.relative_path`` 剔除敏感来源的检索命中，保持原有顺序。"""
    return [h for h in hits if not is_sensitive_path(_path_of(h))]


__all__ = [
    "SENSITIVE_TERMS",
    "normalize_for_match",
    "matched_sensitive_term",
    "is_sensitive_path",
    "is_sensitive_title",
    "is_sensitive_term",
    "filter_sensitive_terms",
    "filter_sensitive_paths",
    "filter_sensitive_hits",
]
