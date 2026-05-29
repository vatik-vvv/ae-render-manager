"""Small Qt widgets for AE Render Manager."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QListWidget


class AepDropList(QListWidget):
    """Project list that accepts .aep files dragged from Explorer."""

    def __init__(self, on_aep_paths=None, parent=None):
        super().__init__(parent)
        self._on_aep_paths = on_aep_paths
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

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
