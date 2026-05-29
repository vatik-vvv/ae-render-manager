"""Detect finished render frames on disk and trigger Telegram preview."""
import os
import time

from ae_paths import expand_frame_in_path
from output_cleanup import mark_file_complete, mark_file_seen, track_output_dir
from preview_media import is_preview_frame, is_render_output
from send2bot_milestones import is_send2bot_milestone


def frame_output_path(output_template, frame):
    if not output_template:
        return ""
    return expand_frame_in_path(output_template.strip(), frame)


class FramePreviewWatcher:
    def __init__(
        self,
        output_path,
        start_frame,
        end_frame,
        send2bot,
        on_frame_ready=None,
        skip_existing_frames=True,
        poll_interval=1.0,
        stable_polls=2,
    ):
        self.output_path = output_path
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.send2bot = send2bot
        self.on_frame_ready = on_frame_ready
        self.skip_existing_frames = skip_existing_frames
        self.poll_interval = poll_interval
        self.stable_polls = max(1, stable_polls)
        self._last_poll = 0.0
        self._sent_frames = set()
        self._size_history = {}
        self._baseline = self._snapshot_baseline() if skip_existing_frames else {}
        if output_path:
            track_output_dir(os.path.dirname(os.path.abspath(output_path)))

    def _snapshot_baseline(self):
        baseline = {}
        for frame in range(self.start_frame, self.end_frame + 1):
            path = frame_output_path(self.output_path, frame)
            if not path or not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
                if st.st_size > 0:
                    baseline[path] = (st.st_size, st.st_mtime)
            except OSError:
                pass
        return baseline

    def _is_unchanged_preexisting(self, path, size):
        if not self.skip_existing_frames:
            return False
        base = self._baseline.get(path)
        if base is None:
            return False
        base_size, base_mtime = base
        if size != base_size:
            return False
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return True
        return mtime <= base_mtime + 0.001

    def _should_send(self, frame):
        if self.send2bot <= 0:
            return False
        if self.start_frame is not None and self.end_frame is not None:
            return is_send2bot_milestone(
                frame, self.start_frame, self.end_frame, self.send2bot
            )
        return frame % self.send2bot == 0

    def _frame_ready(self, frame):
        path = frame_output_path(self.output_path, frame)
        if not path or not is_preview_frame(path) or not os.path.isfile(path):
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size <= 0:
            return None
        prev = self._size_history.get(path)
        if prev is not None and prev[0] == size:
            stable_count = prev[1] + 1
        else:
            stable_count = 0
        self._size_history[path] = (size, stable_count)
        if stable_count + 1 >= self.stable_polls:
            return path
        return None

    def poll(self, force=False):
        if not self.output_path:
            return
        now = time.time()
        if not force and now - self._last_poll < self.poll_interval:
            return
        self._last_poll = now
        for frame in range(self.start_frame, self.end_frame + 1):
            path = frame_output_path(self.output_path, frame)
            if not path or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size <= 0:
                if is_render_output(path):
                    mark_file_seen(path)
                continue
            mark_file_seen(path)
            if size > 0 and self._is_unchanged_preexisting(path, size):
                mark_file_complete(path)
                if self._should_send(frame):
                    self._sent_frames.add(frame)
                continue
            ready = self._frame_ready(frame)
            if ready:
                mark_file_complete(ready)
                if (
                    self.on_frame_ready
                    and self.send2bot > 0
                    and frame not in self._sent_frames
                    and self._should_send(frame)
                ):
                    self._sent_frames.add(frame)
                    self.on_frame_ready(frame, ready)

    def flush(self, timeout=120.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            before = len(self._sent_frames)
            self.poll(force=True)
            pending = [
                f
                for f in range(self.start_frame, self.end_frame + 1)
                if self._should_send(f) and f not in self._sent_frames
            ]
            if not pending:
                break
            if len(self._sent_frames) == before:
                time.sleep(self.poll_interval)
            else:
                time.sleep(0.3)
        self.poll(force=True)
