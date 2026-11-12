"""Render synthetic UI previews without microphone, keys, or network."""
from pathlib import Path
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from iris_caption.ui import MainWindow


def main():
    output = Path(__file__).resolve().parents[1] / 'output' / 'apple-preview'
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1180, 900)
    window.set_devices([{'index': 0, 'name': 'MacBook 内置麦克风'}])
    window.show()
    app.processEvents()
    window.grab().save(str(output / 'empty.png'))
    samples = [
        ('我们今天讨论一下实时字幕的方案。先保证每一句话都能完整留下。',
         '我们今天讨论实时字幕方案，先保证每一句话都能完整留下。', 'corrected'),
        ('希望爱丽丝能识别人名和英文术语，数字和否定表达也要保留。',
         '希望 Iris 能识别人名和英文术语，数字和否定表达也要保留。', 'corrected'),
        ('这个方案还不能确定，我们先测试三十分钟', '', 'partial'),
    ]
    for index, (raw, corrected, status) in enumerate(samples):
        window.upsert_segment(dict(id=str(index), start_ms=index * 8000,
                                   end_ms=(index + 1) * 8000, raw=raw,
                                   corrected=corrected, status=status, manual=False))
    window.set_status('正在收音 · 字幕实时更新')
    for _ in range(3):
        app.processEvents()
    window.scroll.verticalScrollBar().setValue(0)
    app.processEvents()
    window.grab().save(str(output / 'transcript.png'))
    window.layout_combo.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(output / 'columns.png'))
    window.set_projector_mode(True)
    app.processEvents()
    window.grab().save(str(output / 'projector.png'))
    window.set_projector_mode(False)
    window.resize(720, 600)
    for _ in range(3):
        app.processEvents()
    window.grab().save(str(output / 'compact.png'))
    window.mark_saved()
    window.close()
    from unittest.mock import patch
    from iris_caption.dialogs import SettingsDialog
    with patch('iris_caption.dialogs.read_settings', return_value={}):
        settings = SettingsDialog()
    settings.show()
    app.processEvents()
    settings.grab().save(str(output / 'settings.png'))
    settings.close()
    from PySide6.QtCore import QCoreApplication, QEvent
    settings.deleteLater()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    print(output)


if __name__ == '__main__':
    main()
