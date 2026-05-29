"""Thread-safe queue snapshot for the render worker (no GUI access from worker thread)."""


class QueueJobBridge:
    """Worker reads snapshots built on the UI thread."""

    def __init__(self, main_window):
        self._window = main_window

    def get_pending_jobs(self):
        return self._window.get_queue_snapshot()

    def is_all_finished(self):
        return self._window.get_queue_all_finished()
