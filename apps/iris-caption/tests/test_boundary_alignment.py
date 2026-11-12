"""No hardware/model: ownership, ambiguity and bounded matching contracts."""
import pytest

from iris_caption.boundary_alignment import DecodeWindow, reconcile_final


def previous(text="今天讨论方案"):
    return DecodeWindow(text, 0, 16000, 0)


def contextual(text="方案明天实施"):
    return DecodeWindow(text, 9600, 32000, 16000)


def test_no_context_keeps_all_repetitions():
    result = reconcile_final(DecodeWindow("对对对不不是", 0, 16000, 0))
    assert result.text == "对对对不不是"
    assert result.status == "no_overlap"
    assert not result.needs_owned_decode


def test_unique_match_is_not_permission_to_delete():
    result = reconcile_final(contextual(), previous=previous())
    assert result.text is None
    assert result.needs_owned_decode and result.uncertain
    assert result.candidate_trim == 2
    assert result.evidence == "text_candidate"


def test_optional_exact_overlap_anchor_still_requires_owned_decode():
    anchor = DecodeWindow("方案", 9600, 16000, 9600)
    result = reconcile_final(contextual(), previous=previous(), overlap=anchor)
    assert result.text is None
    assert result.evidence == "anchored_candidate"


def test_candidate_agrees_with_independent_owned_transcript():
    result = reconcile_final(contextual(), previous=previous(), owned_text="明天实施")
    assert result.text == "明天实施"
    assert result.status == "owned_agreement"
    assert not result.needs_owned_decode
    assert not result.uncertain


def test_legitimate_repeated_phrase_is_not_trimmed():
    # Speaker really says: 今天讨论方案 / 方案明天实施.
    result = reconcile_final(contextual(), previous=previous(), owned_text="方案明天实施")
    assert previous().text + result.text == "今天讨论方案方案明天实施"
    assert result.status == "owned_agreement"


@pytest.mark.parametrize("left,right,owned", [
    ("对对", "对对对就是", "对对对就是"),
    ("不", "不不是这样", "不不是这样"),
    ("可以可以", "可以可以再说", "可以再说"),
    ("方案", "方案方案明天实施", "方案明天实施"),
])
def test_repetition_ambiguity_never_discards_owned_text(left, right, owned):
    result = reconcile_final(contextual(right), previous=previous(left), owned_text=owned)
    assert result.text == owned
    assert result.candidate_trim == 0
    assert result.evidence == "ambiguous_repetition"


def test_unmatched_uses_owned_text_not_unknown_marker():
    result = reconcile_final(contextual("错误上下文"), previous=previous(), owned_text="不不是")
    assert result.text == "不不是"
    assert result.status == "owned_fallback"
    assert result.uncertain and result.evidence == "unmatched"


def test_owned_empty_is_not_missing_and_drops_context_only_hypothesis():
    result = reconcile_final(contextual(), owned_text="")
    assert result.text == ""
    assert not result.needs_owned_decode


@pytest.mark.parametrize("anchor,evidence", [
    (DecodeWindow("方案", 9500, 16000, 9500), "anchor_wrong_interval"),
    (DecodeWindow("别的", 9600, 16000, 9600), "anchor_mismatch"),
])
def test_anchor_provenance(anchor, evidence):
    result = reconcile_final(contextual(), previous=previous(), overlap=anchor)
    assert result.text is None and result.candidate_trim == 0
    assert result.evidence == evidence


def test_nonadjacent_text_never_matches():
    result = reconcile_final(contextual(), previous=DecodeWindow("方案", 0, 9000, 0))
    assert result.evidence == "nonadjacent"
    assert result.candidate_trim == 0


def test_previous_must_cover_shared_audio():
    result = reconcile_final(contextual(), previous=DecodeWindow("方案", 10000, 16000, 10000))
    assert result.evidence == "uncovered_overlap"


def test_matching_is_bounded_and_never_searches_whole_text():
    result = reconcile_final(contextual(), previous=previous(), max_chars=1)
    assert result.candidate_trim == 0
    assert result.evidence == "unmatched"
    long = "甲" * 10000 + "方案"
    result = reconcile_final(contextual(), previous=previous(long), owned_text="明天实施")
    assert result.candidate_trim == 2


def test_no_fuzzy_or_punctuation_normalization():
    result = reconcile_final(contextual("方案，明天实施"), previous=previous("方案。"))
    assert result.evidence == "unmatched"


@pytest.mark.parametrize("args", [(0, 10, -1), (2, 10, 1), (0, 10, 11), (0.0, 10, 0)])
def test_invalid_sample_intervals(args):
    with pytest.raises(ValueError):
        DecodeWindow("", *args)


@pytest.mark.parametrize("kwargs", [{"max_chars": 0}, {"max_chars": 257},
                                    {"max_overlap_samples": 0}, {"owned_text": 42}])
def test_invalid_limits_and_decode_type(kwargs):
    with pytest.raises(ValueError):
        reconcile_final(contextual(), **kwargs)


def test_overlap_limit_and_empty_owned_interval():
    with pytest.raises(ValueError, match="overlap bound"):
        reconcile_final(contextual(), max_overlap_samples=6399)
    result = reconcile_final(DecodeWindow("方案", 0, 6400, 6400), owned_text="")
    assert result.text == ""


def test_same_evidence_supports_two_different_real_utterances():
    # Text-only candidate cannot distinguish duplication from a real repetition.
    a = reconcile_final(contextual(), previous=previous(), owned_text="明天实施")
    b = reconcile_final(contextual(), previous=previous(), owned_text="方案明天实施")
    assert a.candidate_trim == b.candidate_trim == 2
    assert a.text != b.text
    assert a.text == "明天实施" and b.text == "方案明天实施"


def test_sequential_boundaries_do_not_need_deferred_previous_final():
    texts = ["对对对", "不不是", "方案方案"]
    finals = []
    prev = None
    for index, text in enumerate(texts):
        owned_start = index * 16000
        window = DecodeWindow("前文" + text, max(0, owned_start - 6400),
                              owned_start + 16000, owned_start)
        result = reconcile_final(window, previous=prev, owned_text=text)
        finals.append(result.text)
        prev = window
    assert finals == ["前文对对对", "不不是", "方案方案"]
    assert all(text is not None for text in finals)
    assert "未知" not in "".join(finals)
