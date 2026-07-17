"""Application theme tokens kept separate for a future dark theme."""

LIGHT_STYLESHEET = """
QMainWindow, QWidget { background: #f5f7fb; color: #172033; font-size: 13px; }
QFrame#sidebar { background: #111c36; color: #dbe7ff; }
QFrame#sidebar QLabel { background: transparent; color: #b8c5de; }
QLabel#productName { color: white; font-size: 23px; font-weight: 700; }
QLabel#workflowStage { padding: 11px 9px; border-radius: 7px; color: #9fb0cf; }
QLabel#workflowStage[active="true"] { background: #233764; color: white; font-weight: 600; }
QLabel#privacyBadge {
    padding: 10px; border: 1px solid #315486; border-radius: 8px; color: #a7d5c0;
}
QLabel#title { font-size: 26px; font-weight: 700; color: #172033; }
QLabel#subtitle { font-size: 14px; color: #526078; padding-bottom: 5px; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; color: #273652; }
QLabel#instructionCard, QLabel#resultStatus {
    background: #eef5ff; border: 1px solid #cbdcf8; border-radius: 8px; padding: 9px;
}
QFrame#statusCard, QGroupBox {
    background: white; border: 1px solid #dce3ef; border-radius: 10px; margin-top: 7px;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 600; }
QLabel#statusTitle { font-size: 18px; font-weight: 600; color: #1d4ed8; }
QLabel#statusBody { font-size: 13px; color: #526078; }
QPushButton {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 7px; padding: 8px 14px;
}
QPushButton:hover { border-color: #2563eb; }
QPushButton:focus { border: 2px solid #2563eb; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
QPushButton#primaryButton { background: #2563eb; color: white; border-color: #2563eb; }
QPushButton#dangerButton { background: #b42318; color: white; border-color: #b42318; }
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableView, QTableWidget {
    background: white; border: 1px solid #d5deeb; border-radius: 7px; padding: 5px;
    selection-background-color: #dce9ff; selection-color: #172033;
}
QListWidget, QTableView, QTableWidget { alternate-background-color: #f8fafc; }
QProgressBar { background: #e8edf5; border: 0; border-radius: 5px; height: 10px; }
QProgressBar::chunk { background: #2563eb; border-radius: 5px; }
QCheckBox { spacing: 7px; }
"""
