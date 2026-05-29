"""Background scan of AE render queue via AfterFX + ExtendScript."""
from PySide6.QtCore import QThread, Signal

from scan_queue import scan_project


class ScanWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(str, dict)
    error_signal = Signal(str, str)

    def __init__(self, aep_path, parent=None):
        super().__init__(parent)
        self.aep_path = aep_path

    def run(self):
        try:
            data = scan_project(
                self.aep_path,
                log_callback=self.log_signal.emit,
            )
            self.finished_signal.emit(self.aep_path, data)
        except Exception as exc:
            self.error_signal.emit(self.aep_path, str(exc))
