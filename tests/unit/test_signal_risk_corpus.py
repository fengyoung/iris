"""风险句抽取语料：新增过滤规则前，先往 CASES 里补一条真实样本。

语料来自 2026-10 的周报/会议纪要误报，覆盖模板标题、否定表述、表格行、
元信息行与正向收尾等形态。规则一旦误伤，这里会先红。
"""
from datetime import date

import pytest

from iris.signals.detector import (
    SignalDetector,
    _risk_identity,
    _risk_sentences,
)

TODAY = date(2026, 9, 21)

# snippet → 期望抽取出的风险句（精确匹配，顺序即出现顺序）。
CASES = [
    {
        'name': 'heading',
        'desc': 'Markdown 标题不是风险',
        'snippet': '### 3. 遇到的问题与风险',
        'expect': [],
    },
    {
        'name': 'label_line',
        'desc': '模板标签 + 「原文未明确提及」',
        'snippet': '- **其他阻塞项与资源问题**：原文未明确提及',
        'expect': [],
    },
    {
        'name': 'benign',
        'desc': '风险可控 / 风险不大属于正向结论',
        'snippet': '- 验真C端上线风险可控，先加企微免费提供结果。\n'
                   '- 10月内可完成剩余核心问题治理，风险不大。',
        'expect': [],
    },
    {
        'name': 'unmentioned',
        'desc': '「未明确提及阻塞」不是已发生的风险',
        'snippet': '- 原文未明确提及：具体上线时间、资源投入及其他外部阻塞项。',
        'expect': [],
    },
    {
        'name': 'table_row',
        'desc': '表格行是进度数据，不是风险叙述',
        'snippet': '- | KP3 | 139 款安卓小内配模型上线（解除工艺复核阻塞） |',
        'expect': [],
    },
    {
        'name': 'meta_line',
        'desc': '来源文件名里的「风险对焦」不是风险',
        'snippet': '- 来源：20261015-项目讨论-在线审核项目进度与风险对焦.txt',
        'expect': [],
    },
    {
        'name': 'meeting_topic',
        'desc': '会议主题「风险对焦会」不是风险',
        'snippet': '- 本次为在线审核项目正式启动后的进度与风险对焦会。',
        'expect': [],
    },
    {
        'name': 'unblocked',
        'desc': '「已解除阻塞」是已完成项',
        'snippet': '- 工艺复核已解除阻塞。',
        'expect': [],
    },
    {
        'name': 'resolved',
        'desc': '「风险已解除」是已闭环项',
        'snippet': '- 风险已解除。',
        'expect': [],
    },
    {
        'name': 'positive_crawler',
        'desc': '具体风险句必须保留原文',
        'snippet': '- **爬虫批量返回“序列号无效”**：已做紧急处理，需调研备用渠道兜底，属于外部依赖风险。',
        'expect': ['爬虫批量返回“序列号无效”：已做紧急处理，需调研备用渠道兜底，属于外部依赖风险'],
    },
    {
        'name': 'exact_duplicate',
        'desc': '同一句在窗口内重复出现只保留一次',
        'snippet': '- 交付延期两周。\n- 交付延期两周。',
        'expect': ['交付延期两周'],
    },
    {
        'name': 'multi_risk',
        'desc': '多条具体风险逐条保留（截断在 kr_signals 层）',
        'snippet': '- 风险一：交付延期两周。\n- 风险二：接口联调受阻。\n- 风险三：资源不足导致阻塞。',
        'expect': ['风险一：交付延期两周', '风险二：接口联调受阻', '风险三：资源不足导致阻塞'],
    },
]


@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_risk_sentence_corpus(case):
    assert _risk_sentences(case['snippet']) == case['expect'], case['desc']


def _kr_signals(snippet, path='05-会议纪要/202610/20261019-部门周会.md', krs=None):
    row = {'doc_path': path, 'doc_date': TODAY.isoformat(), 'evidence_snippet': snippet}
    krs = krs or {'O1-KR1': 'x'}
    return SignalDetector(today=TODAY).kr_signals(krs, {key: [row] for key in krs})


def test_kr_signals_ignores_noise_only_snippet():
    snippet = ('### 3. 遇到的问题与风险\n'
               '- 上线风险可控。\n'
               '- 原文未明确提及其他阻塞项。\n'
               '- | KP3 | 139 款安卓小内配模型上线（解除工艺复核阻塞） |\n')
    assert _kr_signals(snippet) == []


def test_kr_signals_merges_krs_and_marks_detail():
    krs = {'O1-KR1': 'x', 'O1-KR2': 'x'}
    signals = _kr_signals('- 接口联调受阻，交付延期两周。', krs=krs)
    assert len(signals) == 1
    assert 'O1-KR1' in signals[0].detail and 'O1-KR2' in signals[0].detail


def test_stable_key_ignores_punctuation_and_generic_tail():
    left = _kr_signals('- 共享目录回写存在风险。')[0].stable_key
    right = _kr_signals('- 病毒问题：共享目录回写，存在风险，需限制写入。')[0].stable_key
    assert left == right


def test_stable_key_differs_for_different_risks():
    signals = _kr_signals('- 风险一：交付延期两周。\n- 风险二：接口联调受阻。')
    assert len(signals) == 2
    assert signals[0].stable_key != signals[1].stable_key


@pytest.mark.parametrize('left,right', [
    ('共享目录回写存在风险', '共享目录回写，存在风险'),
    ('病毒问题：共享目录回写存在风险，需限制写入', '共享目录回写，存在风险'),
    ('交付延期两周，属于外部依赖风险', '交付延期两周'),
])
def test_risk_identity_matches_wording_variants(left, right):
    assert _risk_identity(left) == _risk_identity(right)


def test_risk_identity_prefers_longest_risk_clause():
    assert _risk_identity('交付延期两周，资源不足导致接口联调受阻') == '资源不足导致接口联调受阻'


def test_risk_identity_strips_generic_tail():
    assert _risk_identity('交付延期两周，属于外部依赖风险') == '交付延期两周'


def test_risk_identity_uses_clause_before_generic_tail():
    sentence = '爬虫批量返回“序列号无效”：已做紧急处理，需调研备用渠道兜底，属于外部依赖风险。'
    assert _risk_identity(sentence) == '需调研备用渠道兜底'


def test_risk_identity_handles_generic_only_sentence():
    assert _risk_identity('属于外部依赖风险') == '属于外部依赖风险'
