from iris_caption.session import Session
from iris_caption.export import render_markdown, render_srt
from iris_caption.statistics import session_snapshot


def test_interruption_preserves_text_without_counting_as_final():
    session = Session(speakers_enabled=True)
    event = dict(type='partial', session_id=session.id, segment_id='1',
                 start_ms=100, end_ms=900, text='未说完的话', revision=2)
    segment = session.apply_audio(event)
    interrupted = session.interrupt_partials()
    assert interrupted == [segment]
    assert segment.raw == '未说完的话'
    assert segment.status == 'interrupted'
    assert segment.speaker_state == 'unknown'
    assert session_snapshot(session)['finalized_segments'] == 0
    assert '转写中断' in render_markdown(session)
    assert '未说完的话' in render_srt(session)
    assert '未定稿' in render_srt(session)
    assert session.apply_audio(dict(event, type='final', text='late')) is None
    assert session.interrupt_partials() == []


def test_interruption_does_not_change_completed_segments():
    session = Session()
    segment = session.apply_audio(dict(type='final', session_id=session.id,
        segment_id='1', start_ms=0, end_ms=1000, text='完成', revision=1))
    assert session.interrupt_partials() == []
    assert segment.status == 'pending'
    assert session_snapshot(session)['finalized_segments'] == 1
