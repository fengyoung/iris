import pytest

from iris_caption.session import Session
from iris_caption.export import render_markdown, render_srt


def final(session, sid='one'):
    return session.apply_audio(dict(type='final', session_id=session.id,
                                   segment_id=sid, start_ms=10, end_ms=1500, text='原文'))


def test_old_sessions_and_late_corrections_do_not_overwrite_edits():
    session = Session()
    segment = final(session)
    assert segment
    event = dict(type='correction', session_id=session.id, segment_id='one',
                 revision=segment.revision, corrected_text='自动')
    session.edit('one', '人工')
    assert session.apply_correction(event) is None
    assert session.segments['one'].corrected == '人工'
    event['session_id'] = 'old'
    assert session.apply_correction(event) is None


def test_final_raw_is_immutable_and_context_revision_bound():
    session = Session(background_version=3)
    segment = final(session)
    assert segment.background_version == 3
    assert final(session) is None
    assert session.apply_audio(dict(type='partial', session_id='other')) is None


def test_partial_cannot_be_edited_and_final_is_preserved():
    session = Session()
    session.apply_audio(dict(type='partial', session_id=session.id, segment_id='one',
                             start_ms=0, end_ms=500, text='初稿'))
    with pytest.raises(ValueError):
        session.edit('one', '人工')
    assert final(session).raw == '原文'


def test_export_srt_modes_and_unfinished_fallback():
    session = Session(title='会议')
    final(session)
    assert '00:00:00,010 --> 00:00:01,500' in render_srt(session)
    assert '原文' in render_srt(session)
    session.edit('one', '修正')
    assert '原文：原文\n校正：修正' in render_srt(session, 'both')
    assert '修正' not in render_srt(session, 'raw')
    assert '**原文**' in render_markdown(session)
    assert '**校正**' in render_markdown(session)
