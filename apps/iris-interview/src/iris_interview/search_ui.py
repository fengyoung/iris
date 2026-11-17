"""Local search hit locations; the main window decides how to navigate targets."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout


def _leaves(value):
    if isinstance(value, dict):
        return "\n".join(_leaves(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_leaves(v) for v in value)
    return value if isinstance(value, str) else ""


def search_hits(project, query):
    """Return (target, snippet) pairs; target=(kind, stable_id).

    kinds: resume, transcript, note, free_notes, evaluation, snapshot, metadata.
    evaluation means current draft; snapshot preserves a formal historical match.
    """
    needle = query.casefold().strip()
    if not needle:
        return []
    candidates = []
    resume = project.get("resume", {})
    blocks = resume.get("blocks", [])
    for block in blocks:
        candidates.append((("resume", block["id"]), block.get("text", "")))
    # Extracted/profile text can contain matches not assigned a visible block.
    candidates.append((("resume", ""), _leaves({k: v for k, v in resume.items() if k != "blocks"})))
    for row in project.get("transcript", []):
        candidates.append((("transcript", row["id"]), _leaves(row)))
    for index, note in enumerate(project.get("notes", [])):
        candidates.append((("note", note.get("id", str(index))), note.get("text", "")))
    candidates.append((("free_notes", ""), project.get("free_notes", "")))
    candidates.append((("evaluation", ""), _leaves(project.get("evaluation", {}).get("draft", {}))))
    for snapshot in project.get("snapshots", []):
        candidates.append((("snapshot", snapshot["id"]), _leaves(snapshot.get("content", {}))))
    candidates.append(
        (("metadata", ""), _leaves({k: project.get(k, "") for k in ("name", "candidate", "job")}))
    )
    result = []
    for target, text in candidates:
        if not isinstance(text, str):
            continue
        index = text.casefold().find(needle)
        if index >= 0:
            snippet = text[max(0, index - 45) : index + len(needle) + 100].replace("\n", " ")
            result.append((target, snippet))
    return result


class SearchResultsDialog(QDialog):
    """selected_target is populated only when user explicitly opens a result."""

    target_selected = Signal(object)

    def __init__(self, project, query, parent=None):
        super().__init__(parent)
        self.selected_target = None
        self.setWindowTitle("本场搜索结果")
        self.resize(830, 560)
        layout = QVBoxLayout(self)
        heading = QLabel(f"搜索：{query}")
        heading.setTextFormat(Qt.PlainText)
        layout.addWidget(heading)
        self.results = QListWidget()
        self.hits = search_hits(project, query)
        labels = {
            "resume": "简历",
            "transcript": "转写",
            "note": "笔记",
            "free_notes": "自由笔记",
            "evaluation": "评价草稿",
            "snapshot": "历史版本",
            "metadata": "基本信息",
        }
        for target, snippet in self.hits:
            item = QListWidgetItem(f"{labels[target[0]]} · {snippet}")
            item.setData(Qt.UserRole, target)
            item.setToolTip(snippet)
            self.results.addItem(item)
        layout.addWidget(self.results)
        if not self.hits:
            layout.addWidget(QLabel("没有命中；项目列表可能匹配了其他历史材料。"))
        self.open_button = QPushButton("定位所选内容")
        self.open_button.setEnabled(bool(self.hits))
        self.open_button.clicked.connect(self.open_selected)
        self.results.itemDoubleClicked.connect(self.open_selected)
        layout.addWidget(self.open_button)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        if self.hits:
            self.results.setCurrentRow(0)

    def open_selected(self, *_):
        item = self.results.currentItem()
        if item is not None:
            self.selected_target = tuple(item.data(Qt.UserRole))
            self.target_selected.emit(self.selected_target)
            self.accept()
