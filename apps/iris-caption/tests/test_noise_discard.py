"""A noise rejection may retract only unconfirmed text from the same meeting."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from iris_caption.session import Session


def test_discard_requires_matching_session_and_partial():
    session = Session()
    event = dict(session_id=session.id, type='partial', segment_id='1', start_ms=0, end_ms=1000, text='noise')
    session.apply_audio(event)
    discard = dict(event, type='discard')
    assert not session.discard_partial(dict(discard, session_id='other'))
    assert session.discard_partial(discard)
    assert not session.segments and not session.dirty
    assert not session.discard_partial(discard)
    session.apply_audio(dict(event, type='final', text='这这这确实是重复讲话'))
    assert not session.discard_partial(discard)
    session.edit('1', '人工确认')
    assert not session.discard_partial(discard)


def test_rejected_partial_removed_from_ui():
    from PySide6.QtWidgets import QApplication
    from iris_caption.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    item = dict(id='1',start_ms=0,end_ms=1000,raw='noise',corrected='',status='partial',manual=False)
    window.upsert_segment(item)
    window.remove_segment('1')
    assert not window._segments and not window._cards and not window._dirty
    assert not window.export_button.isEnabled()
    window.upsert_segment(dict(item,status='pending'))
    window.remove_segment('1')
    assert '1' in window._segments
    window.mark_saved()
    window.close()
    app.processEvents()
