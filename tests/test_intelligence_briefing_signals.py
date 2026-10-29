"""情报引用、鲜度、信号边界和交付幂等性。"""
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from iris.core.exceptions import IrisValueError
from iris.intelligence.context import SourceDoc, read_document
from iris.briefing.open_issue_detector import OpenIssueDetector
from iris.briefing.synthesizer import BriefingSynthesizer
from iris.decisions.store import pending_reason
from iris.signals.delivery import SignalDelivery, build_title, render
from iris.signals.detector import Signal, SignalDetector, reminder_signals
from iris.signals.history import SignalHistory, signal_key
from iris.signals.ranker import rank
from iris.signals.service import _annotate_pending
from iris.wiki.freshness import compute_freshness_score

TODAY = date(2026, 9, 21)


def test_briefing_single_synthesis():
    llm = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(text='## 核心背景\n已有方案')))
    context = {'recent_documents': [{'path': 'a.md', 'date': '2026-09-21'}]}
    result = BriefingSynthesizer(llm).synthesize('方案', '2026-09-22', context)
    assert 'a.md' in result
    llm.generate.assert_called_once()
    assert llm.generate.call_args.kwargs['route_context']['user_selected_role'] == 'adv_model'


def test_briefing_budget_does_not_silently_truncate():
    llm = Mock()
    with pytest.raises(IrisValueError):
        BriefingSynthesizer(llm, 5).synthesize('a', '2026-09-21', {'text': 'long evidence'})
    llm.generate.assert_not_called()


def test_missing_closure_label():
    result = OpenIssueDetector().detect([{'date': '2026-09-20', 'path': 'a', 'snippet': '接口权责待定'}])
    assert '待确认' in result[0]['status']


def test_sensitive_source_title(tmp_path):
    path = tmp_path / '普通文件.md'
    path.write_text('---\ntitle: 调薪方案\n---\n内容')
    assert read_document(tmp_path, path) is None


def test_body_sensitive_word_not_filtered(tmp_path):
    path = tmp_path / '业务.md'
    path.write_text('色卡校准和模型绩效')
    assert read_document(tmp_path, path)


def test_path_escape_filtered(tmp_path):
    assert read_document(tmp_path / 'sub', tmp_path / 'outside.md') is None


@pytest.mark.parametrize('offset,expected', [(0, True), (7, True), (8, False), (-1, False)])
def test_review_date_inclusive(offset, expected):
    row = {'title': '方案', 'status': 'open', 'decision_id': 'DEC-X', 'decided_at': TODAY.isoformat(),
           'review_at': (TODAY + timedelta(days=offset)).isoformat()}
    assert bool(SignalDetector(today=TODAY).decisions([row], [])) is expected


def test_no_activity_is_fresh():
    assert compute_freshness_score(None, [], today=TODAY) == 1


def test_caught_up_is_fresh():
    docs = [SourceDoc('a', 'a', '', TODAY, '')]
    assert compute_freshness_score(TODAY, docs, today=TODAY) == 1


def test_active_stale_is_flagged():
    docs = [SourceDoc(str(n), 'a', '', TODAY, '') for n in range(3)]
    assert compute_freshness_score(TODAY - timedelta(days=40), docs, today=TODAY) == 0


def test_rank_dedup_and_limit():
    rows = [Signal('test', str(n), [], '', n, 1) for n in range(10)]
    assert len(rank(rows + rows)) == 5
    assert rank(rows)[0].title == '9'


def test_delivery_idempotent(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    assert delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')['status'] == 'sent'
    assert delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')['duplicate']
    sender.assert_called_once()


def test_delivery_uncertain_no_blind_retry(tmp_path):
    sender = Mock(side_effect=OSError('offline'))
    delivery = SignalDelivery(tmp_path, sender)
    assert delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')['status'] == 'uncertain'
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'uncertain'
    assert result['duplicate'] is True
    assert 'uncertain' in result['reason']
    sender.assert_called_once()


def test_delivery_dry_run(tmp_path):
    sender = Mock()
    assert SignalDelivery(tmp_path, sender).deliver('摘要', day='2026-09-21', user_id='ou_1', dry_run=True)['status'] == 'dry_run'
    sender.assert_not_called()


def test_no_credentials_visible_failure(tmp_path):
    assert SignalDelivery(tmp_path).deliver('摘要', day='2026-09-21')['status'] == 'not_configured'


# ── KR 风险句抽取 ──────────────────────────────────────────

def _risk_row(path, snippet, doc_date=None):
    return {'doc_path': path, 'doc_date': doc_date or TODAY.isoformat(),
            'evidence_snippet': snippet}


def test_kr_signals_ignore_template_headers_and_negated_risks():
    snippet = ('### 3. 遇到的问题与风险\n\n'
               '- 验真C端上线风险可控，先加企微免费提供结果。\n'
               '- 10月内可完成剩余核心问题治理，风险不大。\n'
               '- 原文未明确提及：具体上线时间、资源投入及其他外部阻塞项。\n'
               '- **其他阻塞项与资源问题**：原文未明确提及\n'
               '- 来源：20261015-项目讨论-在线审核项目进度与风险对焦.txt\n'
               '- | KP3 | 139 款安卓小内配模型上线（解除工艺复核阻塞） |\n'
               '- 本次为在线审核项目正式启动后的进度与风险对焦会。\n')
    row = _risk_row('07-成员周报/202610/20261017-周报-w42-甲.md', snippet)
    assert SignalDetector(today=TODAY).kr_signals({'O1-KR1': 'x'}, {'O1-KR1': [row]}) == []


def test_kr_signals_surface_risk_sentence_and_merge_krs():
    snippet = ('### 3. 遇到的问题与风险\n\n'
               '- **爬虫批量返回“序列号无效”**：已做紧急处理，需调研备用渠道兜底，属于外部依赖风险。\n')
    row = _risk_row('07-成员周报/202610/20261017-周报-w42-甲.md', snippet)
    krs = {'O1-KR1': 'x', 'O1-KR2': 'x'}
    signals = SignalDetector(today=TODAY).kr_signals(krs, {k: [row] for k in krs})
    assert len(signals) == 1
    assert '爬虫' in signals[0].title
    assert 'O1-KR1' in signals[0].detail and 'O1-KR2' in signals[0].detail
    assert signals[0].sources == ['周报-w42-甲 · 09-21']


def test_kr_signals_dedupe_similar_sentences_in_same_document():
    snippet = ('### 风险\n\n'
               '- 共享目录回写存在风险。\n'
               '- 病毒问题：共享目录回写存在风险，需限制写入。\n')
    row = _risk_row('05-会议纪要/202610/20261019-部门周会.md', snippet)
    signals = SignalDetector(today=TODAY).kr_signals({'O1-KR2': 'x'}, {'O1-KR2': [row]})
    assert len(signals) == 1
    assert '病毒问题' in signals[0].title


def test_kr_signals_cap_two_risks_per_document():
    snippet = ('### 风险\n\n'
               '- 风险一：交付延期两周。\n'
               '- 风险二：接口联调受阻。\n'
               '- 风险三：资源不足导致阻塞。\n')
    row = _risk_row('05-会议纪要/202610/20261017-项目周会.md', snippet)
    signals = SignalDetector(today=TODAY).kr_signals({'O1-KR1': 'x'}, {'O1-KR1': [row]})
    assert len(signals) == 2


# ── 排序配额与跨日新鲜度 ────────────────────────────────────

def test_rank_keeps_kind_diversity():
    rows = [Signal('KR_AT_RISK', f'风险 {n}', [], '', 7, 4) for n in range(5)]
    rows.append(Signal('WEEKLY_MISSING', '张三 周报断档 15 天', [], '', 6, 3))
    picked = rank(rows)
    assert len(picked) == 5
    assert any(signal.kind == 'WEEKLY_MISSING' for signal in picked)


def test_history_marks_persistent_across_days(tmp_path):
    history = SignalHistory(tmp_path)
    signal = Signal('KR_AT_RISK', '风险', ['a.md'], '', 7, 4, stable_key='a.md|风险')
    history.record([signal], today=TODAY, pushed=True)
    annotations = history.annotate([signal], today=TODAY + timedelta(days=2))
    row = annotations[signal_key(signal)]
    assert row['new'] is False
    assert row['days'] == 3
    assert row['last_pushed'] == TODAY.isoformat()


def test_reminder_signals_mapped():
    rows = reminder_signals({'signals': [
        {'type': 'weekly_report_missing', 'target': '张三', 'days': 15, 'detail': '张三 最近一份周报是 9-06'},
        {'type': 'category_inactive', 'target': '01-目标管理', 'days': 69, 'detail': '栏目 69 天无更新'},
        {'type': 'unknown', 'target': 'x', 'days': 1},
    ]})
    assert [row.kind for row in rows] == ['WEEKLY_MISSING', 'SUPPLY_GAP']
    assert '15' in rows[0].title
    assert rows[0].stable_key == 'WEEKLY_MISSING|张三'


# ── 渲染 ──────────────────────────────────────────────────

def _render_signal():
    return Signal('KR_AT_RISK', '爬虫批量返回“序列号无效”',
                  ['07-成员周报/202610/20261017-周报-w42-甲.md'],
                  '确认风险与应对责任人', 7, 4,
                  detail='关联 KR：O1-KR1',
                  sources=['周报-w42-甲 · 10-17'],
                  stable_key='risk-1')


def test_render_groups_compacts_sources_and_shows_maintenance():
    signal = _render_signal()
    pending = [{
        'candidate_id': 'CAN-2cf17df11aa04615b7417e37da61c76f',
        'decision': {'title': '少拆机与少质检推进'},
        '_pending_days': 30, '_reason': '混合非拍板内容',
        '_ai_approved': False, '_orphan': False,
    }]
    text = render([signal], pending, day=TODAY,
                  annotations={signal_key(signal): {'new': False, 'days': 7}},
                  maintenance={'documents': 1041, 'rebuilt': 12, 'wiki_updated': 3,
                               'graph': '231 节点 / 2766 边', 'usage_calls': 82, 'issues': []})
    assert 'KR 风险' in text
    assert '周报-w42-甲 · 10-17' in text
    assert '持续 7 天' in text
    assert 'CAN-2cf17d' in text
    assert '07-成员周报' not in text
    assert '维护回执' in text
    assert '9月21日' in build_title(TODAY)


def test_render_surfaces_orphan_pending():
    pending = [
        {'candidate_id': 'CAN-aaaaaaaa', 'decision': {'title': 'A'},
         '_pending_days': 1, '_reason': 'r', '_ai_approved': False, '_orphan': True},
        {'candidate_id': 'CAN-bbbbbbbb', 'decision': {'title': 'B'},
         '_pending_days': 2, '_reason': 'r', '_ai_approved': False, '_orphan': False},
    ]
    text = render([], pending, day=TODAY)
    assert '1 条来源文档已不存在' in text
    assert 'CAN-aaaaaaaa' not in text


# ── 交付策略 ──────────────────────────────────────────────

def test_delivery_daily_policy_blocks_same_day_content_change(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    assert delivery.deliver('v1', day='2026-09-21', user_id='ou_1')['status'] == 'sent'
    result = delivery.deliver('v2', day='2026-09-21', user_id='ou_1')
    assert result['duplicate'] is True
    sender.assert_called_once()


def test_delivery_daily_policy_sends_next_day(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('v1', day='2026-09-21', user_id='ou_1')
    assert delivery.deliver('v1', day='2026-09-22', user_id='ou_1')['status'] == 'sent'
    assert sender.call_count == 2


def test_delivery_passes_post_title(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('正文', day='2026-09-21', user_id='ou_1', title='Iris 日报 · 9月21日（周一）')
    assert sender.call_args.kwargs['title'] == 'Iris 日报 · 9月21日（周一）'


def _seed_delivery_record(delivery, *, day, user_id='ou_1', text='摘要',
                          policy='daily', **fields):
    digest = hashlib.sha256((day + user_id + text).encode()).hexdigest()
    key = delivery._delivery_key(day, user_id, digest, policy)
    record = {'status': 'sending', 'file': str(delivery.root / f'{day}-{digest[:12]}.md'),
              'day': day, 'digest': digest, 'title': '', 'user_id': user_id}
    record.update(fields)
    delivery._record(key, record)
    return key


def test_delivery_preview_archive_naming(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1', dry_run=True)
    assert result['file'].endswith('-preview.md')
    assert Path(result['file']).exists()
    sender.assert_not_called()


def test_delivery_not_configured_writes_preview_only(tmp_path):
    result = SignalDelivery(tmp_path).deliver('摘要', day='2026-09-21')
    assert result['status'] == 'not_configured'
    files = list(tmp_path.glob('*.md'))
    assert len(files) == 1 and files[0].name.endswith('-preview.md')


def test_delivery_records_started_at(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    record = next(iter(delivery.store.read()['records'].values()))
    started = datetime.fromisoformat(record['started_at'])
    assert started.tzinfo is not None


def test_delivery_duplicate_reuses_previous_archive(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    first = delivery.deliver('v1', day='2026-09-21', user_id='ou_1')
    second = delivery.deliver('v2', day='2026-09-21', user_id='ou_1')
    assert second['duplicate'] is True
    assert second['file'] == first['file']
    assert len(list(tmp_path.glob('*.md'))) == 1
    sender.assert_called_once()


def test_delivery_stale_sending_becomes_uncertain(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    started_at = (datetime.now().astimezone() - timedelta(minutes=11)).isoformat()
    key = _seed_delivery_record(delivery, day='2026-09-21', started_at=started_at)
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'uncertain'
    assert result['duplicate'] is True
    assert '中断' in result['reason']
    sender.assert_not_called()
    assert delivery.store.read()['records'][key]['status'] == 'uncertain'


def test_delivery_fresh_sending_stays_sending(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    key = _seed_delivery_record(
        delivery, day='2026-09-21',
        started_at=datetime.now().astimezone().isoformat())
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'sending'
    assert result['duplicate'] is True
    sender.assert_not_called()
    assert delivery.store.read()['records'][key]['status'] == 'sending'


def test_delivery_legacy_sending_without_started_at_becomes_uncertain(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    _seed_delivery_record(delivery, day='2026-09-21')
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'uncertain'
    assert '中断' in result['reason']
    sender.assert_not_called()


def test_delivery_invalid_started_at_becomes_uncertain(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    _seed_delivery_record(delivery, day='2026-09-21', started_at='not-a-date')
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'uncertain'
    sender.assert_not_called()


def test_delivery_force_resends_after_sent(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1', force=True)
    assert result['status'] == 'sent'
    assert sender.call_count == 2


def test_delivery_force_resends_after_uncertain(tmp_path):
    sender = Mock(side_effect=[OSError('offline'), {'message_id': 'om_2'}])
    delivery = SignalDelivery(tmp_path, sender)
    assert delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')['status'] == 'uncertain'
    assert delivery.deliver('摘要', day='2026-09-21', user_id='ou_1', force=True)['status'] == 'sent'
    assert sender.call_count == 2


def test_delivery_force_every_run_same_content(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('摘要', day='2026-09-21', user_id='ou_1', policy='every_run')
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1',
                              policy='every_run', force=True)
    assert result['status'] == 'sent'
    assert sender.call_count == 2


def test_delivery_force_dry_run_previews_only(tmp_path):
    sender = Mock(return_value={'message_id': 'om_1'})
    delivery = SignalDelivery(tmp_path, sender)
    delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1',
                              dry_run=True, force=True)
    assert result['status'] == 'dry_run'
    assert sender.call_count == 1


def test_delivery_force_without_user_is_not_configured(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    result = delivery.deliver('摘要', day='2026-09-21', user_id='', force=True)
    assert result['status'] == 'not_configured'
    sender.assert_not_called()


def test_delivery_every_run_stale_sending_becomes_uncertain(tmp_path):
    sender = Mock()
    delivery = SignalDelivery(tmp_path, sender)
    started_at = (datetime.now().astimezone() - timedelta(minutes=11)).isoformat()
    key = _seed_delivery_record(delivery, day='2026-09-21', policy='every_run',
                                started_at=started_at)
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1', policy='every_run')
    assert result['status'] == 'uncertain'
    assert result['duplicate'] is True
    sender.assert_not_called()
    assert delivery.store.read()['records'][key]['status'] == 'uncertain'


def test_delivery_uncertain_keeps_attempted_archive(tmp_path):
    sender = Mock(side_effect=OSError('offline'))
    delivery = SignalDelivery(tmp_path, sender)
    result = delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    assert result['status'] == 'uncertain'
    archive = Path(result['file'])
    assert archive.exists() and not archive.name.endswith('-preview.md')


def test_send_lark_builds_post_payload_with_title(monkeypatch):
    from iris.signals import delivery as delivery_module
    calls = []

    class Result:
        returncode = 0
        stdout = '{"ok": true, "identity": "bot", "data": {}}'
        stderr = ''

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(delivery_module.subprocess, 'run', fake_run)
    delivery_module.send_lark('ou_1', '正文', 'key', title='Iris 日报 · 9月21日（周一）')
    args = calls[0]
    assert '--msg-type' in args and args[args.index('--msg-type') + 1] == 'post'
    content = json.loads(args[args.index('--content') + 1])
    assert content['zh_cn']['title'] == 'Iris 日报 · 9月21日（周一）'
    assert content['zh_cn']['content'][0][0]['tag'] == 'md'
    assert content['zh_cn']['content'][0][0]['text'] == '正文'


# ── 决策待审原因 ────────────────────────────────────────────

def test_pending_reason_does_not_reuse_approval_reason():
    row = {'candidate_id': 'CAN-x', 'status': 'pending', 'decision': {
        'title': '明确决策', 'outcome': 'o', 'decided_at': '2026-09-21',
        'review_details': {
            'reason': '明确拍板决策，数据完整无矛盾。',
            'review': {'verdict': 'approved', 'supported_fields': ['outcome', 'decided_at']},
        },
    }}
    assert pending_reason(row) == 'AI 已复核通过，待人工确认'


def test_annotate_pending_marks_ai_approved_age_and_orphan(tmp_path):
    src = tmp_path / 'SOURCE'
    (src / 'docs').mkdir(parents=True)
    (src / 'docs' / 'ok.md').write_text('x', encoding='utf-8')
    base = {
        'created_at': '2026-09-01T10:00:00+08:00',
        'review_details': {'review': {'verdict': 'approved',
                                      'supported_fields': ['outcome', 'decided_at']}},
    }
    rows = [
        {'candidate_id': 'CAN-ok', 'decision': {
            **base, 'title': 'A', 'outcome': 'o', 'decided_at': '2026-09-01',
            'sources': [{'path': 'docs/ok.md'}]}},
        {'candidate_id': 'CAN-orphan', 'decision': {
            **base, 'title': 'B', 'outcome': 'o', 'decided_at': '2026-09-01',
            'sources': [{'path': 'docs/missing.md'}]}},
    ]
    out = {row['candidate_id']: row for row in _annotate_pending(rows, src, TODAY)}
    assert out['CAN-ok']['_ai_approved'] is True
    assert out['CAN-ok']['_pending_days'] == 20
    assert out['CAN-ok']['_orphan'] is False
    assert out['CAN-orphan']['_orphan'] is True


def test_pending_reason_reports_missing_critical_field():
    row = {'candidate_id': 'CAN-x', 'status': 'pending', 'decision': {
        'title': '决策', 'outcome': 'o', 'decided_at': '2026-09-21', 'owners': ['张三'],
        'review_details': {
            'review': {'verdict': 'approved', 'supported_fields': ['outcome', 'decided_at']},
        },
    }}
    assert pending_reason(row) == 'AI 已复核通过，待补原文依据：责任人'
