"""Single scroll surface aligning original resume blocks and editable questions."""

from __future__ import annotations
import uuid
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QComboBox,
    QInputDialog,
    QDialog,
    QSlider,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)
from PySide6.QtCore import QTimer
from .dialogs import TextDialog


class AlignedResume(QWidget):
    changed = Signal()
    selected = Signal(str)
    note_requested = Signal(str)
    suggest_requested = Signal(str)
    rewrite_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("简历原文 · 仅重排，随时对照原件"), 1)
        header.addWidget(QLabel("定制问题 · 点击展开追问"), 1)
        self.balance = QSlider(Qt.Horizontal)
        self.balance.setRange(35, 65)
        self.balance.setValue(50)
        self.balance.setMaximumWidth(130)
        self.balance.setToolTip("调整简历 / 问题宽度")
        self.balance.sliderReleased.connect(self.rebuild)
        header.addWidget(self.balance)
        layout.addLayout(header)
        self.highlight_block = ""
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)
        self.project = {}
        self.current_question = ""
        self.expanded = set()
        self.rows = {}

    def bind(self, project):
        self.project = project
        self.rebuild()

    def rebuild(self):
        position = self.scroll.verticalScrollBar().value()
        body = QWidget()
        content = QVBoxLayout(body)
        content.setSpacing(0)
        self.rows = {}
        blocks = self.project.get("resume", {}).get("blocks", [])
        questions = self.project.get("plan", {}).get("questions", [])
        for block in blocks:
            row = QFrame()
            row.setObjectName("activeRow" if block["id"] == self.highlight_block else "resumeRow")
            columns = QHBoxLayout(row)
            columns.setContentsMargins(16, 14, 16, 14)
            columns.setSpacing(28)
            left = QVBoxLayout()
            text = QLabel(block["text"].rstrip("\r\n"))
            text.setTextFormat(Qt.PlainText)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.setStyleSheet("font-size: 16px;")
            left.addWidget(text)
            edit = QPushButton("校对本段")
            edit.clicked.connect(lambda checked=False, b=block: self.edit_block(b))
            left_controls = QHBoxLayout()
            left_controls.addWidget(edit)
            locate = QPushButton("定位对应问题")
            locate.clicked.connect(lambda checked=False, bid=block["id"]: self.highlight(bid))
            left_controls.addWidget(locate)
            left_controls.addStretch()
            left.addLayout(left_controls)
            left.addStretch()
            columns.addLayout(left, self.balance.value())
            right = QVBoxLayout()
            associated = [q for q in questions if q.get("block_id") == block["id"]]
            for index, question in enumerate(associated):
                if index >= 2 and block["id"] not in self.expanded:
                    more = QPushButton(f"更多问题（{len(associated) - 2}）")
                    more.clicked.connect(lambda checked=False, bid=block["id"]: self.toggle(bid))
                    right.addWidget(more)
                    break
                right.addWidget(self.question_widget(question))
            add = QPushButton("＋ 添加本段问题")
            add.clicked.connect(lambda checked=False, bid=block["id"]: self.add_question(bid))
            right.addWidget(add, alignment=Qt.AlignLeft)
            if len(associated) > 2 and block["id"] in self.expanded:
                less = QPushButton("收起备选问题")
                less.clicked.connect(lambda checked=False, bid=block["id"]: self.toggle(bid))
                right.addWidget(less)
            right.addStretch()
            columns.addLayout(right, 100 - self.balance.value())
            content.addWidget(row)
            self.rows[block["id"]] = row
        if not blocks:
            content.addWidget(QLabel("导入简历或粘贴文字，校对后生成与原文对齐的问题。"))
        content.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(body)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(position))

    def question_widget(self, q):
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 12)
        title = QLabel(q["text"])
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        box.addWidget(title)
        if q.get("purpose"):
            purpose = QLabel(q["purpose"])
            purpose.setTextFormat(Qt.PlainText)
            purpose.setWordWrap(True)
            purpose.setObjectName("muted")
            box.addWidget(purpose)
        controls = QHBoxLayout()
        expand = QPushButton("收起追问" if q["id"] in self.expanded else "展开追问")
        expand.clicked.connect(lambda: self.toggle(q["id"]))
        controls.addWidget(expand)
        status = QComboBox()
        for label, value in [("未问", "unasked"), ("已问", "asked"), ("待追问", "follow_up")]:
            status.addItem(label, value)
        status.setCurrentIndex(max(0, status.findData(q.get("status", "unasked"))))
        status.currentIndexChanged.connect(lambda: self.set_status(q, status.currentData()))
        controls.addWidget(status)
        edit = QPushButton("编辑")
        edit.clicked.connect(lambda: self.edit_question(q))
        controls.addWidget(edit)
        box.addLayout(controls)
        priority = QComboBox()
        priority.addItem("重点问题", "key")
        priority.addItem("备选问题", "optional")
        priority.setCurrentIndex(1 if q.get("priority") == "optional" else 0)
        priority.currentIndexChanged.connect(lambda: self.set_priority(q, priority.currentData()))
        box.addWidget(priority)
        if q["id"] in self.expanded:
            details = QLabel(
                "\n".join("• " + text for text in q.get("followups", [])) or "尚无追问，可编辑添加"
            )
            details.setTextFormat(Qt.PlainText)
            details.setWordWrap(True)
            box.addWidget(details)
            for block in self.project["resume"]["blocks"]:
                if block["id"] in q.get("related_block_ids", []) and block["id"] != q["block_id"]:
                    link = QPushButton("关联经历：" + block["text"][:28].replace("\n", " "))
                    link.clicked.connect(lambda checked=False, bid=block["id"]: self.jump_block(bid))
                    box.addWidget(link)
            actions = QHBoxLayout()
            for label, fn in [
                ("记笔记", lambda: self.note_requested.emit(q["id"])),
                ("AI 追问建议", lambda: self.suggest_requested.emit(q["id"])),
                ("改写此题", lambda: self.rewrite_requested.emit(q["id"])),
                ("关联原文", lambda: self.rebind_question(q)),
                ("综合关联", lambda: self.related_blocks(q)),
                ("评估维度", lambda: self.edit_dimensions(q)),
                ("上移", lambda: self.move_question(q, -1)),
                ("下移", lambda: self.move_question(q, 1)),
                ("删除", lambda: self.remove_question(q)),
            ]:
                button = QPushButton(label)
                button.clicked.connect(fn)
                actions.addWidget(button)
            box.addLayout(actions)
        return widget

    def toggle(self, key):
        self.current_question = key
        question = next(
            (q for q in self.project.get("plan", {}).get("questions", []) if q["id"] == key), None
        )
        if question:
            self.highlight_block = question["block_id"]
        self.expanded.symmetric_difference_update({key})
        self.selected.emit(key)
        self.rebuild()

    def set_status(self, q, value):
        q["status"] = value
        self.changed.emit()

    def edit_block(self, block):
        dialog = TextDialog("校对简历原文（不进行 AI 改写）", block["text"], self)
        if dialog.exec() == QDialog.Accepted:
            block["text"] = dialog.value()
            self.project["resume"]["text"] = "".join(b["text"] for b in self.project["resume"]["blocks"])
            self.project["evaluation"]["needs_review"] = True
            self.changed.emit()
            self.rebuild()

    def add_question(self, block_id):
        text, ok = QInputDialog.getMultiLineText(self, "添加问题", "主问题")
        if ok and text.strip():
            self.project.setdefault("plan", {}).setdefault("questions", []).append(
                {
                    "id": uuid.uuid4().hex,
                    "block_id": block_id,
                    "text": text,
                    "followups": [],
                    "dimensions": [],
                    "purpose": "",
                    "priority": "key",
                    "status": "unasked",
                }
            )
            self.changed.emit()
            self.rebuild()

    def edit_question(self, question):
        dialog = TextDialog(
            "编辑问题与追问（第一行为主问题，其余行为追问）",
            "\n".join([question["text"], *question.get("followups", [])]),
            self,
        )
        if dialog.exec() == QDialog.Accepted:
            lines = dialog.value().splitlines()
            if lines and lines[0].strip():
                question["text"], question["followups"] = lines[0], [s for s in lines[1:] if s.strip()]
                question["manual"] = True
                self.changed.emit()
                self.rebuild()

    def related_blocks(self, q):
        dialog = QDialog(self)
        dialog.setWindowTitle("综合问题关联的经历")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        layout.addWidget(items)
        blocks = self.project["resume"]["blocks"]
        selected = set(q.get("related_block_ids", [])) | {q["block_id"]}
        for block in blocks:
            item = QListWidgetItem(block["text"][:100].replace("\n", " "))
            item.setData(Qt.UserRole, block["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if block["id"] in selected else Qt.Unchecked)
            items.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            q["related_block_ids"] = [
                items.item(i).data(Qt.UserRole)
                for i in range(items.count())
                if items.item(i).checkState() == Qt.Checked
            ]
            self.changed.emit()
            self.rebuild()

    def edit_dimensions(self, q):
        from .services import DIMENSIONS

        selected, ok = QInputDialog.getItem(
            self,
            "关联评估维度",
            "选择要添加或移除的维度",
            DIMENSIONS + ["专业技能", "AI能力"],
            editable=False,
        )
        if ok:
            dimensions = q.setdefault("dimensions", [])
            if selected in dimensions:
                dimensions.remove(selected)
            else:
                dimensions.append(selected)
            self.changed.emit()

    def set_priority(self, q, value):
        q["priority"] = value
        self.changed.emit()

    def jump_block(self, block_id):
        self.highlight(block_id)
        QTimer.singleShot(
            10,
            lambda: self.scroll.ensureWidgetVisible(self.rows[block_id]) if block_id in self.rows else None,
        )

    def highlight(self, block_id):
        self.highlight_block = block_id
        self.rebuild()

    def rebind_question(self, q):
        blocks = self.project.get("resume", {}).get("blocks", [])
        labels = [f"{i + 1}. {b['text'][:60].replace(chr(10), ' ')}" for i, b in enumerate(blocks)]
        choice, ok = QInputDialog.getItem(self, "关联原文", "选择主要关联段落", labels, editable=False)
        if ok:
            q["block_id"] = blocks[labels.index(choice)]["id"]
            self.highlight_block = q["block_id"]
            self.changed.emit()
            self.rebuild()

    def move_question(self, q, direction):
        questions = self.project["plan"]["questions"]
        same = [item for item in questions if item["block_id"] == q["block_id"]]
        index = same.index(q)
        target = index + direction
        if 0 <= target < len(same):
            a, b = questions.index(q), questions.index(same[target])
            questions[a], questions[b] = questions[b], questions[a]
            self.changed.emit()
            self.rebuild()

    def remove_question(self, question):
        self.project["plan"]["questions"].remove(question)
        self.changed.emit()
        self.rebuild()
