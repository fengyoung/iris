"""Editable evaluation, multiple evidence references, and manual-only job level."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QFormLayout,
    QScrollArea,
)
from .services import DIMENSIONS, RATINGS, VALUES_RATINGS, standard_check


class EvidenceEditor(QWidget):
    """Add/remove ordered references, preview them, and navigate any valid segment."""

    changed = Signal()
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.references = QComboBox()
        self.references.setMinimumContentsLength(14)
        self.references.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.available = QComboBox()
        self.available.setMinimumContentsLength(14)
        self.available.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.available.setToolTip("从本场转写中选择要补充的证据")
        self.add_button = QPushButton("添加引用")
        self.remove_button = QPushButton("移除")
        self.jump_button = QPushButton("跳转")
        layout.addWidget(self.references, 1)
        layout.addWidget(self.jump_button)
        layout.addWidget(self.remove_button)
        layout.addWidget(self.available, 1)
        layout.addWidget(self.add_button)
        self.add_button.clicked.connect(self.add_reference)
        self.remove_button.clicked.connect(self.remove_reference)
        self.jump_button.clicked.connect(self.jump)
        self.references.currentIndexChanged.connect(self.update_actions)
        self.segments = {}
        self.refs = []
        self.update_actions()

    @staticmethod
    def label(segment):
        text = segment.get("manual", segment.get("corrected", segment.get("raw", "")))
        if not isinstance(text, str):
            text = ""
        start = segment.get("start", 0)
        start = start if isinstance(start, (int, float)) else 0
        speaker = segment.get("speaker", "")
        return f"{start:.1f}s {speaker} · {text[:65]}"

    def bind(self, refs, transcript):
        self.segments = {
            s["id"]: s for s in transcript if isinstance(s, dict) and isinstance(s.get("id"), str)
        }
        self.refs = list(dict.fromkeys(ref for ref in refs if isinstance(ref, str)))
        self.available.clear()
        for segment_id, segment in self.segments.items():
            self.available.addItem(self.label(segment), segment_id)
        self.refresh_references()

    def refresh_references(self):
        self.references.clear()
        for ref in self.refs:
            segment = self.segments.get(ref)
            self.references.addItem(self.label(segment) if segment else f"引用已失效 · {ref}", ref)
        self.update_actions()

    def update_actions(self):
        ref = self.references.currentData()
        self.jump_button.setEnabled(ref in self.segments)
        self.remove_button.setEnabled(ref is not None)
        self.add_button.setEnabled(self.available.count() > 0)

    def add_reference(self):
        ref = self.available.currentData()
        if ref is not None and ref not in self.refs:
            self.refs.append(ref)
            self.refresh_references()
            self.references.setCurrentIndex(len(self.refs) - 1)
            self.changed.emit()

    def remove_reference(self):
        ref = self.references.currentData()
        if ref in self.refs:
            self.refs.remove(ref)
            self.refresh_references()
            self.changed.emit()

    def jump(self):
        ref = self.references.currentData()
        if ref in self.segments:
            self.navigate.emit(ref)


class EvaluationPanel(QWidget):
    changed = Signal()
    confirm_requested = Signal()
    evidence_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.loading = False
        layout = QVBoxLayout(self)
        self.summary = QPlainTextEdit()
        self.summary.setPlaceholderText("面试过程总结 / 总体评价")
        self.summary.setMaximumHeight(110)
        layout.addWidget(self.summary)
        self.summary.textChanged.connect(self.commit)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QVBoxLayout(body)
        self.rows = []
        self.evidence_editors = []
        for index, name in enumerate(DIMENSIONS):
            line = QHBoxLayout()
            label = QLabel(name)
            label.setTextFormat(Qt.PlainText)
            line.addWidget(label, 1)
            rating = QComboBox()
            rating.addItems(VALUES_RATINGS if index == 5 else RATINGS)
            rating.setCurrentText("未考察")
            line.addWidget(rating)
            evidence_button = QPushButton("查看证据")
            line.addWidget(evidence_button)
            form.addLayout(line)
            reason = QPlainTextEdit()
            reason.setMaximumHeight(74)
            reason.setPlaceholderText("理由、反向证据或待核实项")
            form.addWidget(reason)
            editor = EvidenceEditor()
            form.addWidget(editor)
            self.evidence_editors.append(editor)
            self.rows.append((rating, reason, evidence_button))
            rating.currentTextChanged.connect(self.commit)
            reason.textChanged.connect(self.commit)
            evidence_button.clicked.connect(lambda checked=False, i=index: self.show_evidence(i))
            editor.changed.connect(self.commit)
            editor.navigate.connect(self.evidence_requested)
        fields = QFormLayout()
        self.match = QComboBox()
        self.match.addItems(["待补充", "很匹配", "无障碍", "不匹配"])
        fields.addRow("专业匹配", self.match)
        self.match.currentTextChanged.connect(self.commit)
        self.skills = {}
        for key, label in [
            ("reason", "专业匹配依据"),
            ("motivation", "求职动机"),
            ("strengths", "优势与短板"),
            ("risks", "风险 / 后续关注"),
            ("ai_ability", "AI 能力"),
        ]:
            editor = QPlainTextEdit()
            editor.setMaximumHeight(90)
            self.skills[key] = editor
            fields.addRow(label, editor)
            editor.textChanged.connect(self.commit)
        self.skills_evidence = EvidenceEditor()
        self.skills_evidence.changed.connect(self.commit)
        self.skills_evidence.navigate.connect(self.evidence_requested)
        fields.addRow("专项技能证据", self.skills_evidence)
        self.follow_up = QPlainTextEdit()
        self.follow_up.setMaximumHeight(90)
        self.follow_up.setPlaceholderText("下一轮需要补充验证的问题")
        fields.addRow("后续验证建议", self.follow_up)
        self.follow_up.textChanged.connect(self.commit)
        self.result = QComboBox()
        self.result.addItems(["待定", "通过", "淘汰"])
        fields.addRow("面试结论", self.result)
        self.result.currentTextChanged.connect(self.commit)
        self.conclusion = QPlainTextEdit()
        self.conclusion.setMaximumHeight(90)
        fields.addRow("结论说明", self.conclusion)
        self.conclusion.textChanged.connect(self.commit)
        self.level = QLineEdit()
        self.level.setPlaceholderText("可留空，仅由你填写，AI 不生成")
        fields.addRow("建议职级（可选）", self.level)
        self.level.textChanged.connect(self.commit)
        form.addLayout(fields)
        self.check = QLabel()
        self.check.setWordWrap(True)
        self.check.setTextFormat(Qt.PlainText)
        form.addWidget(self.check)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        confirm = QPushButton("确认评价 · 保存正式版本")
        confirm.setObjectName("primary")
        confirm.clicked.connect(self.confirm_requested)
        layout.addWidget(confirm)

    @staticmethod
    def _string(value):
        return value if isinstance(value, str) else ""

    def bind(self, project):
        self.loading = True
        self.project = project
        ev = project.get("evaluation", {})
        draft = ev.get("draft", {})
        self.summary.setPlainText(self._string(draft.get("summary", "")))
        rows = {
            r["dimension"]: r for r in draft.get("leadership", []) if isinstance(r, dict) and "dimension" in r
        }
        transcript = project.get("transcript", [])
        management = project.get("job_snapshot", {}).get("management_required", False)
        for index, (name, (rating, reason, _)) in enumerate(zip(DIMENSIONS, self.rows)):
            row = rows.get(name, {})
            default = "不适用" if index == 4 and not management else "未考察"
            rating.setCurrentText(row.get("rating", default))
            reason.setPlainText(self._string(row.get("reason", "")))
            self.evidence_editors[index].bind(row.get("evidence", []), transcript)
        skills = draft.get("skills", {})
        self.match.setCurrentText(skills.get("match", "待补充"))
        for key, editor in self.skills.items():
            editor.setPlainText(self._string(skills.get(key, "")))
        self.skills_evidence.bind(skills.get("evidence", []), transcript)
        self.follow_up.setPlainText(self._string(draft.get("follow_up", "")))
        self.result.setCurrentText(draft.get("result", "待定"))
        self.conclusion.setPlainText(self._string(draft.get("conclusion", "")))
        self.level.setText(self._string(ev.get("suggested_level")))
        self.loading = False
        self.update_check()

    def commit(self):
        if self.loading or self.project is None:
            return
        ev = self.project.setdefault("evaluation", {})
        draft = ev.setdefault("draft", {})
        previous = {r["dimension"]: r for r in draft.get("leadership", [])}
        draft["summary"] = self.summary.toPlainText()
        draft["leadership"] = [
            {
                **previous.get(name, {}),
                "dimension": name,
                "rating": rating.currentText(),
                "reason": reason.toPlainText(),
                "evidence": list(self.evidence_editors[i].refs),
                "manual": True,
            }
            for i, (name, (rating, reason, _)) in enumerate(zip(DIMENSIONS, self.rows))
        ]
        draft["skills"] = {
            **draft.get("skills", {}),
            "match": self.match.currentText(),
            **{k: e.toPlainText() for k, e in self.skills.items()},
            "evidence": list(self.skills_evidence.refs),
        }
        draft["follow_up"] = self.follow_up.toPlainText()
        draft["result"] = self.result.currentText()
        draft["conclusion"] = self.conclusion.toPlainText()
        ev["suggested_level"] = self.level.text() or None
        ev["needs_review"] = True
        self.update_check()
        self.changed.emit()

    def update_check(self):
        if not self.project:
            return
        job = self.project.get("job_snapshot", {})
        rows = self.project.get("evaluation", {}).get("draft", {}).get("leadership", [])
        self.check.setText(standard_check(rows, job.get("tier", ""), job.get("management_required", False)))

    def show_evidence(self, index):
        self.evidence_editors[index].jump()
