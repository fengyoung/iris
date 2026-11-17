"""Readable generated draft review, never expose JSON as the normal workflow."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox, QLabel


def plan_text(value):
    lines = [value.get("summary", ""), "\n开场互相认识"] + value.get("opening", []) + ["\n简历深挖"]
    for i, q in enumerate(value.get("questions", []), 1):
        lines += [f"{i}. {q['text']}", q.get("purpose", "")]
        lines += ["   ↳ " + s for s in q.get("followups", [])]
    lines += ["\n候选人提问"] + value.get("candidate_questions", [])
    return "\n".join(lines)


def evaluation_text(value):
    lines = [value.get("summary", ""), "\n领导力评价"]
    for row in value.get("leadership", []):
        lines += [
            f"{row['dimension']}：{row['rating']}",
            row.get("reason", ""),
            "证据：" + ", ".join(row.get("evidence", [])),
        ]
    skills = value.get("skills", {})
    lines += ["\n专项技能及个人情况"]
    for key, label in [
        ("match", "专业匹配"),
        ("reason", "依据"),
        ("motivation", "求职动机"),
        ("strengths", "优势与短板"),
        ("risks", "风险"),
        ("ai_ability", "AI能力"),
    ]:
        lines.append(label + "：" + skills.get(key, ""))
    lines += [
        "\n面试结论：" + value.get("result", ""),
        value.get("conclusion", ""),
        "\n后续验证：" + value.get("follow_up", ""),
    ]
    return "\n".join(lines)


class DraftReviewDialog(QDialog):
    def __init__(self, label, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(label + " · 审阅后采用")
        self.resize(780, 620)
        layout = QVBoxLayout(self)
        notice = QLabel("这是新生成的草稿。采用后可继续逐项编辑；当前内容在采用前保持不变。")
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.PlainText)
        layout.addWidget(notice)
        editor = QPlainTextEdit(plan_text(value) if "questions" in value else evaluation_text(value))
        editor.setReadOnly(True)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("采用草稿")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
