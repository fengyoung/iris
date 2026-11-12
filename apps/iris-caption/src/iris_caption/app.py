"""Application entry point (also used by the macOS bundle)."""
from __future__ import annotations

import argparse
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description='声笺 · Iris Caption')
    from .version import __version__, version_text
    parser.add_argument('--version', action='version', version=version_text())
    parser.add_argument('--demo', action='store_true', help='演示字幕，不录音或调用云端')
    parser.add_argument('--smoke-test', action='store_true', help='离屏启动验证后自动退出')
    parser.add_argument('--diagnostics', action='store_true', help='检查运行依赖，不录音或联网')
    args = parser.parse_args()
    if args.diagnostics:
        from .diagnostics import main as diagnostics_main
        return diagnostics_main()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from .branding import icon_path
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('声笺')
    app.setApplicationVersion(__version__)
    app.setOrganizationName('Iris')
    app.setWindowIcon(QIcon(str(icon_path())))
    window = MainWindow(persist_preferences=not (args.demo or args.smoke_test))
    app.aboutToQuit.connect(window.flush_preferences)
    controller = None
    if args.demo or args.smoke_test:
        window.set_status('演示模式 · 未收音，未调用云端')
        samples = [
            ('我们今天讨论一下实时字幕的方案。', '我们今天讨论一下实时字幕的方案。'),
            ('希望爱丽丝能识别人名和英文术语。', '希望 Iris 能识别人名和英文术语。'),
            ('这个方案可以，也不能说可以，还是要先测试。',
             '这个方案可以……也不能说可以，还是要先测试。'),
        ]
        for i, (raw, corrected) in enumerate(samples):
            window.upsert_segment(dict(id=str(i), start_ms=i*4000, end_ms=(i+1)*4000,
                                       raw=raw, corrected=corrected, status='corrected', manual=False))
        window.mark_saved()
    else:
        from .controller import Controller
        from .persistence import PersistenceError
        try:
            controller = Controller(window)
        except PersistenceError as error:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(window, '本机数据暂不可用', str(error))
            return 1
        app.aboutToQuit.connect(controller.shutdown)
    window.show()
    if args.smoke_test:
        QTimer.singleShot(300, app.quit)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
