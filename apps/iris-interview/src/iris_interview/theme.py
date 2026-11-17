"""Shared restrained desktop theme, inspired by Iris Caption."""

from PySide6.QtGui import QColor, QPalette

TOKENS = {
    "light": {
        "window": "#f3f3f1",
        "surface": "#ffffff",
        "text": "#202124",
        "muted": "#65676c",
        "line": "#d7d9de",
        "blue": "#0969da",
        "selected": "#e5f0ff",
        "error": "#b42318",
    },
    "dark": {
        "window": "#191a1c",
        "surface": "#24262a",
        "text": "#f1f2f4",
        "muted": "#b0b4bc",
        "line": "#444851",
        "blue": "#70b7ff",
        "selected": "#263f5c",
        "error": "#ff9891",
    },
}


def apply_theme(app, mode):
    t = TOKENS.get(mode, TOKENS["light"])
    palette = QPalette()
    for role, token in [
        (QPalette.Window, "window"),
        (QPalette.Base, "surface"),
        (QPalette.AlternateBase, "window"),
        (QPalette.WindowText, "text"),
        (QPalette.Text, "text"),
        (QPalette.Button, "surface"),
        (QPalette.ButtonText, "text"),
        (QPalette.Highlight, "selected"),
        (QPalette.HighlightedText, "text"),
        (QPalette.ToolTipBase, "surface"),
        (QPalette.ToolTipText, "text"),
    ]:
        palette.setColor(role, QColor(t[token]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(t["muted"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ font-size: 14px; }}
        QMainWindow, QDialog {{ background: {t["window"]}; color: {t["text"]}; }}
        QLabel#heading {{ font-size: 26px; font-weight: 600; }}
        QLabel#muted {{ color: {t["muted"]}; }}
        QLabel#error {{ color: {t["error"]}; }}
        QPushButton {{ padding: 7px 12px; border: 1px solid {t["line"]}; border-radius: 8px;
                        background: {t["surface"]}; color: {t["text"]}; }}
        QPushButton:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {t["blue"]}; }}
        QPushButton:disabled {{ color: {t["muted"]}; }}
        QPushButton#primary {{ background: {t["blue"]}; color: {"#ffffff" if mode == "light" else "#14202e"}; border: none; }}
        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{ background: {t["surface"]};
          color: {t["text"]}; border: 1px solid {t["line"]}; border-radius: 6px; padding: 6px; }}
        QTableWidget {{ background: {t["surface"]}; gridline-color: {t["line"]}; }}
        QHeaderView::section {{ background: {t["window"]}; color: {t["text"]}; padding: 8px; border: none; }}
        QTabBar::tab {{ padding: 9px 16px; }}
        QTabBar::tab:selected {{ color: {t["blue"]}; }}
        QFrame#resumeRow {{ background: {t["surface"]}; border-bottom: 1px solid {t["line"]}; }}
        QFrame#activeRow {{ background: {t["selected"]}; border-bottom: 1px solid {t["line"]}; }}
        QScrollArea {{ border: none; }}
        QSplitter::handle {{ background: {t["line"]}; }}
    """)
