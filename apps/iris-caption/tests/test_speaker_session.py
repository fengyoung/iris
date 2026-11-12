import pytest

from iris_caption.export import render_markdown, render_srt
from iris_caption.session import Segment, Session


def audio(session, sid='one', **metadata):
    event = dict(type='final', session_id=session.id, segment_id=sid,
                 start_ms=100, end_ms=1000, text='原文', audio_revision=7)
    event.update(metadata)
    return session.apply_audio(event)


def speaker(session, sid='one', **metadata):
    event = dict(session_id=session.id, segment_id=sid, audio_revision=7,
                 speaker_id='voice-1', speaker_state='confirmed')
    event.update(metadata)
    return session.apply_speaker(event)


def test_backward_defaults_and_off_exports():
    session = Session()
    segment = audio(session)
    assert segment.speaker_state == 'off'
    assert segment.speaker_name == ''
    assert segment.overlap_intervals == []
    assert speaker(session) is None
    before = render_markdown(session), render_srt(session)
    session.assign_speaker('one', 'private-id')
    assert segment.speaker_name == ''
    assert before == (render_markdown(session), render_srt(session))
    assert Segment('two', 0, 1).overlap_intervals is not segment.overlap_intervals


def test_metadata_allowlist_and_owned_timing():
    session = Session()
    segment = audio(session, owned_start_ms=200, owned_end_ms=900,
                    recognition_start_ms=0, recognition_end_ms=1100,
                    boundary_reason='silence', boundary_alignment='vad',
                    overlap_intervals=[[250, 400], [-1, 1], [5, 4], 'bad'],
                    speaker_manual=True, manual=True, corrected='injected')
    assert (segment.start_ms, segment.end_ms) == (200, 900)
    assert (segment.recognition_start_ms, segment.recognition_end_ms) == (0, 1100)
    assert segment.boundary_reason == 'silence'
    assert segment.boundary_alignment == 'vad'
    assert segment.overlap_intervals == [[250, 400]]
    assert not segment.manual and not segment.speaker_manual and not segment.corrected


def test_audio_revision_is_independent_from_text_edits_and_corrections():
    session = Session(speakers_enabled=True)
    segment = audio(session)
    session.edit('one', '人工')
    assert segment.revision == 2 and segment.audio_revision == 7
    assert speaker(session) is segment
    assert segment.corrected == '人工' and segment.manual
    assert speaker(session, audio_revision=2) is None
    assert speaker(session, audio_revision=6) is None
    assert speaker(session, audio_revision=True) is None
    assert speaker(session, session_id='old') is None
    assert speaker(session, sid='missing') is None
    assert audio(session, text='late final') is None
    assert segment.raw == '原文'


def test_partial_revisions_and_speaker_results():
    session = Session(speakers_enabled=True)
    segment = audio(session, type='partial', audio_revision=1)
    assert speaker(session, audio_revision=1) is segment
    assert audio(session, type='partial', audio_revision=0) is None
    assert audio(session, audio_revision=2) is segment
    assert speaker(session, audio_revision=1) is None
    assert segment.speaker_state == 'pending' and segment.speaker_id is None
    assert speaker(session, audio_revision=2, speaker_id='voice-2') is segment
    assert segment.speaker_id == 'voice-2'


@pytest.mark.parametrize('assigned', ['voice-2', None])
def test_manual_speaker_lock_survives_audio_and_speaker_results(assigned):
    session = Session(speakers_enabled=True)
    segment = audio(session, type='partial')
    session.assign_speaker('one', assigned)
    audio(session, audio_revision=8, speaker_id='voice-1', speaker_state='confirmed')
    assert segment.speaker_id == assigned and segment.speaker_manual
    assert speaker(session, audio_revision=8) is None
    assert segment.speaker_state == ('confirmed' if assigned else 'unknown')


def test_rename_dynamic_labels_and_new_session_reset():
    session = Session(speakers_enabled=True)
    first = audio(session)
    second = audio(session, sid='two')
    speaker(session)
    speaker(session, sid='two')
    assert first.speaker_name == second.speaker_name == '说话人 A'
    session.rename_speaker('voice-1', '  张三  ')
    assert first.view()['speaker_name'] == second.speaker_name == '张三'
    session.assign_speaker('two', 'voice-2')
    assert second.speaker_name == '说话人 B'
    session.set_speakers_enabled(False)
    assert first.speaker_name == second.speaker_name == ''
    assert '张三' not in render_markdown(session)
    session.set_speakers_enabled(True)
    assert first.speaker_name == '张三'
    new = Session(speakers_enabled=True)
    assert not new.speaker_names and new.id != session.id
    audio(new)
    speaker(new)
    assert new.segments['one'].speaker_name == '说话人 A'
    assert first.speaker_name == '张三'


@pytest.mark.parametrize('name', ['', '   ', 'x' * 81, 'a\nb', None])
def test_rename_validation(name):
    session = Session(speakers_enabled=True)
    audio(session)
    speaker(session)
    with pytest.raises(ValueError):
        session.rename_speaker('voice-1', name)
    with pytest.raises(ValueError):
        session.rename_speaker('absent', 'valid')


@pytest.mark.parametrize('state,tag', [('confirmed', '说话人 A'),
    ('pending', '说话人待识别'), ('unknown', '说话人未知'), ('overlap', '重叠语音')])
def test_export_labels_uncertainty_and_overlap(state, tag):
    session = Session(speakers_enabled=True)
    audio(session)
    speaker(session, speaker_state=state)
    for render in (render_markdown, render_srt):
        output = render(session)
        assert f'[{tag}]' in output
        if state != 'confirmed':
            assert '说话人 A' not in output
    speaker(session, speaker_state='confirmed', overlap_intervals=[[300, 500]])
    assert '[重叠语音]' in render_srt(session)
    speaker(session, speaker_state='off')
    assert '说话人 A' not in render_srt(session)


def test_reading_groups_preserve_ids_and_exclude_uncertainty():
    session = Session(speakers_enabled=True)
    for index in range(4):
        audio(session, str(index), start_ms=index * 1000, end_ms=(index + 1) * 1000)
        speaker(session, str(index))
    assert [[s.id for s in group] for group in session.reading_groups()] == [['0', '1', '2', '3']]
    speaker(session, '1', speaker_state='unknown')
    assert [len(group) for group in session.reading_groups()] == [1, 1, 2]
    speaker(session, '2', overlap_intervals=[[2200, 2400]])
    assert [len(group) for group in session.reading_groups()] == [1, 1, 1, 1]
    assert len(session.segments) == 4


def test_names_never_enter_statistics_snapshot():
    from iris_caption.statistics import session_snapshot

    session = Session(speakers_enabled=True)
    audio(session)
    speaker(session)
    session.rename_speaker('voice-1', '秘密姓名')
    snapshot = session_snapshot(session)
    assert '秘密姓名' not in repr(snapshot)
    assert 'voice-1' not in repr(snapshot)
    assert snapshot['finalized_segments'] == 1


def test_malformed_states_and_missing_revision_are_ignored():
    session = Session(speakers_enabled=True)
    audio(session, speaker_state=['confirmed'])
    assert speaker(session, speaker_state=[]) is None
    assert speaker(session, audio_revision=None) is None
    assert speaker(session, speaker_id=None).speaker_state == 'unknown'
