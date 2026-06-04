"""Small Qt widgets for AE Render Manager."""
import os

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ParallelSpinWidget(QWidget):
    """Parallel count: numeric field + visible step buttons (reliable on Windows)."""

    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._spin = QSpinBox()
        self._spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._spin.setAccelerated(True)
        self._spin.setKeyboardTracking(True)
        self._spin.setMinimumWidth(52)
        self._spin.setFixedHeight(32)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor = self._spin.lineEdit()
        if editor is not None:
            editor.installEventFilter(self)

        step_col = QWidget()
        step_col.setFixedWidth(24)
        step_layout = QVBoxLayout(step_col)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(0)
        self._up_btn = QPushButton("▲")
        self._down_btn = QPushButton("▼")
        for btn in (self._up_btn, self._down_btn):
            btn.setFixedSize(24, 16)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._up_btn.clicked.connect(lambda: self._spin.stepBy(1))
        self._down_btn.clicked.connect(lambda: self._spin.stepBy(-1))
        step_layout.addWidget(self._up_btn)
        step_layout.addWidget(self._down_btn)

        from ui_theme import style_parallel_step_buttons

        style_parallel_step_buttons(self._up_btn, self._down_btn)

        layout.addWidget(self._spin)
        layout.addWidget(step_col)
        self._spin.valueChanged.connect(self.valueChanged.emit)

    def setRange(self, minimum, maximum):
        self._spin.setRange(minimum, maximum)

    def setValue(self, value):
        self._spin.setValue(value)

    def value(self):
        return self._spin.value()

    def setToolTip(self, text):
        self._spin.setToolTip(text)
        super().setToolTip(text)

    def eventFilter(self, watched, event):
        if watched is self._spin.lineEdit() and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Up:
                    self._spin.stepBy(1)
                    return True
                if event.key() == Qt.Key.Key_Down:
                    self._spin.stepBy(-1)
                    return True
        return super().eventFilter(watched, event)


class AepDropList(QListWidget):
    """Project list that accepts .aep files dragged from Explorer."""

    def __init__(self, on_aep_paths=None, parent=None):
        super().__init__(parent)
        self._on_aep_paths = on_aep_paths
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = (
                event.position().toPoint()
                if hasattr(event, "position")
                else event.pos()
            )
            if not self.indexAt(pos).isValid():
                self.clearSelection()
                self.setCurrentItem(None)
                event.accept()
                return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._has_aep_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._has_aep_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = self._extract_aep_paths(event)
        if paths and self._on_aep_paths:
            self._on_aep_paths(paths)
        if paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    @staticmethod
    def _has_aep_urls(event):
        mime = event.mimeData()
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(".aep") and os.path.isfile(path):
                return True
        return False

    @staticmethod
    def _extract_aep_paths(event):
        found = []
        seen = set()
        for url in event.mimeData().urls():
            path = os.path.normpath(url.toLocalFile())
            if not path or not path.lower().endswith(".aep"):
                continue
            if not os.path.isfile(path):
                continue
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            found.append(path)
        return found
