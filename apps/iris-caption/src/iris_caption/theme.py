"""Apple-reference tokens adapted to a native desktop reading interface."""
from PySide6.QtGui import QFont, QFontDatabase

COLORS = dict(primary='#0066cc', focus='#0071e3', ink='#1d1d1f', muted='#7a7a7a',
              secondary='#333333', line='#e0e0e0', soft='#f0f0f0', canvas='#ffffff',
              background='#f5f5f7', pearl='#fafafc', disabled='#cccccc')
FONT_SIZES = dict(title=34, heading=21, body=17, caption=14, fine=12)
SPACING = dict(xs=8, sm=12, lg=24, xl=32, xxl=48)
RADII = dict(sm=8, md=11, lg=18)


def font(size, weight=400):
    value = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    value.setPixelSize(size)
    value.setWeight(QFont.Weight(weight))
    return value


APP_STYLE = """
QMainWindow, QDialog { background: %(background)s; color: %(ink)s; }
QWidget { font-family: '.AppleSystemUIFont', 'PingFang SC'; }
QPushButton, QComboBox, QLineEdit, QTextEdit, QTabBar, QTableWidget, QListWidget { font-size: 14px; }
QLabel { color: %(ink)s; background: transparent; }
QLabel#brand, QLabel#dialogTitle { font-size: 34px; font-weight: 600; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; }
QLabel#muted, QLabel#raw { color: %(muted)s; }
QLabel#corrected { color: %(ink)s; }
QLabel#eyebrow { color: %(primary)s; font-size: 12px; font-weight: 600; }
QLabel#status { color: %(primary)s; padding: 8px 0; }
QLabel#empty { color: %(muted)s; font-size: 21px; }
QFrame#segment { background: %(canvas)s; border: none; border-bottom: 1px solid %(soft)s; }
QScrollArea > QWidget > QWidget { background: %(background)s; }
QLabel#muted { font-size: 14px; }
QWidget#transcript { background: %(canvas)s; }
QPushButton, QComboBox { color: %(ink)s; background: %(canvas)s;
 border: 1px solid %(line)s; border-radius: 8px; padding: 8px 12px; min-height: 20px; }
QPushButton#quiet { background: transparent; border: none; color: %(primary)s; }
QPushButton#primary, QPushButton:checked { background: %(primary)s; color: %(canvas)s;
 border-color: %(primary)s; border-radius: 18px; padding: 8px 24px; }
QPushButton:pressed { border-color: %(primary)s; }
QPushButton:disabled, QPushButton#primary:disabled { color: %(muted)s; background: %(soft)s; border-color: %(soft)s; }
QPushButton#quiet:disabled { color: %(muted)s; background: transparent; border: none; }
QComboBox:disabled { color: %(muted)s; background: %(soft)s; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QTextEdit:focus { border-color: %(primary)s; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: %(canvas)s; color: %(ink)s;
 selection-background-color: %(primary)s; selection-color: %(canvas)s; }
QScrollArea { border: none; background: %(canvas)s; border-radius: 18px; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: %(line)s; border-radius: 4px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSlider::groove:horizontal { height: 4px; background: %(line)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(primary)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(canvas)s; border: 1px solid %(line)s;
 width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }
QLineEdit, QTextEdit, QTableWidget, QListWidget { background: %(canvas)s; color: %(ink)s;
 border: 1px solid %(line)s; border-radius: 8px; padding: 8px;
 selection-background-color: %(primary)s; selection-color: %(canvas)s; }
QHeaderView::section { background: %(pearl)s; color: %(secondary)s;
 border: none; border-bottom: 1px solid %(line)s; padding: 8px; }
QMenu { background: %(canvas)s; color: %(ink)s; border: 1px solid %(line)s; padding: 8px; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: %(primary)s; color: %(canvas)s; border-radius: 5px; }
QTabWidget::pane { border: 1px solid %(line)s; background: %(canvas)s; border-radius: 8px; }
QTabBar::tab { padding: 8px 12px; color: %(muted)s; }
QTabBar::tab:selected { color: %(primary)s; background: %(canvas)s; }
QProgressBar { border: none; border-radius: 5px; background: %(soft)s; text-align: center; }
QProgressBar::chunk { background: %(primary)s; border-radius: 5px; }
""" % COLORS


def apply_theme(widget):
    widget.setFont(font(FONT_SIZES['caption']))
    widget.setStyleSheet(APP_STYLE)
