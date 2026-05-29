"""Send Telegram preview images at send2bot frame milestones (e.g. every 500)."""
import os
import time

from ae_paths import frame_output_exists
from frame_preview import frame_output_path
from preview_media import is_preview_frame
from send2bot_milestones import send2bot_milestone_frames


def make_deduped_preview_callback(frame_callback):
    """Wrap a preview callback so each frame index is sent to Telegram at most once."""
    sent_frames = set()

    def wrapped(frame, path):
        try:
            frame_id = int(frame)
        except (TypeError, ValueError):
            frame_id = frame
        if frame_id in sent_frames:
            return
        sent_frames.add(frame_id)
        frame_callback(frame, path)

    return wrapped


def _file_stable(path, size_history, stable_polls):
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= 0:
        return False
    norm = os.path.normcase(os.path.abspath(path))
    prev = size_history.get(norm)
    if prev is not None and prev[0] == size:
        stable = prev[1] + 1
    else:
        stable = 0
    size_history[norm] = (size, stable)
    return stable + 1 >= stable_polls


class TelegramMilestonePreviewer:
    """Track and send preview images at frame milestones while rendering."""

    def __init__(
        self,
        output_path,
        start_frame,
        end_frame,
        send2bot,
        on_frame_ready=None,
        stable_polls=2,
        skip_existing_at_start=True,
    ):
        self.output_path = (output_path or "").strip()
        self.start_frame = int(start_frame) if start_frame is not None else 0
        self.end_frame = int(end_frame) if end_frame is not None else 0
        self.send2bot = max(0, int(send2bot or 0))
        self.on_frame_ready = on_frame_ready
        self.stable_polls = max(1, stable_polls)
        self._sent = set()
        self._size_history = {}
        if skip_existing_at_start:
            for frame in send2bot_milestone_frames(
                self.start_frame, self.end_frame, self.send2bot
            ):
                path = frame_output_path(self.output_path, frame)
                if path and frame_output_exists(path):
                    self._sent.add(frame)

    def poll(self):
        if not self.on_frame_ready or self.send2bot <= 0 or not self.output_path:
            return
        for frame in send2bot_milestone_frames(
            self.start_frame, self.end_frame, self.send2bot
        ):
            if frame in self._sent:
                continue
            path = frame_output_path(self.output_path, frame)
            if not path or not is_preview_frame(path):
                continue
            if not frame_output_exists(path):
                continue
            if not _file_stable(path, self._size_history, self.stable_polls):
                continue
            self._sent.add(frame)
            self.on_frame_ready(frame, path)

    def flush(self, timeout=120.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            before = len(self._sent)
            self.poll()
            if len(self._sent) > before:
                time.sleep(0.3)
            else:
                time.sleep(0.5)
        self.poll()
