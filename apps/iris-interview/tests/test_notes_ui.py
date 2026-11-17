"""Synthetic notes editing and read-only history regression tests."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from iris_interview.notes_ui import NotesPanel, HistoryDialog


@pytest.fixture(scope="module")
def app(qt_application_lifetime):
    return qt_application_lifetime


@pytest.fixture
def project():
    return {
        "free_notes": "原始自由笔记",
        "notes": [{"id": "n1", "time": 12, "kind": "marker", "text": "待核实", "question_id": "q1"}],
        "plan": {"questions": [{"id": "q1", "text": "个人贡献？"}]},
        "transcript": [{"id": "t1", "start": 0, "end": 5}, {"id": "t2", "start": 10, "end": 20}],
        "snapshots": [
            {
                "id": "s1",
                "kind": "plan",
                "source_revision": 2,
                "created_at": "2026-11-16",
                "content": {"questions": ["历史问题"]},
            },
            {
                "id": "s2",
                "kind": "evaluation",
                "source_revision": 5,
                "created_at": "2026-11-17",
                "suggested_level": "人工职级",
                "content": {"result": "通过"},
            },
        ],
    }


def test_free_and_structured_edit_persist(app, project):
    panel = NotesPanel()
    panel.bind(project)
    signals = []
    panel.changed.connect(lambda: signals.append(True))
    assert panel.toPlainText() == "原始自由笔记"
    panel.setPlainText("新自由笔记")
    panel.text.setPlainText("补充具体疑点")
    panel.time.setValue(18.5)
    panel.kind.setCurrentIndex(panel.kind.findData("concern"))
    assert project["free_notes"] == "新自由笔记"
    assert project["notes"][0]["text"] == "补充具体疑点"
    assert project["notes"][0]["time"] == 18.5
    assert project["notes"][0]["kind"] == "concern"
    assert project["notes"][0]["question_id"] == "q1"
    assert signals
    panel.close()


def test_add_remove_marks_and_evidence_navigation(app, project):
    panel = NotesPanel()
    panel.bind(project)
    evidence = []
    panel.evidence_requested.connect(evidence.append)
    panel.jump_to_evidence()
    assert evidence == ["t2"]
    panel.add_note(kind="marker", time=2, text="亮点", question_id="q1")
    assert len(project["notes"]) == 2
    assert panel.text.toPlainText() == "亮点"
    panel.jump_to_evidence()
    assert evidence[-1] == "t1"
    panel.question.setEditText("new-question-id")
    assert project["notes"][-1]["question_id"] == "new-question-id"
    panel.remove_note()
    assert len(project["notes"]) == 1
    panel.remove_note()
    assert not project["notes"] and not panel.text.isEnabled()
    panel.close()


def test_bind_does_not_edit_document_and_refresh_external_mark(app, project):
    before = copy.deepcopy(project)
    panel = NotesPanel()
    panel.bind(project)
    assert project == before
    project["notes"].append({"id": "external", "time": 30, "kind": "marker", "text": "外部标记"})
    panel.refresh()
    assert panel.table.rowCount() == 2
    panel.close()


def test_history_is_read_only_and_preserves_current_project(app, project):
    before = copy.deepcopy(project)
    dialog = HistoryDialog(project)
    assert dialog.content.isReadOnly()
    assert "来源修订 2" in dialog.versions.currentText()
    assert "历史问题" in dialog.content.toPlainText()
    dialog.versions.setCurrentIndex(1)
    assert "人工职级" in dialog.content.toPlainText()
    assert "通过" in dialog.content.toPlainText()
    assert project == before
    dialog.close()
    empty = HistoryDialog({"snapshots": []})
    assert "尚无" in empty.content.toPlainText()
    empty.close()
