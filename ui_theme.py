"""Dark theme and table delegates for After Effects Render Manager."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QStyledItemDelegate

RENDER_PROGRESS_ROLE = Qt.UserRole + 100
PROGRESS_FILL_COLOR = QColor(76, 175, 80)

DARK_STYLESHEET = """
QWidget, QMainWindow, QDialog {
    background-color: #353535;
    color: #ffffff;
}
QLineEdit, QTextEdit, QListWidget, QTableWidget, QComboBox {
    background-color: #191919;
    color: #ffffff;
    border: 1px solid #555555;
    padding: 3px;
    selection-background-color: #5a4a72;
}
QSpinBox {
    background-color: #191919;
    color: #ffffff;
    border: 1px solid #555555;
    padding: 4px 6px;
    min-height: 28px;
    selection-background-color: #5a4a72;
}
QPushButton#parallelStepUp, QPushButton#parallelStepDown {
    background-color: #5a5a5a;
    color: #f0f0f0;
    border: 1px solid #777777;
    padding: 0;
    font-size: 10px;
    font-weight: bold;
}
QPushButton#parallelStepUp {
    border-bottom: none;
}
QPushButton#parallelStepDown {
    border-top: none;
}
QPushButton#parallelStepUp:hover, QPushButton#parallelStepDown:hover {
    background-color: #707070;
}
QPushButton#parallelStepUp:pressed, QPushButton#parallelStepDown:pressed {
    background-color: #888888;
}
QLabel#appTitleLabel {
    font-size: 17px;
    font-weight: bold;
    letter-spacing: 2px;
    color: #e8e8e8;
}
QPushButton {
    background-color: #353535;
    color: #ffffff;
    border: 1px solid #555555;
    padding: 6px 10px;
    border-radius: 3px;
}
QPushButton:hover {
    background-color: #454545;
}
QPushButton:pressed {
    background-color: #252525;
}
QGroupBox {
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 3px 0 3px;
}
QLabel {
    color: #ffffff;
}
QHeaderView::section {
    background-color: #353535;
    color: #ffffff;
    padding: 5px;
    border: 1px solid #555555;
}
QListWidget::item:selected, QTableWidget::item:selected {
    background-color: #5a4a72;
    color: #ffffff;
}
QListWidget::item:alternate {
    background-color: #2a2a2a;
}
QSplitter::handle:vertical {
    background: #3a3a3a;
    height: 6px;
}
QSplitter::handle:vertical:hover {
    background: #4a4a4a;
}
QScrollBar:vertical {
    background: #2a2a2a;
    width: 12px;
}
QScrollBar::handle:vertical {
    background: #555555;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


def apply_dark_theme(widget):
    widget.setStyleSheet(DARK_STYLESHEET)


def style_log_panel(text_edit):
    text_edit.setStyleSheet(
        "QTextEdit { background-color: #191919; color: #e0e0e0; "
        "border: 1px solid #555555; font-family: Consolas, monospace; font-size: 11px; }"
    )


def style_parallel_step_buttons(up_btn, down_btn):
    up_btn.setObjectName("parallelStepUp")
    down_btn.setObjectName("parallelStepDown")


def style_muted_label(label):
    label.setStyleSheet("color: #aaaaaa; font-size: 11px;")


class StatusProgressDelegate(QStyledItemDelegate):
    """Status cell: green fill left-to-right while aerender reports progress."""

    def paint(self, painter, option, index):
        progress = index.data(RENDER_PROGRESS_ROLE)
        try:
            progress = max(0.0, min(1.0, float(progress))) if progress is not None else 0.0
        except (TypeError, ValueError):
            progress = 0.0

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        rect = opt.rect
        if opt.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
            painter.fillRect(rect, opt.backgroundBrush)
        else:
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        if progress > 0:
            fill_w = max(0, int(rect.width() * progress))
            painter.fillRect(rect.x(), rect.y(), fill_w, rect.height(), PROGRESS_FILL_COLOR)

        style = opt.widget.style() if opt.widget else QApplication.style()
        opt.textElideMode = Qt.TextElideMode.ElideRight
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)


def apply_zone2_buttons(
    remove_btn, scan_selected_btn, scan_all_btn, import_from_ae_btn=None
):
    """Zone 2: Remove = red when enabled; scan/import buttons = green when enabled."""
    remove_btn.setStyleSheet(
        "QPushButton:enabled { background-color: #c62828; color: #fff; "
        "border: 1px solid #b71c1c; }"
        "QPushButton:disabled { background-color: #555; color: #aaa; "
        "border: 1px solid #555; }"
    )
    scan_style = (
        "QPushButton:enabled { background-color: #4CAF50; color: #fff; "
        "border: 1px solid #3d8b40; }"
        "QPushButton:disabled { background-color: #555; color: #aaa; "
        "border: 1px solid #555; }"
    )
    scan_selected_btn.setStyleSheet(scan_style)
    scan_all_btn.setStyleSheet(scan_style)
    if import_from_ae_btn is not None:
        import_from_ae_btn.setStyleSheet(scan_style)


def apply_action_buttons(start_btn, stop_btn, remove_btn=None):
    start_btn.setStyleSheet(
        "QPushButton:enabled { background-color: #4CAF50; color: #fff; border: 1px solid #3d8b40; }"
        "QPushButton:disabled { background-color: #555; color: #aaa; }"
    )
    stop_btn.setStyleSheet(
        "QPushButton:enabled { background-color: #d32f2f; color: #fff; border: 1px solid #b71c1c; }"
        "QPushButton:disabled { background-color: #555; color: #aaa; }"
    )
    if remove_btn is not None:
        remove_btn.setStyleSheet(
            "QPushButton:enabled { background-color: #c62828; color: #fff; "
            "border: 1px solid #b71c1c; }"
            "QPushButton:disabled { background-color: #555; color: #aaa; "
            "border: 1px solid #555; }"
        )
