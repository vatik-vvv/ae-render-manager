"""Send Telegram previews by watching image paths mentioned in aerender output."""
import os
import re
import time

from preview_media import is_preview_frame
from output_cleanup import mark_file_complete, mark_file_seen, track_output_dir
from send2bot_milestones import is_send2bot_milestone

IMAGE_PATH_RE = re.compile(
    r'([A-Za-z]:[\\/][^\s<>:"|?*\n\r]+?\.(?:png|jpg|jpeg|exr|tif|tiff|bmp|webp))',
    re.IGNORECASE,
)
FRAME_IN_NAME_RE = re.compile(r"(\d+)(?=\.[^.]+$)")
OUTPUT_TO_RE = re.compile(
    r"(?:output\s+to|saving|saved|writing|wrote|finished)[^:\n]*:\s*[\"']?"
    r"([A-Za-z]:[\\/][^\s\"'<>|]+?\.(?:png|jpg|jpeg|exr|tif|tiff|bmp|webp))",
    re.IGNORECASE,
)
QUOTED_PATH_RE = re.compile(
    r'"([A-Za-z]:[\\/][^"]+?\.(?:png|jpg|jpeg|exr|tif|tiff|bmp|webp))"',
    re.IGNORECASE,
)


def parse_frame_index_from_path(path):
    base = os.path.basename(path)
    match = FRAME_IN_NAME_RE.search(base)
    if match:
        return int(match.group(1))
    return None


class AerenderLogPreviewWatcher:
    """
    On each aerender log line, detect finished frame files and fire callback
  every send2bot-th frame (by frame index in filename, or every Nth file).
    """

    def __init__(
        self,
        send2bot,
        on_frame_ready=None,
        skip_existing_at_start=True,
        stable_polls=2,
        poll_interval=0.5,
        start_frame=None,
        end_frame=None,
    ):
        self.send2bot = max(0, int(send2bot or 0))
        self.on_frame_ready = on_frame_ready
        self.skip_existing_at_start = skip_existing_at_start
        self.stable_polls = max(1, stable_polls)
        self.poll_interval = poll_interval
        self.start_frame = start_frame
        self.end_frame = end_frame
        self._sent_paths = set()
        self._size_history = {}
        self._baseline = set()
        self._file_event_count = 0
        self._started = time.monotonic()
        self._log_path = None
        self._log_offset = 0
        self._pending_paths = set()
        self._output_dirs = set()

    def _note_baseline(self, path):
        if path and os.path.isfile(path):
            self._baseline.add(os.path.normcase(os.path.abspath(path)))

    def _should_send_for_frame(self, frame_index, file_event_index):
        n = self.send2bot
        if n <= 0:
            return False
        if frame_index is not None:
            if self.start_frame is not None and self.end_frame is not None:
                return is_send2bot_milestone(
                    frame_index, self.start_frame, self.end_frame, n
                )
            return frame_index % n == 0
        return file_event_index % n == 0

    def _is_stable(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size <= 0:
            return False
        norm = os.path.normcase(os.path.abspath(path))
        if self.skip_existing_at_start and norm in self._baseline:
            return False
        prev = self._size_history.get(norm)
        if prev is not None and prev[0] == size:
            stable = prev[1] + 1
        else:
            stable = 0
        self._size_history[norm] = (size, stable)
        return stable + 1 >= self.stable_polls

    def _register_path(self, path):
        path = path.strip().strip('"').strip("'")
        if not path or not is_preview_frame(path):
            return
        norm = os.path.normcase(os.path.abspath(path))
        self._pending_paths.add(norm)
        parent = os.path.dirname(path)
        if parent:
            self._output_dirs.add(os.path.normcase(parent))
            track_output_dir(parent)
        mark_file_seen(path)

    def _note_write_finished(self, path):
        path = path.strip().strip('"').strip("'")
        if not path or not is_preview_frame(path):
            return
        if not os.path.isfile(path):
            return
        if self._is_stable(path):
            mark_file_complete(path)

    def _try_send_path(self, path, frame_hint=None):
        if not self.on_frame_ready or self.send2bot <= 0:
            return
        path = path.strip().strip('"').strip("'")
        if not path or not is_preview_frame(path):
            return
        norm = os.path.normcase(os.path.abspath(path))
        if norm in self._sent_paths:
            self._pending_paths.discard(norm)
            return
        if not os.path.isfile(path):
            return
        if not self._is_stable(path):
            self._pending_paths.add(norm)
            mark_file_seen(path)
            return
        mark_file_complete(path)
        frame_index = frame_hint if frame_hint is not None else parse_frame_index_from_path(path)
        self._file_event_count += 1
        if not self._should_send_for_frame(frame_index, self._file_event_count):
            self._pending_paths.add(norm)
            return
        self._sent_paths.add(norm)
        self._pending_paths.discard(norm)
        frame_num = frame_index if frame_index is not None else self._file_event_count
        self.on_frame_ready(frame_num, path)

    def _extract_paths(self, line):
        for pattern in (IMAGE_PATH_RE, OUTPUT_TO_RE, QUOTED_PATH_RE):
            for match in pattern.finditer(line):
                yield match.group(1)

    def on_log_line(self, line):
        if not line:
            return
        for path in self._extract_paths(line):
            self._register_path(path)
            self._note_write_finished(path)
            if self.send2bot > 0:
                self._try_send_path(path)

    def on_progress_frame(self, frame_num, line=None):
        if self.send2bot <= 0 or frame_num is None:
            return
        if line:
            self.on_log_line(line)
        if not self._should_send_for_frame(int(frame_num), 0):
            return
        for norm in list(self._pending_paths):
            self._note_write_finished(norm)
            if self.send2bot > 0:
                self._try_send_path(norm, frame_hint=int(frame_num))
        for out_dir in list(self._output_dirs):
            if not os.path.isdir(out_dir):
                continue
            try:
                entries = [
                    os.path.join(out_dir, name)
                    for name in os.listdir(out_dir)
                    if is_preview_frame(os.path.join(out_dir, name))
                ]
            except OSError:
                continue
            if not entries:
                continue
            newest = max(entries, key=lambda p: os.path.getmtime(p))
            self._try_send_path(newest, frame_hint=int(frame_num))

    def set_log_file(self, log_path):
        self._log_path = log_path
        self._log_offset = 0

    def on_start(self):
        if not self.skip_existing_at_start:
            return
        for norm_path in list(self._size_history.keys()):
            self._baseline.add(norm_path)

    def poll_log_file(self, on_line=None):
        if not self._log_path or not os.path.isfile(self._log_path):
            return
        try:
            size = os.path.getsize(self._log_path)
            if size < self._log_offset:
                self._log_offset = 0
            if size == self._log_offset:
                self._poll_pending_files()
                return
            with open(self._log_path, encoding="utf-8", errors="ignore") as handle:
                handle.seek(self._log_offset)
                chunk = handle.read()
                self._log_offset = size
            for line in chunk.splitlines():
                self.on_log_line(line)
                if on_line:
                    on_line(line)
            self._poll_pending_files()
        except OSError:
            pass

    def _poll_pending_files(self):
        for norm in list(self._pending_paths):
            self._note_write_finished(norm)

    def poll(self, force=False, on_line=None):
        self.poll_log_file(on_line=on_line)

    def flush(self, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.poll_log_file()
            time.sleep(self.poll_interval)
