"""知遇 application shell and project workflows."""

from __future__ import annotations
import copy
import json
from pathlib import Path
import time
import uuid

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QStackedWidget,
    QTabWidget,
    QPlainTextEdit,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QDialog,
    QCheckBox,
    QSpinBox,
)

from .aligned_resume import AlignedResume
from .catalog import JobLibrary, leadership_standard
from .dialogs import ProjectDialog, SettingsDialog, TextDialog
from .evaluation_ui import EvaluationPanel
from .notes_ui import NotesPanel, HistoryDialog
from .export import export_report
from .preferences import Preferences, app_root, get_api_key, set_api_key
from .resume import extract_resume, make_blocks
from .services import CloudService, validate_plan, validate_evaluation
from .storage import ProjectStore
from .theme import apply_theme
from .workers import Job

STATUS = {
    "preparing": "准备中",
    "ready": "待面试",
    "interviewing": "面试中",
    "review": "待评价",
    "completed": "已完成",
}


class MainWindow(QMainWindow):
    audio_event = Signal(object)

    def __init__(self, root=None):
        super().__init__()
        self.root = Path(root) if root else app_root()
        self.store = ProjectStore(self.root)
        self.preferences = Preferences(self.root)
        self.settings = self.preferences.load()
        self.catalog = JobLibrary(self.root)
        self.project = None
        self.runtime = None
        self.tasks = []
        self.started_at = None
        self.loading = False
        self.pool = QThreadPool(self)
        self.correction_pool = QThreadPool(self)
        self.correction_pool.setMaxThreadCount(1)
        self.setWindowTitle("知遇 · Iris-interview")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/icon-inverse.png")))
        self.resize(1250, 850)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self.build_home())
        self.stack.addWidget(self.build_workspace())
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.tick)
        self.clock.start(1000)
        self.audio_event.connect(self.on_audio)
        apply_theme(QApplication.instance(), self.settings["theme"])
        self.refresh()

    def button(self, label, action, primary=False):
        button = QPushButton(label)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(action)
        return button

    def build_home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QHBoxLayout()
        title = QLabel("知遇")
        title.setObjectName("heading")
        heading.addWidget(title)
        heading.addWidget(QLabel("每一场交流，都有据可循"))
        heading.addStretch()
        heading.addWidget(self.button("设置", self.open_settings))
        layout.addLayout(heading)
        tools = QHBoxLayout()
        tools.addWidget(self.button("新建面试", self.new_project, True))
        tools.addWidget(self.button("导入 Project", self.import_project))
        tools.addWidget(self.button("岗位库", self.manage_jobs))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索候选人、岗位、简历、转写和评价")
        self.search.textChanged.connect(self.refresh)
        tools.addWidget(self.search, 1)
        self.home_filter = QComboBox()
        self.home_filter.addItems(["当前项目", "已归档", "回收站", "全部非删除"])
        self.home_filter.currentIndexChanged.connect(self.refresh)
        tools.addWidget(self.home_filter)
        layout.addLayout(tools)
        filters = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("所有状态", None)
        for value, label in STATUS.items():
            self.status_filter.addItem(label, value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_filter)
        self.result_filter = QComboBox()
        self.result_filter.addItems(["所有结论", "通过", "待定", "淘汰"])
        self.result_filter.currentTextChanged.connect(self.refresh)
        filters.addWidget(self.result_filter)
        self.job_filter = QLineEdit()
        self.job_filter.setPlaceholderText("岗位筛选")
        self.round_filter = QLineEdit()
        self.round_filter.setPlaceholderText("轮次筛选")
        self.date_from = QLineEdit()
        self.date_from.setPlaceholderText("起始日期 YYYY-MM-DD")
        self.date_to = QLineEdit()
        self.date_to.setPlaceholderText("结束日期 YYYY-MM-DD")
        for field in [self.job_filter, self.round_filter, self.date_from, self.date_to]:
            field.textChanged.connect(self.refresh)
            filters.addWidget(field)
        layout.addLayout(filters)
        self.projects = QTableWidget(0, 6)
        self.projects.setHorizontalHeaderLabels(["面试", "候选人", "岗位", "状态", "结论", "更新时间"])
        self.projects.setSelectionBehavior(QTableWidget.SelectRows)
        self.projects.setEditTriggers(QTableWidget.NoEditTriggers)
        self.projects.cellDoubleClicked.connect(lambda row, col: self.open_selected())
        self.projects.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.projects)
        actions = QHBoxLayout()
        for label, fn in [
            ("打开", self.open_selected),
            ("归档 / 取消归档", self.archive_selected),
            ("删除 / 恢复", self.trash_selected),
            ("永久删除", self.delete_selected),
        ]:
            actions.addWidget(self.button(label, fn))
        actions.addStretch()
        layout.addLayout(actions)
        self.home_status = QLabel("本地保存 · 不自动录音")
        layout.addWidget(self.home_status)
        return page

    def build_workspace(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        top.addWidget(self.button("‹ 项目", self.go_home))
        self.title = QLabel()
        self.title.setObjectName("heading")
        top.addWidget(self.title, 1)
        self.elapsed = QLabel("00:00")
        top.addWidget(self.elapsed)
        top.addWidget(self.button("资料", self.edit_metadata))
        top.addWidget(self.button("主题", self.toggle_theme))
        top.addWidget(self.button("设置", self.open_settings))
        layout.addLayout(top)
        tools = QHBoxLayout()
        for label, fn in [
            ("导入简历", self.import_resume),
            ("粘贴 / 校对全文", self.paste_resume),
            ("查看原件", self.open_original),
            ("岗位与标准", self.choose_job),
            ("生成计划", self.generate_plan),
            ("确认计划", self.confirm_plan),
        ]:
            tools.addWidget(self.button(label, fn))
        layout.addLayout(tools)
        self.suggestion = QLabel("")
        self.suggestion.setWordWrap(True)
        self.suggestion.setTextFormat(Qt.PlainText)
        self.suggestion.hide()
        layout.addWidget(self.suggestion)
        self.coverage_label = QLabel("")
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setObjectName("muted")
        layout.addWidget(self.coverage_label)
        self.tabs = QTabWidget()
        self.preparation = QPlainTextEdit()
        self.preparation.setReadOnly(True)
        self.tabs.addTab(self.preparation, "准备概览")
        self.aligned = AlignedResume()
        self.aligned.changed.connect(self.schedule_save)
        self.aligned.note_requested.connect(self.add_note)
        self.aligned.suggest_requested.connect(self.suggest)
        self.aligned.rewrite_requested.connect(self.rewrite_question)
        self.tabs.addTab(self.aligned, "简历深挖")
        self.transcripts = QTableWidget(0, 4)
        self.transcripts.setHorizontalHeaderLabels(["时间", "说话人", "文本", "状态"])
        self.transcripts.setEditTriggers(QTableWidget.NoEditTriggers)
        self.transcripts.horizontalHeader().setStretchLastSection(True)
        self.transcripts.cellDoubleClicked.connect(self.edit_transcript)
        self.tabs.addTab(self.transcripts, "过程转写")
        self.notes = NotesPanel()
        self.notes.changed.connect(self.schedule_save)
        self.notes.evidence_requested.connect(self.locate_evidence)
        self.tabs.addTab(self.notes, "笔记")
        self.evaluation = EvaluationPanel()
        self.evaluation.changed.connect(self.schedule_save)
        self.evaluation.confirm_requested.connect(self.confirm_evaluation)
        self.evaluation.evidence_requested.connect(self.locate_evidence)
        self.tabs.addTab(self.evaluation, "面后评价")
        layout.addWidget(self.tabs)
        controls = QHBoxLayout()
        self.stage = QComboBox()
        self.stage.addItems(["开场互相认识", "简历深挖", "候选人提问"])
        self.stage.currentIndexChanged.connect(self.stage_changed)
        controls.addWidget(self.stage)
        self.duration = QSpinBox()
        self.duration.setRange(10, 240)
        self.duration.setValue(60)
        self.duration.setSuffix(" 分钟")
        controls.addWidget(self.duration)
        self.record = QCheckBox("保存录音")
        controls.addWidget(self.record)
        controls.addWidget(self.button("收音检查 / 开始", self.start_capture))
        self.pause_button = self.button("暂停", self.pause_capture)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.button("结束", self.end_capture))
        controls.addWidget(self.button("标记回答", self.mark_answer))
        layout.addLayout(controls)
        self.audio_levels = {"local": QLabel("我：未采集"), "remote": QLabel("远端：未采集")}
        self.partial_label = QLabel("")
        self.partial_label.setTextFormat(Qt.PlainText)
        self.partial_label.setWordWrap(True)
        self.partial_label.setMaximumHeight(56)
        meter_row = QHBoxLayout()
        for label in self.audio_levels.values():
            meter_row.addWidget(label)
        meter_row.addWidget(self.partial_label, 1)
        layout.addLayout(meter_row)
        bottom = QHBoxLayout()
        self.save_label = QLabel("已保存")
        bottom.addWidget(self.save_label, 1)
        bottom.addWidget(self.button("恢复录音", self.recover_recordings))
        bottom.addWidget(self.button("重试校正", self.retry_corrections))
        bottom.addWidget(self.button("回听片段", self.play_selected))
        bottom.addWidget(self.button("历史版本", self.show_history))
        bottom.addWidget(self.button("生成评价", self.generate_evaluation))
        bottom.addWidget(self.button("导出评价", self.export_evaluation))
        bottom.addWidget(self.button("完整备份", self.export_project))
        layout.addLayout(bottom)
        return page

    def error(self, message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "知遇", message)

    def run_task(self, label, function, on_result):
        job = Job(function)
        self.tasks.append(job)
        self.statusBar().showMessage(label + "…")

        def deliver(value):
            try:
                on_result(value)
            except Exception as exc:
                self.error(str(exc) or type(exc).__name__)

        job.signals.result.connect(deliver)
        job.signals.error.connect(self.error)

        def done():
            if job in self.tasks:
                self.tasks.remove(job)
            self.statusBar().showMessage("任务完成；请检查结果", 5000)

        job.signals.finished.connect(done)
        self.pool.start(job)

    def refresh(self):
        index = self.home_filter.currentIndex()
        self.listed = self.store.list_projects(
            query=self.search.text(),
            archived={0: False, 1: True}.get(index),
            trashed=index == 2,
            status=self.status_filter.currentData(),
            job=self.job_filter.text() or None,
            round=self.round_filter.text() or None,
            date_from=self.date_from.text() or None,
            date_to=self.date_to.text() or None,
        )
        if self.result_filter.currentIndex():
            selected_result = self.result_filter.currentText()
            self.listed = [
                p
                for p in self.listed
                if next(
                    (
                        s["content"].get("result")
                        for s in reversed(p.get("snapshots", []))
                        if s.get("kind") == "evaluation"
                    ),
                    None,
                )
                == selected_result
            ]
        self.projects.setRowCount(len(self.listed))
        for row, p in enumerate(self.listed):
            confirmed = [s for s in p.get("snapshots", []) if s.get("kind") == "evaluation"]
            result = confirmed[-1]["content"].get("result", "尚未确认") if confirmed else "尚未确认"
            for col, text in enumerate(
                [
                    p["name"],
                    p["candidate"],
                    p["job"],
                    STATUS.get(p["status"], p["status"]),
                    result,
                    p["updated_at"][:19],
                ]
            ):
                self.projects.setItem(row, col, QTableWidgetItem(str(text)))
        self.projects.resizeColumnsToContents()
        for row, p in enumerate(self.listed):
            if p.get("search_snippet"):
                self.projects.item(row, 0).setToolTip(p["search_snippet"])
        self.home_status.setText(f"{len(self.listed)} 场面试 · 本地保存 · 双击打开")

    def selected_project(self):
        row = self.projects.currentRow()
        return self.listed[row] if 0 <= row < len(self.listed) else None

    def new_project(self):
        dialog = ProjectDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.open_project(self.store.create_project(**dialog.values()))

    def open_selected(self):
        project = self.selected_project()
        if project and not project.get("trashed_at"):
            self.open_project(self.store.get_project(project["id"]))
            if self.search.text():
                from .search_ui import SearchResultsDialog

                dialog = SearchResultsDialog(self.project, self.search.text(), self)
                if dialog.exec() == QDialog.Accepted and dialog.selected_target:
                    self.locate_search(dialog.selected_target)

    def locate_search(self, target):
        kind, identifier = target
        if kind == "resume":
            self.tabs.setCurrentIndex(1)
            self.aligned.highlight(identifier)

            def scroll():
                row = self.aligned.rows.get(identifier)
                if row:
                    self.aligned.scroll.ensureWidgetVisible(row)

            QTimer.singleShot(10, scroll)
        elif kind == "transcript":
            self.locate_evidence(identifier)
        elif kind in {"note", "free_notes"}:
            self.tabs.setCurrentIndex(3)
        elif kind == "evaluation":
            self.tabs.setCurrentIndex(4)
        elif kind == "snapshot":
            self.show_history()
        else:
            self.tabs.setCurrentIndex(0)

    def open_project(self, project):
        self.loading = True
        self.project = project
        if not self.runtime:
            self.started_at = None
        self.stage.blockSignals(True)
        self.stage.setCurrentIndex(project.get("stage", 0))
        self.stage.blockSignals(False)
        if project["status"] == "interviewing":
            project["status"] = "review"
            project.setdefault("interruptions", []).append({"reason": "应用中断后恢复；未自动收音"})
        self.title.setText(project["name"] or project["candidate"] or "未命名面试")
        self.aligned.bind(project)
        self.evaluation.bind(project)
        self.notes.bind(project)
        self.duration.setValue(project.get("duration", 60))
        self.record.setChecked(False)
        self.refresh_transcripts()
        self.update_overview()
        self.loading = False
        self.stack.setCurrentIndex(1)

    def update_overview(self):
        if not self.project:
            return
        plan = self.project.get("plan", {})
        job = self.project.get("job_snapshot", {})
        lines = [
            f"岗位：{job.get('name', self.project.get('job', ''))}",
            f"简历校对：{'已确认' if self.project.get('resume', {}).get('verified') else '待校对'}",
            "\n" + plan.get("summary", "请导入简历、选择岗位，并生成计划。"),
            "\n开场",
        ]
        lines += plan.get("opening", []) + ["\n候选人提问观察"] + plan.get("candidate_questions", [])
        lines += ["\n提取提示"] + self.project.get("resume", {}).get("warnings", [])
        self.preparation.setPlainText("\n".join(lines))

    def schedule_save(self):
        if self.loading or not self.project:
            return
        self.save_label.setText("正在保存…")
        self.save_timer.start(350)
        if self.settings.get("coverage_reminders"):
            from .services import DIMENSIONS

            covered = {
                d
                for q in self.project.get("plan", {}).get("questions", [])
                if q.get("status") == "asked"
                for d in q.get("dimensions", [])
            }
            remaining = [d for d in DIMENSIONS if d not in covered]
            self.coverage_label.setText("计划覆盖提示（非能力判断）：尚未标记考察 " + "、".join(remaining))
        else:
            self.coverage_label.clear()

    def save(self):
        if not self.project:
            return True
        self.save_timer.stop()
        self.project["duration"] = self.duration.value()
        try:
            saved = self.store.save_project(self.project)
            for key in ("revision", "updated_at", "created_at"):
                self.project[key] = saved[key]
            self.project["evaluation"]["needs_review"] = saved["evaluation"]["needs_review"]
            self.save_label.setText(
                "已自动保存"
                + (" · 评价有待确认修改" if self.project.get("evaluation", {}).get("needs_review") else "")
            )
            return True
        except Exception as exc:
            self.save_label.setText("保存失败：" + str(exc))
            self.error(str(exc))
            return False

    def go_home(self):
        if self.runtime:
            self.error("请先结束本场采集，再切换项目")
            return
        if self.tasks:
            self.error("请等待当前任务结束后再切换项目")
            return
        if not self.save():
            return
        self.refresh()
        self.stack.setCurrentIndex(0)

    def notes_changed(self):
        if not self.loading and self.project:
            self.project["free_notes"] = self.notes.toPlainText()
            self.schedule_save()

    def add_note(self, question_id=""):
        text, ok = QInputDialog.getMultiLineText(self, "面试笔记", "先标记也可以稍后补充")
        if ok:
            self.notes.add_note(text=text, time=self.current_time(), question_id=question_id)

    def mark_answer(self):
        self.notes.add_note(
            kind="marker",
            text="待补充的回答标记",
            time=max(0, self.current_time() - 15),
            question_id=self.aligned.current_question,
        )
        self.statusBar().showMessage("已标记最近 15 秒上下文，可在笔记中补充", 4000)

    def recover_recordings(self):
        if self.runtime:
            self.error("请先结束当前采集")
            return
        if not self.save():
            return
        snapshot = copy.deepcopy(self.project)

        def recover():
            doc = snapshot
            for session in snapshot.get("recording_sessions", []):
                if session["state"] != "pending":
                    continue
                directory = self.store.recording_directory(doc["id"], session["id"])
                method = (
                    self.store.finalize_recording_session
                    if directory.exists() and any(directory.glob("*.wav"))
                    else self.store.cancel_empty_recording_session
                )
                doc = method(doc["id"], session["id"], expected_revision=doc["revision"])
            return doc

        def ready(doc):
            from .capture_ui import _merge_managed_project

            _merge_managed_project(self.project, doc)
            self.statusBar().showMessage("录音会话恢复检查完成")

        self.run_task("恢复录音会话", recover, ready)

    def play_selected(self):
        row = self.transcripts.currentRow()
        if row < 0:
            self.tabs.setCurrentIndex(2)
            self.error("请先选择一条转写片段，再点击回听")
            return
        from .playback import PlaybackPanel

        PlaybackPanel(self.store, self.project, self.project["transcript"][row], self).exec()

    def show_history(self):
        HistoryDialog(self.project, self).exec()

    def current_time(self):
        return (
            time.monotonic() - self.started_at
            if self.started_at
            else (self.project or {}).get("capture_elapsed_seconds", 0)
        )

    def tick(self):
        seconds = int(self.current_time())
        self.elapsed.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
        if self.settings.get("time_reminders") and seconds == self.duration.value() * 60:
            self.statusBar().showMessage("已达到计划面试时长")

    def stage_changed(self, index):
        if not self.project:
            return
        self.project["stage"] = index
        self.tabs.setCurrentIndex(1 if index == 1 else 0)
        self.schedule_save()

    def edit_metadata(self):
        fields = {
            key: self.project.get(key, "")
            for key in ["name", "candidate", "job", "round", "interview_at", "focus"]
        }
        dialog = ProjectDialog(self)
        for key, field in dialog.fields.items():
            field.setText(fields.get(key, ""))
        if dialog.exec() == QDialog.Accepted:
            self.project.update(dialog.values())
            if self.save():
                self.title.setText(self.project["name"] or "未命名面试")

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            try:
                if dialog.key.text():
                    set_api_key(dialog.key.text())
                self.settings = self.preferences.update(**dialog.values())
                apply_theme(QApplication.instance(), self.settings["theme"])
            except Exception as exc:
                self.error(str(exc))

    def toggle_theme(self):
        self.settings = self.preferences.update(
            theme="dark" if self.settings["theme"] == "light" else "light"
        )
        apply_theme(QApplication.instance(), self.settings["theme"])

    def cloud(self):
        return CloudService(self.settings, get_api_key())

    def import_resume(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入简历", "", "简历 (*.pdf *.docx *.txt *.md *.png *.jpg *.jpeg)"
        )
        if not path:
            return
        if not self.save():
            return
        pid = self.project["id"]
        original_resume = copy.deepcopy(self.project.get("resume", {}))

        def load():
            if Path(path).suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
                from .ocr import extract_with_ocr

                return extract_with_ocr(path)
            return extract_resume(path)

        def ready(resume):
            if not self.project or self.project["id"] != pid:
                return
            if not self.save():
                return
            if (
                self.project.get("resume") != original_resume
                and QMessageBox.question(self, "简历已修改", "提取期间简历有修改，仍替换为新文件并清空问题？")
                != QMessageBox.Yes
            ):
                return
            saved = self.store.add_attachment(
                pid, path, kind="resume", expected_revision=self.project["revision"]
            )
            for key in ("attachments", "revision", "updated_at"):
                self.project[key] = saved[key]
            self.project["resume"] = resume
            self.project["plan"] = {"questions": []}
            if not self.save():
                return
            self.open_project(self.project)
            self.tabs.setCurrentIndex(1)

        self.run_task("提取简历", load, ready)

    def paste_resume(self):
        from .material_ui import ResumeReviewDialog

        attachments = [a for a in self.project.get("attachments", []) if a.get("kind") == "resume"]
        path = self.store.attachment_path(self.project["id"], attachments[-1]["id"]) if attachments else None
        dialog = ResumeReviewDialog(path, self.project.get("resume", {}).get("text", ""), self)
        if dialog.exec() == QDialog.Accepted:
            if (
                self.project.get("plan", {}).get("questions")
                and QMessageBox.question(self, "重新分段", "全文校对会重新分段，并清空旧问题关联。继续？")
                != QMessageBox.Yes
            ):
                return
            text = dialog.text()
            self.project["resume"].update(text=text, blocks=make_blocks(text), verified=True)
            self.project["plan"] = {"questions": []}
            self.save()
            self.aligned.bind(self.project)
            self.update_overview()

    def open_original(self):
        attachments = [a for a in self.project.get("attachments", []) if a.get("kind") == "resume"]
        if attachments:
            path = self.store.attachment_path(self.project["id"], attachments[-1]["id"])
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def manage_jobs(self):
        options = ["新建岗位", "导入岗位库文件"] + [j["name"] for j in self.catalog.list()]
        choice, ok = QInputDialog.getItem(self, "岗位库", "选择操作", options, editable=False)
        if not ok:
            return
        if choice == "导入岗位库文件":
            path, _ = QFileDialog.getOpenFileName(
                self, "导入 iris-hire 或岗位 JSON", "", "岗位 (*.md *.json *.txt)"
            )
            if path:
                try:
                    if Path(path).suffix.lower() == ".json":
                        self.catalog.import_file(Path(path))
                        self.statusBar().showMessage("岗位已导入，请审阅后采用")
                    else:
                        text = Path(path).read_text(encoding="utf-8-sig")

                        def accept_jobs(jobs):
                            from .job_ui import CatalogDraftDialog

                            dialog = CatalogDraftDialog(jobs, self)
                            if dialog.exec() == QDialog.Accepted:
                                for job in dialog.value():
                                    self.catalog.save(job)

                        self.run_task(
                            "从岗位原文提取多岗位草稿", lambda: self.cloud().split_catalog(text), accept_jobs
                        )
                except Exception as exc:
                    self.error(str(exc))
            return
        job = next(
            (j for j in self.catalog.list() if j["name"] == choice),
            {
                "name": "新岗位",
                "jd": "",
                "tier": "基层",
                "management_required": False,
                "dimensions": [],
                "reviewed": False,
            },
        )
        from .job_ui import JobDialog

        dialog = JobDialog(job, self)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.catalog.save(dialog.value())
            except Exception as exc:
                self.error(str(exc))

    def choose_job(self):
        jobs = self.catalog.list()
        if not jobs:
            self.manage_jobs()
            jobs = self.catalog.list()
        if not jobs:
            return
        names = [j["name"] for j in jobs] + ["尚未确定岗位 · AI 推荐", "直接填写本场 JD 并生成草稿"]
        choice, ok = QInputDialog.getItem(self, "本场岗位", "岗位快照独立于公共岗位库", names, editable=False)
        if not ok:
            return
        if choice == names[-2]:
            self.run_task(
                "匹配岗位",
                lambda: self.cloud().recommend_jobs(self.project["resume"], jobs),
                lambda value: self.show_json("岗位推荐（未自动选定）", value),
            )
            return
        if choice == names[-1]:
            jd, ok = QInputDialog.getMultiLineText(self, "岗位 JD", "粘贴岗位要求")
            if ok:
                self.run_task("生成岗位标准草稿", lambda: self.cloud().job_draft(jd), self.adopt_job)
            return
        self.adopt_job(copy.deepcopy(jobs[names.index(choice)]))

    def adopt_job(self, job):
        from .job_ui import JobDialog

        dialog = JobDialog(job, self)
        dialog.setWindowTitle("确认本场岗位快照")
        if dialog.exec() == QDialog.Accepted:
            job = dialog.value()
            job["reviewed"] = True
            job.setdefault("tier", "基层")
            self.project["job_snapshot"] = job
            self.project["job"] = job.get("name", "")
            self.project["leadership_standard"] = leadership_standard()
            self.save()
            self.update_overview()

    def show_json(self, title, value):
        dialog = TextDialog(title, json.dumps(value, ensure_ascii=False, indent=2), self, True)
        dialog.exec()

    def generate_plan(self):
        if not self.save():
            return
        snapshot = copy.deepcopy(self.project)

        def ready(plan):
            self.adopt_generated(snapshot, "面试计划", plan, lambda: self.set_plan(plan))

        self.run_task("生成简历定制问题", lambda: self.cloud().generate_plan(snapshot), ready)

    def set_plan(self, plan):
        plan = validate_plan(plan, {b["id"] for b in self.project["resume"]["blocks"]})
        self.project["plan"] = plan
        self.save()
        self.aligned.bind(self.project)
        self.update_overview()
        self.tabs.setCurrentIndex(1)

    def adopt_generated(self, snapshot, label, value, apply):
        if not self.project or self.project["id"] != snapshot["id"]:
            return
        from .draft_ui import DraftReviewDialog

        dialog = DraftReviewDialog(label, value, self)
        if dialog.exec() == QDialog.Accepted:
            dirty = self.save_timer.isActive()
            if not self.save():
                return
            if (dirty or self.project["revision"] > snapshot["revision"] + 1) and QMessageBox.question(
                self, "内容已修改", "生成期间本场有新修改，是否仍采用新草稿？"
            ) != QMessageBox.Yes:
                return
            apply()

    def confirm_plan(self):
        if not self.project.get("plan", {}).get("questions"):
            self.error("请先添加或生成问题")
            return
        self.project["resume"]["verified"] = True
        self.project["status"] = "ready"
        if not self.save():
            return
        self.project = self.store.snapshot(
            self.project["id"], kind="plan", expected_revision=self.project["revision"]
        )
        self.open_project(self.project)

    def rewrite_question(self, question_id):
        question = next((q for q in self.project["plan"]["questions"] if q["id"] == question_id), None)
        if question is None:
            return
        instruction, ok = QInputDialog.getText(self, "改写此题", "希望怎样调整？例如：更聚焦本人贡献")
        if not ok or not instruction.strip():
            return
        original = copy.deepcopy(question)
        pid = self.project["id"]
        blocks = copy.deepcopy(self.project["resume"]["blocks"])
        job = copy.deepcopy(self.project["job_snapshot"])

        def ready(proposal):
            if not self.project or self.project["id"] != pid:
                return
            current = next((q for q in self.project["plan"]["questions"] if q["id"] == question_id), None)
            if current is None:
                return
            from .draft_ui import DraftReviewDialog

            if DraftReviewDialog("单题改写", {"questions": [proposal]}, self).exec() != QDialog.Accepted:
                return
            if (
                current != original
                and QMessageBox.question(self, "问题已修改", "生成期间该题有修改，仍采用改写？")
                != QMessageBox.Yes
            ):
                return
            current.clear()
            current.update(proposal)
            self.schedule_save()
            self.aligned.rebuild()

        self.run_task(
            "改写当前问题", lambda: self.cloud().rewrite_question(original, blocks, job, instruction), ready
        )

    def suggest(self, question_id):
        if not self.settings.get("live_suggestions"):
            self.error("可在设置中开启追问建议")
            return
        question = next((q for q in self.project["plan"]["questions"] if q["id"] == question_id), {})
        transcript = copy.deepcopy(self.project["transcript"])

        def ready(value):
            self.suggestion.setText("可选追问：" + value["question"] + "\n依据：" + value.get("reason", ""))
            self.suggestion.show()

        self.run_task("生成可选追问", lambda: self.cloud().suggest(question, transcript), ready)

    def generate_evaluation(self):
        if self.runtime:
            self.error("请先结束采集，整理记录后生成评价")
            return
        if not self.save():
            return
        snapshot = copy.deepcopy(self.project)
        standards = snapshot.get("leadership_standard") or leadership_standard()

        def ready(value):
            def apply():
                validated = validate_evaluation(
                    value,
                    {t["id"] for t in self.project["transcript"]},
                    management_required=self.project.get("job_snapshot", {}).get(
                        "management_required", False
                    ),
                )
                self.project["evaluation"]["draft"] = validated
                self.project["evaluation"]["needs_review"] = True
                self.save()
                self.evaluation.bind(self.project)
                self.tabs.setCurrentIndex(4)

            self.adopt_generated(snapshot, "评价草稿", value, apply)

        self.run_task("按证据生成评价", lambda: self.cloud().evaluate(snapshot, standards), ready)

    def confirm_evaluation(self):
        self.project["evaluation"]["needs_review"] = False
        self.project["status"] = "completed"
        if not self.save():
            return
        self.project = self.store.snapshot(
            self.project["id"], kind="evaluation", expected_revision=self.project["revision"]
        )
        self.open_project(self.project)
        self.tabs.setCurrentIndex(4)

    def refresh_transcripts(self):
        rows = self.project.get("transcript", [])
        self.transcripts.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                f"{row.get('start', 0):.1f}s",
                row.get("speaker", ""),
                row.get("manual", row.get("corrected", row.get("raw", ""))),
                row.get("status", ""),
            ]
            for col, value in enumerate(values):
                self.transcripts.setItem(index, col, QTableWidgetItem(str(value)))
        self.transcripts.setColumnWidth(2, 650)
        self.transcripts.resizeRowsToContents()

    def edit_transcript(self, row, col):
        segment = self.project["transcript"][row]
        if col == 1:
            speaker, ok = QInputDialog.getItem(
                self, "说话人", "修正归属", ["我", "候选人", "不确定"], editable=True
            )
            if ok:
                segment["speaker"] = speaker
        else:
            dialog = TextDialog(
                "校正转写 · 原始识别保留",
                segment.get("manual", segment.get("corrected", segment["raw"])),
                self,
            )
            if dialog.exec() != QDialog.Accepted:
                return
            segment["manual"] = dialog.value()
        segment["revision"] = segment.get("revision", 0) + 1
        self.project["evaluation"]["needs_review"] = True
        self.save()
        self.refresh_transcripts()

    def locate_evidence(self, ref):
        self.tabs.setCurrentIndex(2)
        for index, row in enumerate(self.project["transcript"]):
            if row["id"] == ref:
                self.transcripts.selectRow(index)
                self.transcripts.scrollToItem(self.transcripts.item(index, 0))
                break

    def start_capture(self):
        from .capture_ui import start_capture_dialog

        start_capture_dialog(self)

    def pause_capture(self):
        if not self.runtime:
            return
        runtime = self.runtime
        if self.pause_button.text() == "暂停":
            self.run_task("暂停采集", runtime.pause, lambda _: self.pause_button.setText("继续"))
        else:
            self.run_task("恢复采集", runtime.resume, lambda _: self.pause_button.setText("暂停"))

    def end_capture(self):
        if not self.runtime:
            return
        runtime = self.runtime
        self.run_task("结束采集并整理尾段", lambda: runtime.stop(), lambda _: None)

    def capture_finished(self):
        self.runtime = None
        self.project["status"] = "review"
        self.pause_button.setText("暂停")
        self.save()
        self.statusBar().showMessage("采集已结束。可复核记录，然后主动生成评价。")

    def on_audio(self, event):
        kind = event.get("type")
        if kind == "level":
            track = event.get("track")
            if track in self.audio_levels:
                value = min(10, max(0, int(event.get("peak", 0) * 10)))
                self.audio_levels[track].setText(
                    ("我" if track == "local" else "远端") + "：" + "▰" * value + "▱" * (10 - value)
                )
            return
        if kind == "partial":
            self.partial_label.setText(
                ("我：" if event.get("track") == "local" else "候选人：") + event.get("text", "")[-100:]
            )
            return
        if kind == "final":
            start = event.get("start_ms", 0) / 1000
            end = event.get("end_ms", 0) / 1000
            segment = {
                "id": str(event.get("id") or uuid.uuid4().hex),
                "source": event.get("track", ""),
                "speaker": "我" if event.get("track") == "local" else "候选人",
                "start": start,
                "end": end,
                "raw": event.get("text", ""),
                "revision": 0,
                "status": "待核实归属" if event.get("speaker_state") == "uncertain" else "本地转写",
            }
            segment["session_start_ms"] = event.get("session_start_ms", event.get("start_ms", 0))
            segment["session_end_ms"] = event.get("session_end_ms", event.get("end_ms", 0))
            segment["recording_session_id"] = getattr(
                getattr(self, "capture_session", None), "recording_session", None
            )
            self.project["transcript"].append(segment)
            self.partial_label.setText(segment["speaker"] + "：" + segment["raw"][-100:])
            self.project["transcript"].sort(key=lambda s: s["start"])
            self.schedule_save()
            self.refresh_transcripts()
            if self.project.get("cloud_correction") and segment["raw"]:
                self.correct_segment(segment)
        elif kind in {"error", "gap"}:
            self.project.setdefault("interruptions", []).append(event)
            self.schedule_save()
            self.save_label.setText("采集提示：" + str(event.get("reason", event.get("message", event))))
        elif kind == "status":
            self.statusBar().showMessage(str(event.get("state", event.get("message", event))))

    def retry_corrections(self):
        for segment in self.project.get("transcript", []):
            if (
                "manual" not in segment
                and not segment.get("corrected")
                and "等待" not in segment.get("status", "")
            ):
                self.correct_segment(segment)

    def correct_segment(self, segment):
        if len(self.tasks) >= 24:
            segment["status"] = "校正队列已满，保留本地转写待重试"
            return
        pid = self.project["id"]
        sid = segment["id"]
        revision = segment.get("revision", 0)
        raw = segment["raw"]
        context = "\n".join(t.get("raw", "") for t in self.project["transcript"][-8:-1])
        job = Job(lambda: self.cloud().correct(raw, context))
        self.tasks.append(job)
        segment["status"] = "等待云端校正"

        def ready(text):
            if not self.project or self.project["id"] != pid:
                return
            current = next((t for t in self.project["transcript"] if t["id"] == sid), None)
            if current is None:
                return
            if current.get("revision", 0) != revision or "manual" in current:
                current["correction_suggestion"] = text
                current["status"] = "人工修改已保留"
            else:
                current["corrected"] = text
                current["status"] = "已校正"
            self.schedule_save()
            self.refresh_transcripts()

        def failed(message):
            if self.project and self.project["id"] == pid:
                current = next((t for t in self.project["transcript"] if t["id"] == sid), None)
                if current:
                    current["status"] = "校正失败，可稍后重试"
                self.schedule_save()
                self.refresh_transcripts()
            self.statusBar().showMessage("云端校正失败，本地转写继续")

        job.signals.result.connect(ready)
        job.signals.error.connect(failed)
        job.signals.finished.connect(lambda: self.tasks.remove(job) if job in self.tasks else None)
        self.correction_pool.start(job)

    def archive_selected(self):
        p = self.selected_project()
        if p:
            self.store.set_archived(p["id"], not p["archived"], expected_revision=p["revision"])
            self.refresh()

    def trash_selected(self):
        p = self.selected_project()
        if not p:
            return
        if p.get("trashed_at"):
            self.store.restore_project(p["id"], expected_revision=p["revision"])
        else:
            self.store.trash_project(p["id"], expected_revision=p["revision"])
        self.refresh()

    def delete_selected(self):
        p = self.selected_project()
        if not p or not p.get("trashed_at"):
            return
        if (
            QMessageBox.question(self, "永久删除", "删除所有受管材料、历史版本及录音，无法恢复。确认？")
            == QMessageBox.Yes
        ):
            self.store.permanently_delete(p["id"], confirmation=p["id"], expected_revision=p["revision"])
            self.refresh()

    def export_project(self):
        if not self.save():
            return
        path, _ = QFileDialog.getSaveFileName(self, "完整备份", "面试备份.zip", "ZIP (*.zip)")
        if path:
            pid = self.project["id"]
            self.run_task("导出备份", lambda: self.store.export_project(pid, path), lambda _: None)

    def import_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入完整备份", "", "ZIP (*.zip)")
        if path:
            self.run_task("导入备份", lambda: self.store.import_project(path), lambda p: self.open_project(p))

    def export_evaluation(self):
        if not self.save():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出评价", "面试评价.md", "Markdown (*.md);;JSON (*.json)"
        )
        if path:
            try:
                exported = copy.deepcopy(self.project)
                versions = [s for s in exported.get("snapshots", []) if s.get("kind") == "evaluation"]
                if versions:
                    exported["evaluation"]["draft"] = versions[-1]["content"]
                    exported["evaluation"]["suggested_level"] = versions[-1].get("suggested_level")
                elif (
                    QMessageBox.question(self, "尚未确认", "没有正式评价版本，是否导出当前草稿？")
                    != QMessageBox.Yes
                ):
                    return
                export_report(exported, Path(path))
            except Exception as exc:
                self.error(str(exc))

    def closeEvent(self, event):
        if self.runtime:
            self.error("请先结束采集，等待尾段保存后退出")
            event.ignore()
            return
        if self.tasks:
            self.error("后台任务尚未结束，请等待结果后退出")
            event.ignore()
            return
        if not self.save():
            event.ignore()
            return
        self.store.close()
        event.accept()
