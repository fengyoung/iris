"""Local search target routing tests using synthetic projects."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from iris_interview.search_ui import SearchResultsDialog, search_hits


@pytest.fixture(scope="module")
def app(qt_application_lifetime):
    return qt_application_lifetime


def test_hits_cover_material_types_and_stable_ids(app):
    project = {
        "resume": {"blocks": [{"id": "b1", "text": "命中简历"}]},
        "transcript": [{"id": "t1", "manual": "命中修正回答"}],
        "notes": [{"id": "n1", "text": "命中笔记"}],
        "free_notes": "命中自由笔记",
        "evaluation": {"draft": {"summary": "命中评价"}},
        "snapshots": [{"id": "s1", "content": {"summary": "命中历史"}}],
    }
    hits = search_hits(project, "命中")
    assert {target for target, _ in hits} == {
        ("resume", "b1"),
        ("transcript", "t1"),
        ("note", "n1"),
        ("free_notes", ""),
        ("evaluation", ""),
        ("snapshot", "s1"),
    }
    dialog = SearchResultsDialog(project, "命中")
    emitted = []
    dialog.target_selected.connect(emitted.append)
    dialog.results.setCurrentRow(1)
    dialog.open_selected()
    assert dialog.selected_target == ("transcript", "t1")
    assert emitted == [("transcript", "t1")]
    dialog.close()


def test_query_literal_casefold_and_no_blank_matches(app):
    project = {"name": "C++ MATCH", "resume": {"blocks": []}}
    assert search_hits(project, "match")[0][0] == ("metadata", "")
    assert search_hits(project, "C++")
    assert search_hits(project, "") == []
    dialog = SearchResultsDialog(project, "missing")
    assert dialog.selected_target is None and not dialog.open_button.isEnabled()
    dialog.close()
