"""Parallel render queue worker for PySide6 UI."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from ae_render_settings import resolve_frame_range
from ae_paths import get_max_parallel
from output_cleanup import begin_queue_session
from render_progress_tracker import set_ratio
from render_runner import (
    finalize_queue_render,
    is_stop_requested,
    reset_stop_flag,
    run_render,
    stop_render,
)
from telegram_notifier import send_image, send_message


class ParallelRenderWorker(QThread):
    log_signal = Signal(str)
    update_row_signal = Signal(int, str, str, str)
    progress_signal = Signal(int, int, int)
    frame_progress_signal = Signal(int, float)
    disk_progress_signal = Signal(int, int, int)
    queue_count_signal = Signal(int, int)
    finished_signal = Signal(bool)

    def __init__(self, queue_bridge, initial_jobs=None, max_parallel=None, parent=None):
        super().__init__(parent)
        self._bridge = queue_bridge
        self._initial_jobs = list(initial_jobs or [])
        self.max_parallel = max_parallel or get_max_parallel()
        self._executor = None
        self._last_batch_size = 0
        self._row_ui_map = {}
        self._row_map_lock = threading.Lock()
        self._in_flight_rows = set()

    def remap_rows(self, row_a, row_b):
        """Keep running job progress/status on the correct UI row after a swap."""
        row_a, row_b = int(row_a), int(row_b)
        if row_a == row_b:
            return
        with self._row_map_lock:
            for worker_row in list(self._row_ui_map):
                ui_row = self._row_ui_map[worker_row]
                if ui_row == row_a:
                    self._row_ui_map[worker_row] = row_b
                elif ui_row == row_b:
                    self._row_ui_map[worker_row] = row_a
            for worker_row in (row_a, row_b):
                if worker_row not in self._row_ui_map:
                    self._row_ui_map[worker_row] = row_b if worker_row == row_a else row_a
                elif self._row_ui_map[worker_row] == worker_row:
                    self._row_ui_map[worker_row] = row_b if worker_row == row_a else row_a
        if row_a in self._in_flight_rows:
            self._in_flight_rows.discard(row_a)
            self._in_flight_rows.add(row_b)
        elif row_b in self._in_flight_rows:
            self._in_flight_rows.discard(row_b)
            self._in_flight_rows.add(row_a)

    def _ui_row(self, worker_row):
        with self._row_map_lock:
            return self._row_ui_map.get(int(worker_row), int(worker_row))

    def _fetch_pending_jobs(self):
        jobs = []
        if self._bridge is not None:
            jobs = self._bridge.get_pending_jobs()
        if not jobs and self._initial_jobs:
            jobs = list(self._initial_jobs)
            self.log_signal.emit(f"Queue snapshot: using {len(jobs)} job(s) from start")
        elif jobs:
            self._initial_jobs = []
        return jobs

    def _all_enabled_finished(self):
        if self._bridge is not None:
            return bool(self._bridge.is_all_finished())
        return True

    def _next_runnable(self, in_flight_rows):
        jobs = self._fetch_pending_jobs()
        batch_total = len(jobs)
        if batch_total > self._last_batch_size:
            self.log_signal.emit(
                f"Queue updated: {batch_total} pending job(s) "
                f"(+{batch_total - self._last_batch_size} new)"
            )
        self._last_batch_size = batch_total
        runnable = []
        for idx, item in enumerate(jobs, start=1):
            row = item["row"]
            if row in in_flight_rows:
                continue
            copy = dict(item)
            copy["job_index"] = idx
            copy["_batch_total"] = batch_total
            runnable.append(copy)
        self.queue_count_signal.emit(len(runnable), batch_total)
        return runnable, batch_total

    def _run_one(self, item, total):
        row = item["row"]
        idx = item.get("job_index", 1)
        total = item.get("_batch_total", total)
        prefix = f"Job {idx}/{total}"
        if is_stop_requested():
            return row, "Stopped", None

        self.progress_signal.emit(idx, total, self._ui_row(row))

        aep = item["project"]
        rq_index = item.get("rq_index")
        full_queue = item.get("full_queue", False)
        skip = item.get("skip_val") in ("1", 1, True)
        output_path = item.get("output_path", "")
        start_frame, end_frame = resolve_frame_range(
            item.get("start_frame"), item.get("end_frame")
        )
        increment = max(1, int(item.get("increment") or 1))

        if (
            not full_queue
            and skip
            and output_path
            and start_frame is not None
            and end_frame is not None
        ):
            from ae_paths import count_existing_frames

            existing, frame_total = count_existing_frames(
                output_path, start_frame, end_frame, increment
            )
            if frame_total > 0 and existing == frame_total:
                self.log_signal.emit(
                    f"{prefix}: skipped ({frame_total} frames exist) — {output_path}"
                )
                return row, "Skipped", None

        if is_stop_requested():
            return row, "Stopped", None

        start_time = datetime.now().strftime("%H:%M:%S")
        self.update_row_signal.emit(self._ui_row(row), "Running", start_time, "")

        send2bot = max(0, int(item.get("send2bot", 0) or 0))
        frame_cb = None
        if send2bot > 0:

            def frame_cb(frame, path, it=item):
                caption = (
                    f"Frame {frame} — {it.get('comp') or os.path.basename(it.get('project', ''))} — "
                    f"{os.path.basename(path)}"
                )
                ok, err = send_image(path, caption=caption)
                if ok:
                    self.log_signal.emit(
                        f"Telegram preview: frame {frame} — {os.path.basename(path)}"
                    )
                elif not ok:
                    self.log_signal.emit(f"Telegram preview failed: {err}")
                    text = (
                        f"Frame {frame} — {it.get('comp') or os.path.basename(it.get('project', ''))}"
                    )
                    ok2, err2 = send_message(text)
                    if not ok2:
                        self.log_signal.emit(f"Telegram text failed: {err2}")

        def progress_cb(ratio, existing=-1, total=-1, r=row):
            if is_stop_requested():
                return
            ui = self._ui_row(r)
            if total > 0 and existing >= 0:
                set_ratio(ui, existing / float(total), force=True)
                self.disk_progress_signal.emit(ui, existing, total)
            else:
                set_ratio(ui, ratio)
            self.frame_progress_signal.emit(ui, float(ratio))

        ret = run_render(
            project=item["project"],
            rq_index=item.get("rq_index"),
            comp=item.get("comp", ""),
            start_frame=start_frame,
            end_frame=end_frame,
            increment=increment,
            output_path=output_path,
            rs_template=item.get("rs_template", ""),
            rs_template_scanned=item.get("rs_template_scanned", ""),
            om_template_scanned=item.get("om_template_scanned", ""),
            log_callback=self.log_signal.emit,
            send2bot=send2bot,
            frame_callback=frame_cb,
            progress_callback=progress_cb,
            progress_row=row,
            skip_existing_frames=skip,
            job_label=prefix,
            full_queue=full_queue,
            use_proxy=item.get("use_proxy") in ("1", 1, True),
        )

        if is_stop_requested():
            return row, "Stopped", None
        if ret == 0:
            return row, "Completed", None
        if ret == 2:
            return row, "Incomplete", None
        if ret == -1 and is_stop_requested():
            return row, "Stopped", None
        return row, "Failed", None

    def _finish_job(self, item, status):
        row = self._ui_row(item["row"])
        end_time = datetime.now().strftime("%H:%M:%S")
        self.update_row_signal.emit(row, status, "", end_time)

    def run(self):
        reset_stop_flag()
        begin_queue_session()
        self._last_batch_size = 0
        self._row_ui_map = {}
        self._in_flight_rows = set()
        interrupted = False

        def run_serial():
            nonlocal interrupted
            while True:
                if self.isInterruptionRequested() or is_stop_requested():
                    interrupted = True
                    stop_render(log_callback=self.log_signal.emit)
                    break
                runnable, _batch_total = self._next_runnable(set())
                if not runnable and self._last_batch_size == 0:
                    self.log_signal.emit(
                        "Warning: worker sees 0 jobs in queue snapshot "
                        "(UI may be out of sync — try Stop and Start again)"
                    )
                if not runnable:
                    if self._all_enabled_finished():
                        break
                    if is_stop_requested():
                        interrupted = True
                        break
                    time.sleep(0.5)
                    continue
                for item in runnable:
                    if self.isInterruptionRequested() or is_stop_requested():
                        interrupted = True
                        stop_render(log_callback=self.log_signal.emit)
                        return
                    total = item.get("_batch_total", 1)
                    try:
                        row, status, _extra = self._run_one(item, total)
                    except Exception as e:
                        status = "Stopped" if is_stop_requested() else "Failed"
                        self.log_signal.emit(f"Error: {e}")
                    self._finish_job(item, status)
                    if is_stop_requested():
                        interrupted = True
                        stop_render(log_callback=self.log_signal.emit)
                        return
                    time.sleep(1.0)

        def run_parallel():
            nonlocal interrupted
            in_flight_rows = self._in_flight_rows
            futures = {}
            executor = ThreadPoolExecutor(max_workers=self.max_parallel)
            self._executor = executor
            try:
                while True:
                    if self.isInterruptionRequested() or is_stop_requested():
                        interrupted = True
                        stop_render(log_callback=self.log_signal.emit)
                        for fut in list(futures.keys()):
                            fut.cancel()
                        break
                    done = [f for f in futures if f.done()]
                    for future in done:
                        item = futures.pop(future)
                        row = item["row"]
                        in_flight_rows.discard(row)
                        try:
                            row, status, _extra = future.result()
                        except Exception as e:
                            status = "Stopped" if is_stop_requested() else "Failed"
                            self.log_signal.emit(f"Error: {e}")
                        self._finish_job(item, status)
                    slots = self.max_parallel - len(futures)
                    if slots > 0:
                        runnable, _batch_total = self._next_runnable(in_flight_rows)
                        for item in runnable[:slots]:
                            if is_stop_requested():
                                interrupted = True
                                break
                            total = item.get("_batch_total", 1)
                            fut = executor.submit(self._run_one, item, total)
                            futures[fut] = item
                            in_flight_rows.add(item["row"])
                            label = item.get("comp") or os.path.basename(
                                item.get("project", "")
                            )
                            self.log_signal.emit(
                                f"Started row {item['row'] + 1}: {label}"
                            )
                    if futures:
                        time.sleep(0.2)
                        continue
                    if self._all_enabled_finished():
                        break
                    if is_stop_requested():
                        interrupted = True
                        break
                    time.sleep(0.5)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

        if self.max_parallel <= 1:
            run_serial()
        else:
            run_parallel()

        was_stopped = interrupted or is_stop_requested()
        if was_stopped:
            stop_render(log_callback=self.log_signal.emit)
        else:
            finalize_queue_render(log_callback=self.log_signal.emit)
        self.finished_signal.emit(was_stopped)
        reset_stop_flag()
