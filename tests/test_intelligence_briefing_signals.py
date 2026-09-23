"""情报引用、鲜度、信号边界和交付幂等性。"""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from iris.core.exceptions import IrisValueError
from iris.intelligence.context import SourceDoc, read_document
from iris.briefing.open_issue_detector import OpenIssueDetector
from iris.briefing.synthesizer import BriefingSynthesizer
from iris.signals.detector import SignalDetector, Signal
from iris.signals.ranker import rank
from iris.signals.delivery import SignalDelivery
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
    delivery.deliver('摘要', day='2026-09-21', user_id='ou_1')
    sender.assert_called_once()


def test_delivery_dry_run(tmp_path):
    sender = Mock()
    assert SignalDelivery(tmp_path, sender).deliver('摘要', day='2026-09-21', user_id='ou_1', dry_run=True)['status'] == 'dry_run'
    sender.assert_not_called()


def test_no_credentials_visible_failure(tmp_path):
    assert SignalDelivery(tmp_path).deliver('摘要', day='2026-09-21')['status'] == 'not_configured'
