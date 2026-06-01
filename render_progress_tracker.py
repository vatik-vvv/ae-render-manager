"""Thread-safe render progress; main-thread timer polls aerender log files."""
import os
import threading

from render_progress import RenderLogState, progress_ratio_from_line

_lock = threading.Lock()
_jobs = {}
_log_frozen_rows = set()


class _JobProgress:
    __slots__ = ("row", "log_path", "offset", "state", "ratio", "start", "end")

    def __init__(self, row, log_path, start_frame, end_frame):
        self.row = row
        self.log_path = log_path or ""
        self.offset = 0
        self.state = RenderLogState()
        self.ratio = 0.0
        self.start = start_frame
        self.end = end_frame

    def _apply_ratio(self, ratio):
        if ratio is None:
            return
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            return
        ratio = max(0.0, min(1.0, ratio))
        if ratio >= self.ratio:
            self.ratio = ratio

    def ingest_line(self, line):
        if self.row in _log_frozen_rows:
            return
        text = (line or "").strip()
        if not text:
            return
        self.state.update(text)
        ratio = progress_ratio_from_line(
            text, self.start, self.end, log_state=self.state
        )
        self._apply_ratio(ratio)

    def poll_log_file(self):
        if self.row in _log_frozen_rows:
            return
        path = self.log_path
        if not path or not os.path.isfile(path):
            return
        try:
            size = os.path.getsize(path)
            if size < self.offset:
                self.offset = 0
            if size <= self.offset:
                return
            with open(path, encoding="utf-8", errors="ignore") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return
        for raw in chunk.splitlines():
            self.ingest_line(raw)


def register_job(row, log_path, start_frame, end_frame):
    with _lock:
        _jobs[int(row)] = _JobProgress(row, log_path, start_frame, end_frame)


def set_ratio(row, ratio, force=False):
    with _lock:
        job = _jobs.get(int(row))
        if job:
            if force:
                try:
                    job.ratio = max(0.0, min(1.0, float(ratio)))
                except (TypeError, ValueError):
                    pass
            else:
                job._apply_ratio(ratio)


def set_ratio_from_disk(row, existing, total):
    try:
        total = int(total)
        existing = int(existing)
    except (TypeError, ValueError):
        return
    if total <= 0:
        return
    set_ratio(row, min(1.0, max(0.0, existing / float(total))), force=True)


def set_log_progress_frozen(row, frozen=True):
    """Ignore aerender log lines for progress (log often ends before AE finishes)."""
    row = int(row)
    with _lock:
        if frozen:
            _log_frozen_rows.add(row)
        else:
            _log_frozen_rows.discard(row)


def ingest_line(row, line):
    with _lock:
        if int(row) in _log_frozen_rows:
            return
        job = _jobs.get(int(row))
        if job:
            job.ingest_line(line)


def poll_all():
    """Poll log files and return {row: ratio} (call from UI main thread)."""
    with _lock:
        jobs = list(_jobs.values())
        frozen = set(_log_frozen_rows)
    ratios = {}
    for job in jobs:
        if job.row not in frozen:
            job.poll_log_file()
        ratios[job.row] = job.ratio
    return ratios


def is_log_frozen(row):
    with _lock:
        return int(row) in _log_frozen_rows


def swap_rows(row_a, row_b):
    """Move progress state when two queue rows are swapped in the UI."""
    row_a, row_b = int(row_a), int(row_b)
    if row_a == row_b:
        return
    with _lock:
        job_a = _jobs.pop(row_a, None)
        job_b = _jobs.pop(row_b, None)
        if job_a:
            job_a.row = row_b
            _jobs[row_b] = job_a
        if job_b:
            job_b.row = row_a
            _jobs[row_a] = job_b
        frozen_a = row_a in _log_frozen_rows
        frozen_b = row_b in _log_frozen_rows
        _log_frozen_rows.discard(row_a)
        _log_frozen_rows.discard(row_b)
        if frozen_a:
            _log_frozen_rows.add(row_b)
        if frozen_b:
            _log_frozen_rows.add(row_a)


def end_job(row):
    row = int(row)
    with _lock:
        _jobs.pop(row, None)
        _log_frozen_rows.discard(row)


def clear_jobs():
    with _lock:
        _jobs.clear()
        _log_frozen_rows.clear()
