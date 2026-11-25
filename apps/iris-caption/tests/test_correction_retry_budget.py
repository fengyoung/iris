"""Offline retry budgets from transport to controller; no microphone, HTTP or secrets."""
from queue import Queue
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from iris_caption.controller import Controller
from iris_caption.correction import CorrectionError, CorrectionService
from iris_caption.session import Session


@pytest.fixture
def case(app):
    c = Controller.__new__(Controller)
    QObject.__init__(c)
    c.session, c.window, c.settings = Session(), Mock(), {}
    c._closing, c._service_generation = False, 1
    c.tasks, c.retry_tasks, c.results = {}, {}, Queue()
    c.service = None
    c._update_statistics = Mock()
    seg = c.session.apply_audio(dict(type='final', session_id=c.session.id, segment_id='1',
                                     start_ms=0, end_ms=1, text='本地文本'))
    seg.corrected = seg.raw
    ready = dict(type='task_ready', task=dict(session_id=c.session.id, segment_id=seg.id,
                                              revision=seg.revision, text=seg.raw))
    yield c, seg, ready
    if c.service:
        c.service.stop()


def test_transport_exhaustion_is_terminal_until_one_manual_round(case):
    c, seg, ready = case
    transport = Mock(side_effect=CorrectionError('rate_limit', True))
    c.service = CorrectionService('unused', lambda event: c.results.put(dict(event, service_generation=1)),
                                  transport=transport, max_retries=2, retry_delay=0).start()
    c._event(ready)
    c._event(ready)  # duplicated asynchronous ready event
    assert seg.status == 'queued'
    old = c.results.get(timeout=2)
    c._event(old)
    assert seg.status == 'retry_exhausted' and seg.corrected == '本地文本'
    for _ in range(10):
        c.retry_pending()
    assert transport.call_count == 3 and not c.retry_tasks
    transport.side_effect, transport.return_value = None, '本地文本。'
    c.retry_correction(seg.id)
    c.retry_correction(seg.id)  # double click while the first request is in flight
    c._event(old)  # previous revision cannot terminate a new request
    assert seg.status == 'queued'
    c._event(c.results.get(timeout=2))
    assert seg.status == 'corrected' and seg.corrected == '本地文本。'
    c.retry_correction(seg.id)
    assert transport.call_count == 4 and not c.tasks


def test_queue_admission_is_bounded_separately_from_transport(case):
    c, seg, ready = case
    c.service = Mock()
    c.service.submit.return_value = False
    c._event(ready)
    for _ in range(8):
        c.retry_pending()
    assert c.service.submit.call_count == 3 and seg.status == 'retry_exhausted'
    assert not c.retry_tasks and seg.corrected == seg.raw
    c.service.submit.return_value = True
    c.retry_correction(seg.id)
    c.retry_pending()
    assert c.service.submit.call_count == 4 and seg.status == 'queued'


def test_generation_change_does_not_refresh_inflight_budget(case, monkeypatch):
    c, seg, ready = case
    c.service = Mock()
    c.service.submit.return_value = True
    c._event(ready)
    replacement = Mock()
    monkeypatch.setattr('iris_caption.controller.available_key', lambda: 'unused')
    monkeypatch.setattr('iris_caption.controller.CorrectionService', lambda *a, **kw: replacement)
    c._init_service()
    assert seg.status == 'retry_required'
    c.retry_pending()
    replacement.submit.assert_not_called()
    c.retry_correction(seg.id)
    assert replacement.submit.call_count == 1
    c._event(dict(type='correction', session_id=c.session.id, segment_id=seg.id,
                  revision=seg.revision, service_generation=1, corrected_text='wrong generation'))
    assert seg.corrected == '本地文本'
    rev = seg.revision
    c.session.edit(seg.id, '人工改写')
    c._event(dict(type='correction', session_id=c.session.id, segment_id=seg.id,
                  revision=rev, service_generation=2, corrected_text='wrong revision'))
    c.retry_correction(seg.id)
    assert seg.status == 'manual' and seg.corrected == '人工改写' and replacement.submit.call_count == 1


def test_no_service_requires_configuration_before_manual_retry(case):
    c, seg, ready = case
    c._event(ready)
    assert seg.status == 'local'
    rev = seg.revision
    c.retry_correction(seg.id)
    c.window.set_status.assert_called_once()
    assert seg.revision == rev and seg.status == 'local'
