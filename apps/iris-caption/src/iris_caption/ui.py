"""Standalone Qt UI; controllers own capture, export, and revision validation.

GUI methods run on the Qt thread. Call mark_saved after a successful export and
cancel_pending_close after a canceled/failed export. No Iris runtime imports.
"""
from __future__ import annotations

try:
    from PySide6.QtCore import Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QPixmap, QShortcut
    from PySide6.QtWidgets import (
        QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
        QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider,
        QTextEdit, QVBoxLayout, QWidget,
    )
except ImportError:
    PYSIDE_AVAILABLE = False
else:
    PYSIDE_AVAILABLE = True


if not PYSIDE_AVAILABLE:
    class MainWindow:
        """Optional dependency placeholder."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError("The desktop UI requires PySide6.")
else:
    _STATUS = {
        "partial": "转写中", "transcribing": "转写中", "final": "已定稿",
        "pending": "待校正", "queued": "待校正", "correcting": "校正中",
        "corrected": "已校正", "failed": "校正待重试", "error": "校正待重试",
        "manual": "人工修改", "cancelled": "已取消",
        "local": "词典校正 · 待云端", "retry": "校正待重试",
    }
    from .theme import APP_STYLE as _STYLE

    def _timestamp(value):
        seconds = max(0, int(value or 0)) // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    class _SegmentCard(QFrame):
        edit_clicked = Signal(str)
        speaker_clicked = Signal(str)

        def __init__(self, segment, parent=None):
            super().__init__(parent)
            self.setObjectName("segment")
            self.segment = segment
            layout = QVBoxLayout(self)
            layout.setContentsMargins(32, 24, 32, 24)
            layout.setSpacing(12)
            top = QHBoxLayout()
            self.meta = QLabel()
            self.meta.setObjectName("muted")
            top.addWidget(self.meta)
            self.speaker_label = QLabel()
            self.speaker_label.setTextFormat(Qt.TextFormat.PlainText)
            self.speaker_label.setObjectName("muted")
            top.addWidget(self.speaker_label)
            top.addStretch()
            self.speaker_button = QPushButton("说话人…")
            self.speaker_button.setObjectName("quiet")
            self.speaker_button.setAccessibleName("指定此句段的说话人")
            self.speaker_button.clicked.connect(lambda: self.speaker_clicked.emit(str(self.segment["id"])))
            top.addWidget(self.speaker_button)
            self.edit_button = QPushButton("编辑")
            self.edit_button.setObjectName("quiet")
            self.edit_button.setAccessibleName("编辑此句段的校正结果")
            self.edit_button.clicked.connect(lambda: self.edit_clicked.emit(str(self.segment["id"])))
            top.addWidget(self.edit_button)
            layout.addLayout(top)
            self.text_layout = QHBoxLayout()
            layout.addLayout(self.text_layout)
            self.raw_box, self.raw = self._text_box("原始转写", "raw")
            self.corrected_box, self.corrected = self._text_box("校正结果", "corrected")
            self.text_layout.addWidget(self.raw_box, 1)
            self.text_layout.addWidget(self.corrected_box, 1)

        def _text_box(self, title, name):
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("muted" if name == "raw" else "eyebrow")
            layout.addWidget(label)
            text = QLabel()
            text.setObjectName(name)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(text)
            layout.addStretch()
            return box, text

        def render(self, columns, size, projector, comparison, speakers_enabled=False):
            item = self.segment
            state = "人工修改" if item.get("manual") else _STATUS.get(item.get("status"), item.get("status", "待校正"))
            self.meta.setText(f"{_timestamp(item.get('start_ms'))} — {_timestamp(item.get('end_ms'))}  ·  {state}")
            speaker_state = item.get("speaker_state", "unknown")
            overlap = bool(item.get("overlap_intervals")) or speaker_state == "overlap"
            label = ("多人同时讲话 · 可能不完整" if overlap else
                     (str(item.get("speaker_name") or item.get("speaker_id") or "未知说话人")
                      if speaker_state == "confirmed" else
                      "说话人待确认" if speaker_state == "pending" else "未知说话人"))
            self.speaker_label.setText(label)
            self.speaker_label.setVisible(speakers_enabled)
            self.speaker_button.setVisible(speakers_enabled and not projector)
            self.speaker_button.setEnabled(item.get("status") not in {"partial", "transcribing"})
            self.raw.setText(str(item.get("raw") or ""))
            corrected = item.get("corrected")
            self.corrected.setText(str(corrected or item.get("raw") or ""))
            self.text_layout.setDirection(QHBoxLayout.Direction.LeftToRight if columns else QHBoxLayout.Direction.TopToBottom)
            self.raw_box.setVisible(not projector or comparison)
            self.edit_button.setVisible(not projector)
            self.edit_button.setEnabled(item.get("status") not in {"partial", "transcribing"})
            for label in (self.raw, self.corrected):
                font = label.font()
                font.setPointSize(size)
                label.setFont(font)

    class MainWindow(QMainWindow):
        """Signal-only view; selected_device is an integer index or None.

        Segment updates mark dirty. Manual corrections survive late automatic
        updates. mark_saved closes a pending close only if no new update arrived.
        """
        start_requested = Signal()
        pause_requested = Signal()
        stop_requested = Signal()
        settings_requested = Signal()
        speakers_requested = Signal()
        speaker_assign_requested = Signal(str)
        materials_requested = Signal()
        knowledge_requested = Signal()
        statistics_requested = Signal()
        export_requested = Signal(str, str)
        edit_requested = Signal(str, str)
        microphone_changed = Signal(int)

        def __init__(self, parent=None, *, persist_preferences=False):
            super().__init__(parent)
            self._persist_preferences = persist_preferences
            self._loading_preferences = True
            self._preferred_microphone = ""
            self._preferences_timer = QTimer(self)
            self._preferences_timer.setSingleShot(True)
            self._preferences_timer.setInterval(300)
            self._preferences_timer.timeout.connect(self.save_preferences)
            self.setWindowTitle("声笺 · Iris Caption")
            self.resize(1120, 800)
            self.setMinimumSize(720, 480)
            self.setStyleSheet(_STYLE)
            self._speaker_continuations = set()
            self._speakers_enabled = False
            self._segments = {}
            self._cards = {}
            self._dirty = False
            self._capture_active = False
            self._capture_state = "idle"
            self._capture_pending = None
            self._generation = 0
            self._close_generation = None
            self._follow = True
            self._unseen = set()
            self._projector = False
            self._was_maximized = False
            self._build_ui()
            self._refresh_controls()
            self.read_preferences()
            self._loading_preferences = False
            self.font_slider.valueChanged.connect(self._schedule_preferences)
            self.layout_combo.currentIndexChanged.connect(self._schedule_preferences)
            self.comparison_button.toggled.connect(self._schedule_preferences)
            self._escape = QShortcut(QKeySequence("Escape"), self)
            self._escape.activated.connect(lambda: self.set_projector_mode(False))

        def _button(self, text, signal, row):
            button = QPushButton(text)
            button.setObjectName("quiet")
            button.clicked.connect(signal.emit)
            row.addWidget(button)
            return button

        def _build_ui(self):
            settings_menu = self.menuBar().addMenu("设置")
            settings_action = settings_menu.addAction("设置…")
            settings_action.triggered.connect(self.settings_requested.emit)
            self.speakers_action = settings_menu.addAction("本场会议说话人…")
            self.speakers_action.triggered.connect(self.speakers_requested.emit)
            self.speakers_action.setVisible(False)
            help_menu = self.menuBar().addMenu("帮助")
            self.about_action = QAction("关于声笺…", self)
            self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
            self.about_action.triggered.connect(self.show_about)
            help_menu.addAction(self.about_action)
            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.setContentsMargins(32, 24, 32, 24)
            layout.setSpacing(17)
            self.header = QWidget()
            header = QHBoxLayout(self.header)
            header.setContentsMargins(0, 0, 0, 0)
            from .branding import icon_path
            self.logo_label = QLabel()
            self.logo_label.setAccessibleName('声笺 Iris-caption')
            self.logo_label.setFixedSize(40, 40)
            self.logo_label.setPixmap(QPixmap(str(icon_path())).scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            header.addWidget(self.logo_label)
            brand = QLabel("声笺")
            brand.setObjectName("brand")
            header.addWidget(brand)
            subtitle = QLabel("Iris Caption")
            subtitle.setObjectName("muted")
            header.addWidget(subtitle)
            header.addStretch()
            self._button("会议资料", self.materials_requested, header)
            self._button("知识与词典", self.knowledge_requested, header)
            self.statistics_button = self._button("统计", self.statistics_requested, header)
            self._button("设置", self.settings_requested, header)
            layout.addWidget(self.header)
            self.capture_bar = QWidget()
            capture = QHBoxLayout(self.capture_bar)
            capture.setContentsMargins(0, 0, 0, 0)
            
            self.microphone_combo = QComboBox()
            self.microphone_combo.setMinimumWidth(240)
            self.microphone_combo.setMaximumWidth(360)
            self.microphone_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            self.microphone_combo.currentTextChanged.connect(self.microphone_combo.setToolTip)
            self.microphone_combo.setAccessibleName("输入麦克风")
            self.microphone_combo.currentIndexChanged.connect(self._device_changed)
            capture.addWidget(self.microphone_combo)
            self.start_button = self._button("开始 / 继续", self.start_requested, capture)
            self.start_button.setObjectName("primary")
            self.pause_button = self._button("暂停", self.pause_requested, capture)
            self.stop_button = self._button("结束", self.stop_requested, capture)
            capture.addStretch()
            self.export_button = QPushButton("导出记录 ▾")
            menu = QMenu(self.export_button)
            for title, kind, mode in (
                ("Markdown · 原文与校正", "markdown", "both"),
                ("SRT · 校正结果（默认）", "srt", "corrected"),
                ("SRT · 原文", "srt", "raw"), ("SRT · 双行对照", "srt", "both"),
            ):
                action = QAction(title, menu)
                action.triggered.connect(lambda checked=False, k=kind, m=mode: self.export_requested.emit(k, m))
                menu.addAction(action)
            self.export_button.setMenu(menu)
            capture.addWidget(self.export_button)
            layout.addWidget(self.capture_bar)
            view = QHBoxLayout()
            self.layout_combo = QComboBox()
            self.layout_combo.addItems(["上下对照", "双列对照"])
            self.layout_combo.setAccessibleName("字幕排列方式")
            self.layout_combo.currentIndexChanged.connect(self._render_all)
            view.addWidget(self.layout_combo)
            view.addWidget(QLabel("字号"))
            self.font_slider = QSlider(Qt.Orientation.Horizontal)
            self.font_slider.setRange(14, 48)
            self.font_slider.setValue(21)
            self.font_slider.setFixedWidth(96)
            self.font_slider.setAccessibleName("字幕字号")
            self.font_slider.valueChanged.connect(self._render_all)
            view.addWidget(self.font_slider)
            self.font_label = QLabel("21")
            view.addWidget(self.font_label)
            view.addStretch()
            self.comparison_button = QPushButton("投屏对照")
            self.comparison_button.setCheckable(True)
            self.comparison_button.setVisible(False)
            self.comparison_button.toggled.connect(self._render_all)
            view.addWidget(self.comparison_button)
            self.projector_button = QPushButton("全屏投屏")
            self.projector_button.setCheckable(True)
            self.projector_button.toggled.connect(self.set_projector_mode)
            view.addWidget(self.projector_button)
            layout.addLayout(view)
            self.speaker_status_label = QLabel("说话人区分：等待模型")
            self.speaker_status_label.setObjectName("muted")
            self.speaker_status_label.setTextFormat(Qt.TextFormat.PlainText)
            self.speaker_status_label.setWordWrap(True)
            self.speaker_status_label.setAccessibleName("说话人区分运行状态")
            self.speaker_status_label.hide()
            layout.addWidget(self.speaker_status_label)
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.transcript = QWidget()
            self.transcript.setObjectName("transcript")
            self.transcript.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.transcript_layout = QVBoxLayout(self.transcript)
            self.transcript_layout.setContentsMargins(0, 0, 0, 0)
            self.transcript_layout.setSpacing(0)
            self.empty_label = QLabel("让每一句话，清晰留下。\n\n选择麦克风，点击「开始」即可转写。\n原文先显示，校正随后更新。")
            self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_label.setObjectName("empty")
            self.empty_label.setMinimumHeight(240)
            self.transcript_layout.addWidget(self.empty_label)
            self.transcript_layout.addStretch()
            self.scroll.setWidget(self.transcript)
            bar = self.scroll.verticalScrollBar()
            bar.valueChanged.connect(self._scroll_changed)
            bar.rangeChanged.connect(self._range_changed)
            layout.addWidget(self.scroll, 1)
            footer = QHBoxLayout()
            self.status_label = QLabel("准备就绪")
            self.status_label.setObjectName("status")
            self.status_label.setTextFormat(Qt.TextFormat.PlainText)
            footer.addWidget(self.status_label)
            self.saved_label = QLabel("尚无记录 · by Iris")
            self.saved_label.setObjectName("muted")
            footer.addWidget(self.saved_label)
            self.statistics_label = QLabel("原文 0 字 · 校正 0 字")
            self.statistics_label.setObjectName("muted")
            footer.addWidget(self.statistics_label)
            footer.addStretch()
            self.follow_button = QPushButton("正在跟随实时")
            self.follow_button.setObjectName("quiet")
            self.follow_button.clicked.connect(self.follow_live)
            footer.addWidget(self.follow_button)
            layout.addLayout(footer)

        @property
        def selected_device(self):
            return self.microphone_combo.currentData()

        def show_about(self):
            from .about_dialog import AboutDialog
            AboutDialog(self).exec()

        def read_preferences(self):
            """Restore harmless display choices; never start capture or fullscreen."""
            if not self._persist_preferences:
                return
            from .settings import read_preferences
            from .persistence import PersistenceError
            self._loading_preferences = True
            try:
                preferences = read_preferences()
                self.font_slider.setValue(preferences.get("font_size", 21))
                self.layout_combo.setCurrentIndex(int(preferences.get("layout") == "columns"))
                self.comparison_button.setChecked(preferences.get("comparison", False))
                self._preferred_microphone = preferences.get("microphone_name", "")
            except (PersistenceError, OSError):
                self._persist_preferences = False
                self.status_label.setText("显示偏好无法读取，可能已损坏或来自更新版本；原文件未改动，本次不保存显示偏好。请恢复备份或使用匹配版本。")
            finally:
                self._loading_preferences = False

        def _schedule_preferences(self, *_):
            if self._persist_preferences and not self._loading_preferences:
                self._preferences_timer.start()

        def save_preferences(self):
            """Flush pending changes, reporting filesystem failures without a modal."""
            self._preferences_timer.stop()
            if not self._persist_preferences or self._loading_preferences:
                return
            from .settings import save_preferences
            from .persistence import PersistenceError
            preferences = {
                "font_size": self.font_slider.value(),
                "layout": "columns" if self.layout_combo.currentIndex() == 1 else "stacked",
                "comparison": self.comparison_button.isChecked(),
                "microphone_name": self._preferred_microphone or self.microphone_combo.currentText(),
            }
            try:
                save_preferences(preferences)
            except PersistenceError:
                self._persist_preferences = False
                self.status_label.setText("显示偏好无法保存：原文件格式不兼容或已损坏，已保留原文件。请恢复备份或使用匹配版本。")
            except OSError:
                self.status_label.setText("显示偏好暂未保存；下次调整时将重试。")

        def flush_preferences(self):
            """Flush a pending debounced save before QApplication quits."""
            if self._preferences_timer.isActive():
                self.save_preferences()

        def set_devices(self, devices):
            """Restore by stable name after asynchronous enumeration, then fallback."""
            name = self._preferred_microphone or self.microphone_combo.currentText()
            self.microphone_combo.blockSignals(True)
            self.microphone_combo.clear()
            for device in devices:
                self.microphone_combo.addItem(str(device["name"]), int(device["index"]))
            index = self.microphone_combo.findText(name)
            if index >= 0:
                self.microphone_combo.setCurrentIndex(index)
            self._refresh_controls()
            self.microphone_combo.blockSignals(False)
            self.microphone_combo.setToolTip(self.microphone_combo.currentText())

        def _device_changed(self, index):
            if index >= 0:
                self._preferred_microphone = self.microphone_combo.currentText()
                self._schedule_preferences()
                self.microphone_changed.emit(self.selected_device)

        def set_speakers_enabled(self, enabled):
            """Controller supplies the active session opt-in, not next-session settings."""
            self._speakers_enabled = enabled is True
            self.speakers_action.setVisible(self._speakers_enabled)
            self.speaker_status_label.setVisible(self._speakers_enabled)
            self.speaker_status_label.setText("说话人区分：等待模型" if self._speakers_enabled else "")
            self._render_all()

        def set_speaker_status(self, text):
            """Independent status survives recording/correction status updates."""
            self.speaker_status_label.setText(str(text))

        def set_reading_groups(self, groups):
            """Keep segments independently editable; mark continuations only."""
            self._speaker_continuations = {str(key) for group in groups for key in group[1:]}
            self._render_all()

        def set_statistics(self, raw, corrected):
            """Show controller-provided character counts without retaining text."""
            self.statistics_label.setText(f"原文 {max(0, int(raw)):,} 字 · 校正 {max(0, int(corrected)):,} 字")

        def set_status(self, text):
            self.status_label.setText(text)

        def clear_segments(self):
            """Reset the transcript for a new session without prompting to save.

            The controller must resolve unsaved content before calling this.
            Display preferences and microphone selection are preserved.
            """
            self.cancel_pending_close()
            for card in self._cards.values():
                self.transcript_layout.removeWidget(card)
                card.hide()
                card.deleteLater()
            self._speaker_continuations.clear()
            self._cards.clear()
            self._segments.clear()
            self._unseen.clear()
            self._dirty = False
            self._generation += 1
            self._follow = True
            self.empty_label.show()
            self.saved_label.setText("尚无记录 · by Iris")
            self._update_follow_label()
            self._refresh_controls()
            self.scroll.verticalScrollBar().setValue(0)
            QTimer.singleShot(0, self, self._scroll_to_end)

        def remove_segment(self, segment_id):
            key = str(segment_id)
            item = self._segments.get(key)
            if item is None or item.get('status') != 'partial' or item.get('manual'):
                return
            self._segments.pop(key)
            card = self._cards.pop(key)
            self.transcript_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
            self._unseen.discard(key)
            if not self._segments:
                self._dirty = False
                self.empty_label.show()
                self.saved_label.setText('尚无记录 · by Iris')
            self._update_follow_label()
            self._refresh_controls()

        def upsert_segment(self, segment):
            item = dict(segment)
            key = str(item["id"])
            item["id"] = key
            old = self._segments.get(key)
            if old:
                item = {**old, **item}
                if old.get("manual") and not segment.get("manual"):
                    item.update(corrected=old.get("corrected"), manual=True, status="manual")
            if item == old:
                return
            self._segments[key] = item
            if key not in self._cards:
                card = _SegmentCard(item)
                card.edit_clicked.connect(self._edit_segment)
                card.speaker_clicked.connect(self.speaker_assign_requested.emit)
                self._cards[key] = card
                self.transcript_layout.insertWidget(self.transcript_layout.count() - 1, card)
            else:
                self._cards[key].segment = item
            self.empty_label.hide()
            self._render_card(self._cards[key])
            self.mark_dirty()
            if self._follow:
                QTimer.singleShot(0, self, self._scroll_to_end)
            else:
                self._unseen.add(key)
                self._update_follow_label()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, 'font_slider'):
                self._render_all()

        def _render_card(self, card):
            size = max(34, self.font_slider.value()) if self._projector else self.font_slider.value()
            card.render(self.layout_combo.currentIndex() == 1 and self.width() >= 834, size,
                        self._projector, self.comparison_button.isChecked(), self._speakers_enabled)
            if (self._speakers_enabled and str(card.segment['id']) in self._speaker_continuations
                    and card.segment.get('speaker_state') == 'confirmed'
                    and not card.segment.get('overlap_intervals')):
                card.speaker_label.setText(card.speaker_label.text() + ' · 接续发言')

        def _render_all(self, *_):
            self.font_label.setText(str(self.font_slider.value()))
            self.layout_combo.setVisible(not self._projector or self.comparison_button.isChecked())
            for card in self._cards.values():
                self._render_card(card)

        def set_projector_mode(self, enabled):
            enabled = bool(enabled)
            if enabled == self._projector:
                return
            self._projector = enabled
            self.projector_button.setChecked(enabled)
            self.projector_button.setText("退出投屏 · Esc" if enabled else "全屏投屏")
            self.header.setVisible(not enabled)
            self.capture_bar.setVisible(not enabled)
            self.comparison_button.setVisible(enabled)
            if enabled:
                self._was_maximized = self.isMaximized()
                self.showFullScreen()
                self.follow_live()
            elif self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self._render_all()

        def _scroll_changed(self, value):
            bar = self.scroll.verticalScrollBar()
            self._follow = value >= bar.maximum() - 4
            if self._follow:
                self._unseen.clear()
            self._update_follow_label()

        def _range_changed(self, *_):
            if self._follow:
                self._scroll_to_end()

        def _scroll_to_end(self):
            if self._follow:
                bar = self.scroll.verticalScrollBar()
                bar.setValue(bar.maximum())

        def _update_follow_label(self):
            if self._follow:
                self.follow_button.setText("正在跟随实时")
            else:
                count = f" · {len(self._unseen)} 条更新" if self._unseen else ""
                self.follow_button.setText(f"回到实时{count}")

        def follow_live(self):
            self._follow = True
            self._unseen.clear()
            self._scroll_to_end()
            self._update_follow_label()

        def _edit_segment(self, key):
            item = self._segments[key]
            if item.get("status") in {"partial", "transcribing"}:
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("编辑校正结果")
            dialog.resize(620, 340)
            layout = QVBoxLayout(dialog)
            hint = QLabel("仅修改校正结果，原文保留。人工修改不会被自动校正覆盖。")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            editor = QTextEdit()
            editor.setPlainText(str(item.get("corrected") if item.get("corrected") is not None else item.get("raw") or ""))
            layout.addWidget(editor)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                text = editor.toPlainText()
                self.upsert_segment({**item, "corrected": text, "manual": True, "status": "manual"})
                self.edit_requested.emit(key, text)

        def mark_dirty(self):
            self._generation += 1
            self._dirty = True
            self._refresh_controls()
            self.saved_label.setText("未保存 · 仅在内存中")

        @property
        def closing_after_save(self):
            return self._close_generation is not None

        @property
        def capture_state(self):
            return self._capture_state

        def set_capture_state(self, state, *, pending=None):
            """Render capture state; stopping remains active until final events drain."""
            if state not in {"idle", "loading", "recording", "paused", "stopping", "error"}:
                raise ValueError("Unknown capture state")
            self._capture_state = state
            self._capture_pending = pending
            self._capture_active = state in {"loading", "recording", "paused", "stopping"}
            self._refresh_controls()

        def set_capture_active(self, active):
            """Compatibility API for callers without detailed worker state."""
            self.set_capture_state("recording" if active else "idle")

        def _refresh_controls(self):
            state, pending = self._capture_state, self._capture_pending
            closing = self.closing_after_save
            self.start_button.setText({"loading": "加载中…", "recording": "正在收音",
                "paused": "继续", "stopping": "结束中…", "error": "重试"}.get(state, "开始"))
            self.pause_button.setText("继续中…" if pending == "resume" else
                "暂停中…" if pending == "pause" else "继续" if state == "paused" else "暂停")
            self.stop_button.setText("结束中…" if state == "stopping" else "结束")
            self.start_button.setEnabled(not closing and not pending and state in {"idle", "error", "paused"})
            self.pause_button.setEnabled(not closing and not pending and state in {"recording", "paused"})
            self.stop_button.setEnabled(not closing and state in {"loading", "recording", "paused"})
            self.microphone_combo.setEnabled(not closing and not self._capture_active and self.microphone_combo.count() > 0)
            useful = any(item.get("raw") or item.get("corrected") for item in self._segments.values())
            self.export_button.setEnabled(bool(useful) and not closing)
            for action in self.export_button.menu().actions():
                action.setEnabled(bool(useful) and not closing)

        def prepare_close_export(self):
            """Rebase pending close to the drained snapshot before exporting.

            Returns False when no close is pending or capture is still active.
            Call after set_capture_active(False), immediately before snapshotting.
            mark_saved acknowledges success; cancel_pending_close cancels failure.
            """
            if self._capture_active or not self.closing_after_save:
                return False
            self._close_generation = self._generation
            return True

        def mark_saved(self):
            """Acknowledge successful export; finish an unchanged, drained close."""
            pending = self._close_generation
            if pending is not None and self._capture_active:
                self.saved_label.setText("正在等待录音结束与剩余字幕，请完成后保存")
                return
            self._close_generation = None
            self._refresh_controls()
            if pending is not None and pending != self._generation:
                self.saved_label.setText("有新的未保存字幕 · 请再次保存")
                return
            self._dirty = False
            self.saved_label.setText("已保存 · by Iris")
            if pending is not None:
                self.close()

        def cancel_pending_close(self):
            """Call after export cancellation/failure; retain unsaved contents."""
            self._close_generation = None
            self._refresh_controls()

        def closeEvent(self, event: QCloseEvent):
            if not self._dirty and not self._capture_active:
                self.save_preferences()
                event.accept()
                return
            event.ignore()
            if self._close_generation is not None:
                return
            choice = QMessageBox.warning(
                self, "保存会议记录？",
                "录音仍在进行。保存并退出将先结束录音，保存剩余字幕。" if self._capture_active
                else "会议记录尚未保存。关闭后将无法恢复。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Discard:
                self.save_preferences()
                event.accept()
            elif choice == QMessageBox.StandardButton.Save:
                self._close_generation = self._generation
                self._refresh_controls()
                # Queue emission so synchronous mark_saved cannot re-enter this event.
                generation = self._close_generation
                QTimer.singleShot(0, lambda: self._export_pending_close(generation))

        def _export_pending_close(self, generation):
            if self._close_generation == generation:
                self.export_requested.emit("markdown", "both")
