"""No devices or model access: exercise the real pending-worker event routing."""

from types import SimpleNamespace
from unittest.mock import Mock
import queue

from PySide6.QtCore import QObject
import pytest

from iris_caption import controller as module
from iris_caption.session import Session
from iris_caption.export import render_markdown


@pytest.fixture
def capture(app, monkeypatch, tmp_path):
    c = module.Controller.__new__(module.Controller)
    QObject.__init__(c)
    c.window = Mock(closing_after_save=False)
    c.session = Session(speakers_enabled=True)
    c.session.apply_audio(
        dict(
            type="final", session_id=c.session.id, segment_id="8", start_ms=100, end_ms=900, text="已确认记录"
        )
    )
    c.session.apply_audio(
        dict(
            type="partial",
            session_id=c.session.id,
            segment_id="9",
            start_ms=900,
            end_ms=1690,
            text="中断的句子",
        )
    )
    c.session.interrupt_partials()
    c.capture_state, c._capture_pending = "error", None
    c._closing = c.paused = False
    c.process = c.commands = c.audio_events = c._deferred_export = None
    c.service = Mock()
    c.tasks, c.retry_tasks = {"8": object()}, {"8": object()}
    c.settings = {"model_path": str(tmp_path), "speakers_enabled": True}
    c.knowledge = Mock()
    c.knowledge.active_bundle.return_value = {}
    c.rules = Mock()
    c._reset_materials = Mock()
    c.background = "本场材料"
    c._update_statistics = Mock()
    c._schedule = Mock()
    c._stats_started_at = "old"
    c.results = queue.Queue()
    child = Mock()
    child.is_alive.return_value = True
    child.exitcode = 0
    monkeypatch.setattr(
        module.mp,
        "get_context",
        lambda _: SimpleNamespace(Queue=lambda **kw: queue.Queue(**kw), Process=lambda **kw: child),
    )
    monkeypatch.setattr(module, "resolve_vad_model_path", lambda _: "/vad")
    monkeypatch.setattr(module, "resolve_speaker_model_path", lambda _: "/speaker")
    monkeypatch.setattr(
        module.QMessageBox, "question", lambda *args: module.QMessageBox.StandardButton.Discard
    )
    return c, child


def emit(c, wire, kind, **fields):
    c.audio_events.put(dict(type=kind, session_id=wire, **fields))
    c.poll()


@pytest.mark.parametrize("new", [True, False])
def test_two_failed_readiness_attempts_keep_exportable_session(capture, new):
    c, child = capture
    previous = c.session
    for _ in range(2):
        child.is_alive.return_value = True
        (c.start_new_meeting if new else c.start)()
        wire = c._attempt.wire_id
        emit(c, wire, "speaker_status", text="pending candidate speaker")
        emit(c, wire, "partial", segment_id="1", revision=1, start_ms=0, end_ms=20, text="候选初稿")
        emit(c, wire, "error", text="model unavailable")
        emit(c, wire, "stopped")
        child.is_alive.return_value = False
        c.poll()
        assert c.session is previous and c.capture_state == "error"
        assert list(previous.segments) == ["8", "9"]
        assert all(t in render_markdown(previous) for t in ("已确认记录", "转写中断", "中断的句子"))
        assert c.tasks.keys() == c.retry_tasks.keys() == {"8"}
        assert c.background == "本场材料" and c._stats_started_at == "old"
    c.window.clear_segments.assert_not_called()
    c.service.cancel.assert_not_called()
    c.rules.clear_session.assert_not_called()
    c._reset_materials.assert_not_called()


def test_recovery_continues_clock_ids_and_isolates_old_worker_and_speaker(capture):
    c, child = capture
    original = c.session
    original.assign_speaker("8", "A")
    c.start()
    wire1 = c._attempt.wire_id
    emit(c, wire1, "status", state="recording")
    emit(
        c,
        wire1,
        "final",
        segment_id="1",
        revision=2,
        start_ms=5,
        end_ms=505,
        recognition_start_ms=1,
        text="续录一",
    )
    emit(
        c,
        wire1,
        "speaker",
        segment_id="1",
        audio_revision=2,
        speaker_id="A",
        speaker_state="confirmed",
        overlap_intervals=[[100, 300]],
    )
    a = c.session.segments["10"]
    assert (a.start_ms, a.end_ms, a.recognition_start_ms) == (1695, 2195, 1691)
    assert a.overlap_intervals == [[1790, 1990]] and a.speaker_id != "A"
    assert c.session is original and c._stats_started_at == "old"
    emit(c, wire1, "error", text="another failure")
    child.is_alive.return_value = False
    c.poll()
    child.is_alive.return_value = True
    c.start()
    wire2 = c._attempt.wire_id
    assert wire1 != wire2
    emit(c, wire1, "status", state="recording")
    assert c.capture_state == "loading"
    emit(c, wire2, "status", state="recording")
    emit(c, wire1, "error", text="stale")
    assert c.capture_state == "recording"
    emit(c, wire1, "final", segment_id="1", start_ms=0, end_ms=100, text="stale")
    emit(c, wire2, "final", segment_id="1", start_ms=0, end_ms=200, text="续录二")
    assert [(s.id, s.start_ms, s.end_ms) for s in c.session.ordered()][-2:] == [
        ("10", 1695, 2195),
        ("11", 2195, 2395),
    ]
    c.rules.clear_session.assert_not_called()
    c.window.clear_segments.assert_not_called()


def test_new_meeting_switches_only_on_ready_and_can_export_first(capture, monkeypatch):
    c, child = capture
    previous = c.session
    monkeypatch.setattr(module.QMessageBox, "question", lambda *args: module.QMessageBox.StandardButton.Save)
    c.export = Mock(return_value=False)
    c.start_new_meeting()
    assert c.process is None and c.session is previous
    c.export.return_value = True
    c.start_new_meeting()
    assert c.process is child and c.session is previous
    wire = c._attempt.wire_id
    emit(c, wire, "status", state="loading")
    assert c.session is previous
    emit(c, wire, "status", state="recording")
    assert c.session is not previous and not c.session.segments
    c._reset_materials.assert_called_once()
    c.rules.clear_session.assert_called_once()
    assert not c.tasks and not c.retry_tasks
    c.window.clear_segments.assert_called_once()
    c.service.cancel.assert_called_once_with(previous.id)
