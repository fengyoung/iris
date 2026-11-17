"""Offscreen evaluation editing tests with synthetic transcript evidence."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from iris_interview.evaluation_ui import EvaluationPanel
from iris_interview.services import DIMENSIONS


@pytest.fixture(scope="module")
def app(qt_application_lifetime):
    return qt_application_lifetime


@pytest.fixture
def project():
    return {
        "job_snapshot": {"management_required": False, "tier": "基层"},
        "transcript": [
            {"id": "t1", "start": 1.2, "speaker": "候选人", "raw": "第一段回答"},
            {"id": "t2", "start": 15, "speaker": "候选人", "raw": "第二段回答"},
        ],
        "evaluation": {
            "draft": {
                "leadership": [
                    {
                        "dimension": DIMENSIONS[0],
                        "rating": "良好",
                        "reason": "具体行为",
                        "evidence": ["t1", "t2"],
                    }
                ],
                "skills": {"match": "无障碍", "evidence": ["t2"]},
                "follow_up": "验证规模",
            },
            "suggested_level": "人工职级",
            "confirmed": [],
            "needs_review": False,
        },
    }


def test_multiple_evidence_selection_and_navigation(app, project):
    panel = EvaluationPanel()
    panel.bind(project)
    signals = []
    panel.evidence_requested.connect(signals.append)
    editor = panel.evidence_editors[0]
    assert editor.references.count() == 2
    assert "第一段回答" in editor.references.itemText(0)
    editor.references.setCurrentIndex(1)
    panel.show_evidence(0)
    assert signals == ["t2"]
    editor.remove_reference()
    assert project["evaluation"]["draft"]["leadership"][0]["evidence"] == ["t1"]
    editor.available.setCurrentIndex(1)
    editor.add_reference()
    assert project["evaluation"]["draft"]["leadership"][0]["evidence"] == ["t1", "t2"]
    editor.add_reference()
    assert editor.refs == ["t1", "t2"]
    assert project["evaluation"]["needs_review"]
    panel.close()


def test_skills_evidence_followup_and_manual_level(app, project):
    panel = EvaluationPanel()
    panel.bind(project)
    signals = []
    panel.evidence_requested.connect(signals.append)
    panel.skills_evidence.jump()
    assert signals == ["t2"]
    panel.skills_evidence.available.setCurrentIndex(0)
    panel.skills_evidence.add_reference()
    assert project["evaluation"]["draft"]["skills"]["evidence"] == ["t2", "t1"]
    panel.follow_up.setPlainText("下一轮验证容量与实验归因")
    assert project["evaluation"]["draft"]["follow_up"] == "下一轮验证容量与实验归因"
    assert project["evaluation"]["suggested_level"] == "人工职级"
    panel.level.clear()
    assert project["evaluation"]["suggested_level"] is None
    panel.close()


def test_nonmanagement_default_does_not_overwrite_existing_rating(app, project):
    panel = EvaluationPanel()
    panel.bind(project)
    assert panel.rows[4][0].currentText() == "不适用"
    assert project["evaluation"]["needs_review"] is False
    panel.summary.setPlainText("人工摘要")
    assert project["evaluation"]["draft"]["leadership"][4]["rating"] == "不适用"
    project["evaluation"]["draft"]["leadership"][4]["rating"] = "合格"
    panel.bind(project)
    assert panel.rows[4][0].currentText() == "合格"
    management = {
        "job_snapshot": {"management_required": True},
        "transcript": [],
        "evaluation": {"draft": {}, "suggested_level": None},
    }
    panel.bind(management)
    assert panel.rows[4][0].currentText() == "未考察"
    panel.close()


def test_missing_reference_is_visible_removable_and_cannot_jump(app, project):
    project["evaluation"]["draft"]["skills"]["evidence"] = ["missing"]
    panel = EvaluationPanel()
    panel.bind(project)
    editor = panel.skills_evidence
    assert "已失效" in editor.references.currentText()
    assert not editor.jump_button.isEnabled()
    editor.remove_reference()
    assert project["evaluation"]["draft"]["skills"]["evidence"] == []
    panel.close()
