"""Application theme tokens and stylesheet."""

LIGHT_STYLESHEET = """
QMainWindow, QWidget { background: #f5f7fb; color: #172033; }
QLabel#title { font-size: 28px; font-weight: 700; color: #172033; }
QLabel#subtitle { font-size: 14px; color: #526078; }
QFrame#statusCard {
    background: white;
    border: 1px solid #dce3ef;
    border-radius: 12px;
}
QLabel#statusTitle { font-size: 18px; font-weight: 600; color: #1d4ed8; }
QLabel#statusBody { font-size: 13px; color: #526078; }
QPushButton {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    padding: 8px 14px;
}
QPushButton:hover { border-color: #2563eb; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
QPushButton#analyseButton { background: #2563eb; color: white; border-color: #2563eb; }
QListWidget, QTableView {
    background: white;
    border: 1px solid #dce3ef;
    border-radius: 8px;
    alternate-background-color: #f8fafc;
}
"""
