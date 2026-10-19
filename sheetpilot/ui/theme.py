"""Application theme tokens for modern light and dark modes."""

from __future__ import annotations

LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background: #f8fafc;
    color: #0f172a;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#sidebar {
    background: #0f172a;
    border-right: 1px solid #1e293b;
    color: #e2e8f0;
}
QFrame#sidebar QLabel {
    background: transparent;
    color: #94a3b8;
}
QLabel#productName {
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
}
QLabel#workflowStage {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #cbd5e1;
    padding: 10px 12px;
}
QLabel#workflowStage[active="true"] {
    background: #1d4ed8;
    border: 1px solid #3b82f6;
    color: #ffffff;
    font-weight: 600;
}
QFrame#stagePill {
    background: #334155;
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
}
QFrame#stagePill[state="completed"] {
    background: #38bdf8;
}
QFrame#stagePill[state="current"] {
    background: #3b82f6;
}
QPushButton#navButton {
    background: transparent;
    border: 0;
    border-radius: 7px;
    color: #cbd5e1;
    font-weight: 500;
    padding: 9px 12px;
    text-align: left;
}
QPushButton#navButton:hover {
    background: #1e293b;
    color: #ffffff;
}
QPushButton#navButton:checked {
    background: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#navButton:disabled {
    color: #475569;
}
QPushButton#themeToggleButton {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 7px;
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 10px;
}
QPushButton#themeToggleButton:hover {
    background: #334155;
    border-color: #475569;
    color: #ffffff;
}
QLabel#privacyBadge {
    background: #064e3b;
    border: 1px solid #047857;
    border-radius: 8px;
    color: #a7f3d0;
    font-size: 11px;
    font-weight: 600;
    padding: 10px;
}
QLabel#title {
    color: #0f172a;
    font-size: 26px;
    font-weight: 700;
}
QLabel#subtitle {
    color: #475569;
    font-size: 14px;
    padding-bottom: 6px;
}
QLabel#sectionTitle {
    color: #1e293b;
    font-size: 14px;
    font-weight: 600;
}
QLabel#instructionCard, QLabel#resultStatus {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    color: #1e3a8a;
    padding: 10px 12px;
}
QFrame#statusCard, QGroupBox {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    margin-top: 8px;
    padding: 10px;
}
QGroupBox::title {
    color: #0f172a;
    font-weight: 600;
    left: 12px;
    padding: 0 6px;
    subcontrol-origin: margin;
}
QLabel#statusTitle {
    color: #2563eb;
    font-size: 18px;
    font-weight: 700;
}
QLabel#statusBody {
    color: #475569;
    font-size: 13px;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    color: #0f172a;
    font-weight: 600;
    padding: 8px 14px;
}
QPushButton:hover {
    background: #f8fafc;
    border-color: #2563eb;
}
QPushButton:focus {
    border: 2px solid #2563eb;
}
QPushButton:disabled {
    background: #f1f5f9;
    border-color: #e2e8f0;
    color: #94a3b8;
}
QPushButton#primaryButton {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #1d4ed8;
    border-color: #1d4ed8;
}
QPushButton#dangerButton {
    background: #dc2626;
    border-color: #dc2626;
    color: #ffffff;
}
QPushButton#dangerButton:hover {
    background: #b91c1c;
    border-color: #b91c1c;
}
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableView, QTableWidget {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    color: #0f172a;
    padding: 6px 8px;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
}
QListWidget, QTableView, QTableWidget {
    alternate-background-color: #f8fafc;
    gridline-color: #e2e8f0;
}
QHeaderView::section {
    background: #f1f5f9;
    border: 0;
    border-bottom: 1px solid #cbd5e1;
    border-right: 1px solid #e2e8f0;
    color: #334155;
    font-weight: 600;
    padding: 7px 8px;
}
QProgressBar {
    background: #e2e8f0;
    border: 0;
    border-radius: 5px;
    height: 10px;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 5px;
}
QCheckBox {
    spacing: 8px;
}
"""

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background: #0b1120;
    color: #f1f5f9;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#sidebar {
    background: #070b14;
    border-right: 1px solid #1e293b;
    color: #e2e8f0;
}
QFrame#sidebar QLabel {
    background: transparent;
    color: #94a3b8;
}
QLabel#productName {
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
}
QLabel#workflowStage {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 8px;
    color: #cbd5e1;
    padding: 10px 12px;
}
QLabel#workflowStage[active="true"] {
    background: #1d4ed8;
    border: 1px solid #3b82f6;
    color: #ffffff;
    font-weight: 600;
}
QFrame#stagePill {
    background: #1e293b;
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
}
QFrame#stagePill[state="completed"] {
    background: #38bdf8;
}
QFrame#stagePill[state="current"] {
    background: #3b82f6;
}
QPushButton#navButton {
    background: transparent;
    border: 0;
    border-radius: 7px;
    color: #94a3b8;
    font-weight: 500;
    padding: 9px 12px;
    text-align: left;
}
QPushButton#navButton:hover {
    background: #1e293b;
    color: #ffffff;
}
QPushButton#navButton:checked {
    background: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#navButton:disabled {
    color: #475569;
}
QPushButton#themeToggleButton {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 7px;
    color: #f8fafc;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 10px;
}
QPushButton#themeToggleButton:hover {
    background: #334155;
    border-color: #64748b;
}
QLabel#privacyBadge {
    background: #064e3b;
    border: 1px solid #059669;
    border-radius: 8px;
    color: #a7f3d0;
    font-size: 11px;
    font-weight: 600;
    padding: 10px;
}
QLabel#title {
    color: #f8fafc;
    font-size: 26px;
    font-weight: 700;
}
QLabel#subtitle {
    color: #94a3b8;
    font-size: 14px;
    padding-bottom: 6px;
}
QLabel#sectionTitle {
    color: #e2e8f0;
    font-size: 14px;
    font-weight: 600;
}
QLabel#instructionCard, QLabel#resultStatus {
    background: #172554;
    border: 1px solid #1e40af;
    border-radius: 8px;
    color: #dbeafe;
    padding: 10px 12px;
}
QFrame#statusCard, QGroupBox {
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 10px;
    margin-top: 8px;
    padding: 10px;
}
QGroupBox::title {
    color: #f8fafc;
    font-weight: 600;
    left: 12px;
    padding: 0 6px;
    subcontrol-origin: margin;
}
QLabel#statusTitle {
    color: #60a5fa;
    font-size: 18px;
    font-weight: 700;
}
QLabel#statusBody {
    color: #94a3b8;
    font-size: 13px;
}
QPushButton {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 7px;
    color: #f8fafc;
    font-weight: 600;
    padding: 8px 14px;
}
QPushButton:hover {
    background: #334155;
    border-color: #60a5fa;
}
QPushButton:focus {
    border: 2px solid #3b82f6;
}
QPushButton:disabled {
    background: #0f172a;
    border-color: #1e293b;
    color: #475569;
}
QPushButton#primaryButton {
    background: #2563eb;
    border-color: #3b82f6;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #1d4ed8;
}
QPushButton#dangerButton {
    background: #dc2626;
    border-color: #ef4444;
    color: #ffffff;
}
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableView, QTableWidget {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 7px;
    color: #f8fafc;
    padding: 6px 8px;
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
}
QListWidget, QTableView, QTableWidget {
    alternate-background-color: #111827;
    gridline-color: #1e293b;
}
QHeaderView::section {
    background: #1e293b;
    border: 0;
    border-bottom: 1px solid #334155;
    border-right: 1px solid #0f172a;
    color: #cbd5e1;
    font-weight: 600;
    padding: 7px 8px;
}
QProgressBar {
    background: #1e293b;
    border: 0;
    border-radius: 5px;
    height: 10px;
}
QProgressBar::chunk {
    background: #3b82f6;
    border-radius: 5px;
}
QCheckBox {
    spacing: 8px;
}
"""


def get_stylesheet(*, dark_mode: bool = False) -> str:
    """Return the active application stylesheet."""
    return DARK_STYLESHEET if dark_mode else LIGHT_STYLESHEET
