"""信号检测：每条信号带来源，证据缺失不冒充实际停滞。"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import Enum
import re

from iris.intelligence.context import local_today
from iris.signals.text import clip, compact_source
from iris.wiki._sensitive import is_sensitive_title


class SignalType(str, Enum):
    UNDOCUMENTED_TOPIC = 'UNDOCUMENTED_TOPIC'
    STALE_WIKI_ACTIVE = 'STALE_WIKI_ACTIVE'
    KR_EVIDENCE_GAP = 'KR_EVIDENCE_GAP'
    KR_AT_RISK = 'KR_AT_RISK'
    DECISION_REVIEW_DUE = 'DECISION_REVIEW_DUE'
    OPEN_DECISION_STALE = 'OPEN_DECISION_STALE'
    NEW_PERSON_NO_PAGE = 'NEW_PERSON_NO_PAGE'
    ORPHAN_CONCEPT = 'ORPHAN_CONCEPT'
    SUPPLY_GAP = 'SUPPLY_GAP'
    WEEKLY_MISSING = 'WEEKLY_MISSING'
    PROJECT_STALLED = 'PROJECT_STALLED'


@dataclass
class Signal:
    kind: str
    title: str
    evidence: list[str]
    action: str
    urgency: int = 1
    impact: int = 1
    # 通知正文用的一句话解释（风险原文 / 触发原因），不参与排序。
    detail: str = ''
    # 已压缩的人类可读来源（文档名 · MM-DD），与 evidence 可不等长。
    sources: list[str] = field(default_factory=list)
    # 跨日去重使用的稳定指纹；为空时由 title + 首个来源推导。
    stable_key: str = ''

    def to_dict(self):
        return asdict(self)


# 「风险/问题：」「### 3. 遇到的问题与风险」这类模板标题不是风险本身。
_LABEL_RE = re.compile(
    r'^[\s*#>\-–—•·:：/、，。；;（）()【】\[\]]*'
    r'(?:遇到(?:的)?)?(?:问题|风险|问题与风险|风险与问题|风险/问题|问题/风险|'
    r'其他风险项|风险及问题|问题及风险)'
    r'[\s*:：/、，。；;（）()【】\[\]]*$'
)
_HEADING_RE = re.compile(r'^\s*#{1,6}\s')
_LEADING_BULLET_RE = re.compile(r'^\s*(?:[-*•·]|\d+[.、)])\s*')
_MARKDOWN_RE = re.compile(r'[*_`>]+')
_META_RE = re.compile(r'^\s*(?:来源|日期|类型|时长|标题|主题|地点|参与人|参会人|会议主题)\s*[:：]')
_RISK_RE = re.compile(r'风险|阻塞|延期|受阻')
_SEVERE_RE = re.compile(r'延期|受阻|阻塞|卡点|缺口|未定稿|无法|缺少|未闭环|潜在风险|存在风险')
_BENIGN_RE = re.compile(r'符合预期|按计划推进|正常推进|风险不(?:大|存在)|原文未(?:说明|提及|明确)')
# 先剔除「风险可控 / 风险不大 / 未明确提及阻塞」等非风险表述，再看是否还剩风险词。
_NON_RISK_RE = re.compile(
    r'(?:风险(?:可控|不大|较低|很小|有限|重心|口径|定义|指标|防控)|'
    r'无(?:明显)?风险|暂无(?:明显)?风险|'
    r'风险已(?:解除|消除|同步|推动|解决|闭环)|风险(?:对焦|复盘|讨论)(?:会|$)|'
    r'(?:解除|消除|解决)[^。；\n]{0,12}(?:阻塞|风险)|'
    r'(?:不|未)(?:会)?(?:再)?延期|'
    r'未(?:明确)?提及[^。；\n]{0,40}(?:风险|阻塞|延期|受阻|卡点|资源问题)|'
    r'未(?:显式)?列出[^。；\n]{0,20}(?:阻塞|卡点|风险)|'
    r'其他[^。；\n]{0,12}(?:风险|阻塞|问题)[^。；\n]{0,12}(?:原文)?(?:未|无|暂无))'
)

# 风险句跨日指纹：同一风险换标点、换泛化尾句不应变成「新增」。
_CLAUSE_SPLIT_RE = re.compile(r'[，,；;：:。！？!?]')
_NON_WORD_RE = re.compile(r'[\W_]+')
# 纯泛化风险分句（「存在风险」「属于外部依赖风险」）不含风险主体，不能当身份。
_GENERIC_RISK_CLAUSE_RE = re.compile(
    r'^(?:仍|还)?(?:属于|存在|有|构成|带来)?'
    r'(?:一定|潜在|较大|较小|外部依赖|外部|整体)?(?:的)?风险(?:项|点)?$'
)
# 只剥离紧邻的「存在/属于…风险」后缀，避免误伤「系统性交付风险」这类主体。
_GENERIC_RISK_SUFFIX_RE = re.compile(
    r'(?:属于|存在|有|构成|带来)'
    r'(?:一定|潜在|较大|较小|外部依赖|外部|整体)?(?:的)?风险(?:项|点)?$'
)

_REMINDER_SPECS = {
    'category_inactive': ('SUPPLY_GAP', 6, 3, '补充或确认该栏目'),
    'weekly_report_missing': ('WEEKLY_MISSING', 6, 3, '提醒提交周报'),
    'project_stalled': ('PROJECT_STALLED', 5, 3, '确认项目状态或补充证据'),
}
_REMINDER_TITLES = {
    'SUPPLY_GAP': '{target} 已 {days} 天无更新',
    'WEEKLY_MISSING': '{target} 周报断档 {days} 天',
    'PROJECT_STALLED': '{target} 项目停滞 {days} 天',
}


def _clean_risk_line(line: str) -> str:
    text = _LEADING_BULLET_RE.sub('', line.strip())
    text = _MARKDOWN_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def _is_risk_label(text: str) -> bool:
    return bool(_LABEL_RE.fullmatch(text))


def _risk_score(text: str) -> int:
    """具体风险（延期/受阻/缺口）优先于泛泛提及「风险」。"""
    score = 2
    if _SEVERE_RE.search(text):
        score += 2
    if '风险' in text:
        score += 1
    return score


def _risk_title(sentence: str, limit: int = 60) -> str:
    """风险句标题：保留句首主语（通常才是风险主体），只做长度截断。"""
    return clip(sentence, limit)


def _risk_identity(sentence: str) -> str:
    """风险句跨日身份：取最长的带风险主体分句，去掉标点与泛化尾句。

    「病毒问题：共享目录回写存在风险，需限制写入」与「共享目录回写，存在风险」
    都应归一到「共享目录回写」，否则同一风险换个说法就会重新变成「新增」。
    """
    cleaned = _clean_risk_line(sentence)
    clauses = [_clean_risk_line(part) for part in _CLAUSE_SPLIT_RE.split(cleaned)]
    candidates: list[str] = []
    for index, clause in enumerate(clauses):
        if not clause or not _RISK_RE.search(_NON_RISK_RE.sub('', clause)):
            continue
        normalized = _NON_WORD_RE.sub('', clause)
        if _GENERIC_RISK_CLAUSE_RE.fullmatch(normalized):
            # 「共享目录回写，存在风险」：风险词被逗号拆成独立泛化分句，
            # 主语在前一个分句里，取它而不是整句。
            if index > 0:
                subject = _NON_WORD_RE.sub('', clauses[index - 1])
                core = _GENERIC_RISK_SUFFIX_RE.sub('', subject)
                if len(core) >= 4:
                    candidates.append(core)
            continue
        core = _GENERIC_RISK_SUFFIX_RE.sub('', normalized)
        if len(core) >= 4:
            candidates.append(core)
    if candidates:
        return max(candidates, key=len)
    fallback = _GENERIC_RISK_SUFFIX_RE.sub('', _NON_WORD_RE.sub('', cleaned))
    return fallback or _NON_WORD_RE.sub('', cleaned) or cleaned


def _sentences_similar(left: str, right: str) -> bool:
    """同一文档内的近似风险句去重（包含关系、核心分句包含、二元组重合度高）。"""
    a = re.sub(r'\W', '', left)
    b = re.sub(r'\W', '', right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # 「共享目录回写存在风险，正限制…」与「病毒问题：…，共享目录回写存在风险」
    # 共享同一个核心分句；短碎句通常是长句的标题式复述。
    lead_a = re.split(r'[，,；;：:]', left, maxsplit=1)[0]
    lead_b = re.split(r'[，,；;：:]', right, maxsplit=1)[0]
    if len(lead_a) >= 6 and lead_a in right:
        return True
    if len(lead_b) >= 6 and lead_b in left:
        return True
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a or not grams_b:
        return False
    return len(grams_a & grams_b) / len(grams_a | grams_b) >= 0.65


def _risk_sentences(snippet: str) -> list[str]:
    """从证据窗口里抽取真实风险句，而不是「命中关键词的行」。

    过滤规则：
    - Markdown 标题 / 「风险/问题：」模板标签；
    - 「风险可控 / 风险不大 / 未明确提及阻塞」等非风险表述；
    - 同一句在窗口内重复出现只保留一次。
    """
    sentences: list[str] = []
    seen: set[str] = set()
    for raw_line in (snippet or '').splitlines():
        if _HEADING_RE.match(raw_line):
            continue
        line = _clean_risk_line(raw_line)
        # 表格行是数据/进度，不是风险叙述；「解除…阻塞」这类正向表述已在
        # _NON_RISK_RE 中剔除，避免把 KPI 完成项当成风险。
        if not line or line.startswith('|') or _META_RE.match(line) or _is_risk_label(line):
            continue
        parts = re.split(r'(?<=[。！？；;])', line) if re.search(r'[。；;]', line) else [line]
        for part in parts:
            text = _clean_risk_line(part).strip('。；;，, ')
            if not text or _is_risk_label(text) or _BENIGN_RE.search(text):
                continue
            if not _RISK_RE.search(_NON_RISK_RE.sub('', text)):
                continue
            key = re.sub(r'\s+', '', text)
            if key in seen:
                continue
            seen.add(key)
            sentences.append(text)
    return sentences


class SignalDetector:
    def __init__(self, *, today=None):
        self.today = today or local_today()

    def decisions(self, records, docs):
        signals = []
        for row in records:
            if row['status'] != 'open':
                continue
            if row.get('review_at'):
                days = (date.fromisoformat(row['review_at']) - self.today).days
                if 0 <= days <= 7:
                    signals.append(Signal('DECISION_REVIEW_DUE', f"{row['title']}：{days} 天后复盘",
                        [row['decision_id'], row['review_at']], '安排决策复盘', 10 - days, 3,
                        stable_key=f'DECISION_REVIEW_DUE|{row["decision_id"]}'))
            latest = max([date.fromisoformat(row['decided_at'])] +
                         [d.date for d in docs if row['title'] in d.text or row['decision_id'] in d.text])
            if (self.today - latest).days > 30:
                signals.append(Signal('OPEN_DECISION_STALE', row['title'] + ' 超过30天无后续证据',
                                      [row['decision_id'], latest.isoformat()], '核对执行状态', 4, 3,
                                      stable_key=f'OPEN_DECISION_STALE|{row["decision_id"]}'))
        return signals

    def kr_signals(self, krs, evidence):
        """KR 风险信号：按「文档 + 风险句」去重，多 KR 引用同一句时合并。

        旧实现每条 KR 生成一条「相关记录提及风险」，一条周报会被 4 个 KR 各报一次，
        且不含风险本身。现在一条风险只出一条信号，detail 标明关联 KR。
        """
        cutoff = (self.today - timedelta(days=14)).isoformat()
        signals: list[Signal] = []
        risks: dict[tuple[str, str], dict] = {}
        for key, text in krs.items():
            rows = evidence.get(key, [])
            recent = [r for r in rows if cutoff <= r['doc_date'] <= self.today.isoformat()]
            if not recent:
                signals.append(Signal('KR_EVIDENCE_GAP', f'{key}：14天内无新增支撑证据',
                                      [r['doc_path'] for r in rows[-1:]], '核对进展与文档覆盖；不代表实际停滞', 5, 3,
                                      stable_key=f'KR_EVIDENCE_GAP|{key}'))
                continue
            for row in recent:
                for sentence in _risk_sentences(row.get('evidence_snippet', '')):
                    norm = re.sub(r'\s+', '', sentence)
                    item = risks.setdefault((row['doc_path'], norm), {
                        'sentence': sentence, 'krs': set(), 'row': row,
                    })
                    item['krs'].add(key)
        per_doc: Counter[str] = Counter()
        selected: dict[str, list[str]] = {}
        for (path, norm), item in sorted(
            risks.items(),
            key=lambda pair: (-_risk_score(pair[1]['sentence']), -len(pair[1]['sentence']),
                              -len(pair[1]['krs']), pair[1]['row'].get('doc_date', '')),
        ):
            if per_doc[path] >= 2:
                continue
            kept = selected.setdefault(path, [])
            if any(_sentences_similar(item['sentence'], prev) for prev in kept):
                continue
            kept.append(item['sentence'])
            per_doc[path] += 1
            score = _risk_score(item['sentence'])
            signals.append(Signal(
                'KR_AT_RISK', _risk_title(item['sentence']), [path],
                '确认风险与应对责任人', 7,
                min(5, 2 + len(item['krs']) + (1 if score >= 4 else 0)),
                detail='关联 KR：' + '、'.join(sorted(item['krs'])),
                sources=[compact_source(path, item['row'].get('doc_date', ''))],
                stable_key=f'{path}|{_risk_identity(item["sentence"])}',
            ))
        return signals

    def undocumented(self, topics, docs):
        signals = []
        for topic, chats in topics.items():
            if len(set(chats)) < 2 or is_sensitive_title(topic):
                continue
            if not any(topic in d.title for d in docs):
                signals.append(Signal('UNDOCUMENTED_TOPIC', topic + '：多群讨论但标题中未找到文档',
                                      sorted(set(chats)), '核验是否需要补录讨论文档', 3, len(set(chats)),
                                      stable_key=f'UNDOCUMENTED_TOPIC|{topic}'))
        return signals

    def missing_entities(self, docs, page_titles):
        references: dict[str, set[str]] = {}
        for doc in docs:
            if not 0 <= (self.today - doc.date).days <= 14:
                continue
            for name in re.findall(r'\[\[((?:人物|概念)-[^\]|]+)(?:\|[^\]]+)?\]\]', doc.text):
                if name not in page_titles and not is_sensitive_title(name):
                    references.setdefault(name, set()).add(doc.path)
        signals = []
        for name, paths in references.items():
            if name.startswith('概念-') and len(paths) < 2:
                continue
            kind = 'NEW_PERSON_NO_PAGE' if name.startswith('人物-') else 'ORPHAN_CONCEPT'
            signals.append(Signal(kind, name + ' 尚无页面', sorted(paths), '核验实体并补建页面', 2, len(paths),
                                  stable_key=f'{kind}|{name}'))
        return signals


def reminder_signals(payload: dict) -> list[Signal]:
    """把 ReminderEngine.collect() 的输出转成统一信号，与 KR/Wiki 信号一起排序。"""
    signals: list[Signal] = []
    for row in (payload or {}).get('signals', []) or []:
        spec = _REMINDER_SPECS.get(row.get('type', ''))
        if not spec:
            continue
        kind, urgency, impact, action = spec
        target = str(row.get('target', '') or '').strip()
        if not target:
            continue
        days = int(row.get('days', 0) or 0)
        title = _REMINDER_TITLES[kind].format(target=target, days=days)
        detail = str(row.get('detail', '') or '')
        if kind == 'SUPPLY_GAP':
            # 标题已经写明「已 N 天无更新」，详情只补阈值，避免整句复述。
            threshold = row.get('threshold')
            detail = f'阈值 {threshold} 天' if threshold else ''
        signals.append(Signal(
            kind, title, [], action, urgency, impact,
            detail=detail,
            stable_key=f'{kind}|{target}',
        ))
    return signals
