"""Snapshot/export contracts: synthetic memory-only audio and speaker events."""
import pytest

from iris_caption.export import export_session, render_markdown, render_srt
from iris_caption.session import Session


def add(s, sid, kind, text, **extra):
    return s.apply_audio(dict(type=kind, session_id=s.id, segment_id=sid,
                              start_ms=100, end_ms=900, revision=2, text=text, **extra))


def test_live_snapshot_srt_filter_and_rescue(tmp_path):
    s = Session()
    add(s, '1', 'partial', '断句')
    s.interrupt_partials()
    s.edit('1', '人工整理的断句')
    add(s, '2', 'partial', '半句话')
    assert s.segments['1'].status == 'interrupted'
    file = tmp_path / 'out.srt'
    file.write_text('之前的文件')
    with pytest.raises(ValueError, match='没有已定稿字幕'):
        export_session(s, file, 'srt')
    assert file.read_text() == '之前的文件'
    add(s, '3', 'final', '已经说完')
    assert '实时快照' in render_markdown(s)
    md = render_markdown(s)
    assert '转写中断' in md and '实时初稿' in md and '人工整理的断句' in md
    assert '本地结果（转写已定稿，未完成云端校正）' in md
    default = render_srt(s)
    assert default.startswith('1\n') and '已经说完' in default
    assert '断句' not in default and '半句话' not in default
    export_session(s, file, 'srt', include_unfinalized=True)
    rescue = file.read_text()
    assert '实时初稿 · 未定稿' in rescue and '转写中断 · 未定稿' in rescue
    assert '人工整理的断句' in rescue and '半句话' in rescue
    assert '\n3\n' in rescue


def test_live_export_between_utterances(tmp_path):
    s = Session()
    add(s, '1', 'final', '离线定稿')
    assert '实时快照' not in render_markdown(s)
    path = tmp_path / 'live.md'
    export_session(s, path, 'markdown', live_snapshot=True)
    assert '实时快照 · 字幕截至 00:00:00.900' in path.read_text()


def test_boundary_aggregate_accepts_audio_reason_without_private_content():
    s = Session()
    add(s, '1', 'final', '隐私', reason='limit')
    add(s, '2', 'final', '隐私', reason='limit', boundary_reason='speaker')
    add(s, '3', 'final', '隐私', reason='私人会议内容')
    add(s, '4', 'partial', '隐私', reason='pause')
    assert s.segments['1'].boundary_reason == 'limit'
    assert s.boundary_diagnostics() == {'limit': 1, 'speaker': 1, 'unknown': 1}


@pytest.mark.parametrize('intervals', [[], [[200, 400]]])
def test_manual_lock_preserves_same_revision_overlap(intervals):
    s = Session(speakers_enabled=True)
    seg = add(s, '1', 'final', '李四在讲话')
    s.assign_speaker('1', 'A')
    s.rename_speaker('A', '李四')
    event = dict(type='speaker', session_id=s.id, segment_id='1', audio_revision=2,
                 speaker_state='overlap', speaker_id='B', overlap_intervals=intervals)
    assert s.apply_speaker(dict(event, audio_revision=1)) is None and not seg.has_overlap
    assert s.apply_speaker(event) is seg
    assert (seg.speaker_id, seg.speaker_name, seg.speaker_manual) == ('A', '李四', True)
    assert seg.view()['has_overlap'] and seg.overlap_intervals == intervals
    assert s.apply_speaker(dict(event, speaker_state='unknown', overlap_intervals=[])) is None
    for doc in (render_markdown(s), render_srt(s)):
        assert '[李四]' in doc and '[人工确认]' in doc and '[重叠语音]' in doc
