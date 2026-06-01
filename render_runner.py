import logging
import os
import queue
import subprocess
import threading
import time
import uuid

from aerender_log_preview import AerenderLogPreviewWatcher
from ae_render_settings import (
    USE_QUEUE_RS,
    is_use_queue_rs,
    resolve_frame_range,
    resolve_frame_value,
    resolve_om_template,
    resolve_rs_template,
)
from aerender_log import (
    describe_aerender_early_exit,
    format_render_failure_hints,
    parse_aerender_log_summary,
)
from ae_paths import (
    all_frames_exist,
    count_existing_frames,
    ensure_output_directory,
    first_missing_frame,
    get_aerender_path,
    wait_for_render_output_idle,
)
from app_paths import logs_dir
from frame_preview import FramePreviewWatcher, frame_output_path
from process_tree import (
    collect_ae_child_pids,
    kill_process_tree,
    pid_is_alive,
)
from output_cleanup import (
    begin_render_session,
    cleanup_incomplete_outputs,
    mark_job_output_complete,
    register_job_output,
    track_output_dir,
)
from render_progress import (
    RenderLogState,
    apply_progress_update,
    parse_aerender_progress_frame,
    parse_frame_from_line,
)
from render_progress_tracker import (
    end_job,
    ingest_line,
    register_job,
    set_log_progress_frozen,
    set_ratio,
    set_ratio_from_disk,
)
from telegram_notifier import send_message
from telegram_previews import TelegramMilestonePreviewer, make_deduped_preview_callback
from telegram_report import format_job_telegram, status_from_render_result

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_active_lock = threading.Lock()
_active_sessions = []
_stop_requested = False

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _RenderSession:
    __slots__ = ("proc", "project", "root_pid", "child_pids")

    def __init__(self, proc, project):
        self.proc = proc
        self.project = project
        self.root_pid = proc.pid
        self.child_pids = set()


def _register_session(session):
    with _active_lock:
        _active_sessions.append(session)


def _unregister_session(session):
    with _active_lock:
        if session in _active_sessions:
            _active_sessions.remove(session)


def _refresh_session_children(session):
    if session is None:
        return
    session.child_pids |= collect_ae_child_pids(session.root_pid)


def _kill_session(session, force=True):
    if session is None:
        return
    _refresh_session_children(session)
    if session.proc.poll() is None:
        try:
            session.proc.terminate()
        except Exception:
            pass
        try:
            session.proc.kill()
        except Exception:
            pass
    all_pids = {session.root_pid} | set(session.child_pids)
    for pid in all_pids:
        if pid:
            kill_process_tree(pid, force=force)


def _sessions_still_running():
    with _active_lock:
        sessions = list(_active_sessions)
    for session in sessions:
        if session.proc.poll() is None:
            return True
        _refresh_session_children(session)
    return False


def _session_tree_busy(session):
    """True while this job's aerender or its After Effects child processes are alive."""
    if session is None:
        return False
    if session.proc.poll() is None:
        return True
    _refresh_session_children(session)
    for pid in {session.root_pid} | set(session.child_pids):
        if pid_is_alive(pid):
            return True
    return False


def _notify_telegram(message, log_callback=None):
    ok, err = send_message(message)
    if not ok and log_callback and err:
        log_callback(f"Telegram warning: {err}")


def _format_elapsed(seconds):
    total = int(max(0, round(seconds)))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _is_full_queue_job(job):
    if job.get("full_queue"):
        return True
    rq_index = job.get("rq_index")
    comp = (job.get("comp") or "").strip()
    return not rq_index and not comp


def build_aerender_cmd(job, log_path=None, segment_start=None, segment_end=None):
    aerender = get_aerender_path()
    cmd = [
        aerender,
        "-project",
        os.path.normpath(job["project"]),
        "-v",
        "ERRORS_AND_PROGRESS",
    ]
    if log_path:
        cmd.extend(["-log", log_path])
    if _is_full_queue_job(job):
        return cmd
    rq_index = job.get("rq_index")
    comp = (job.get("comp") or "").strip()
    if rq_index:
        cmd.extend(["-rqindex", str(int(rq_index))])
    elif comp:
        cmd.extend(["-comp", comp])
    start = resolve_frame_value(job.get("start_frame"))
    end = resolve_frame_value(job.get("end_frame"))
    # Resume passes and scanned Start/End columns always pass -s/-e (even with Use queue).
    if segment_start is not None:
        cmd.extend(["-s", str(int(segment_start))])
    elif start is not None:
        cmd.extend(["-s", str(int(start))])
    if segment_end is not None:
        cmd.extend(["-e", str(int(segment_end))])
    elif end is not None:
        cmd.extend(["-e", str(int(end))])
    inc = job.get("increment")
    if inc and int(inc) != 1:
        cmd.extend(["-i", str(int(inc))])
    output_path = (job.get("output_path") or "").strip()
    if output_path:
        cmd.extend(["-output", output_path])
    rs = resolve_rs_template(job)
    if rs:
        cmd.extend(["-RStemplate", rs])
    om = resolve_om_template(job)
    if om:
        cmd.extend(["-OMtemplate", om])
    return cmd


def _check_output_frame_count(job, log_callback=None, prefix=""):
    """Return (existing, total) after render, or (None, None) if unknown."""
    from ae_render_settings import resolve_frame_range

    if _is_full_queue_job(job):
        return None, None
    output_path = (job.get("output_path") or "").strip()
    if not output_path:
        return None, None
    start, end = resolve_frame_range(job.get("start_frame"), job.get("end_frame"))
    if start is None or end is None:
        return None, None
    increment = max(1, int(job.get("increment") or 1))
    existing, total = count_existing_frames(output_path, start, end, increment)
    if log_callback and total > 0 and existing < total:
        log_callback(
            f"{prefix}Incomplete output: {existing}/{total} frame(s) on disk — "
            f"{os.path.basename(output_path)}"
        )
    return existing, total


def is_stop_requested():
    return _stop_requested


def kill_active_renders(log_callback=None):
    """End aerender processes started by this app (tracked sessions only)."""
    with _active_lock:
        sessions = list(_active_sessions)
    killed = 0
    for session in sessions:
        _kill_session(session, force=True)
        killed += 1
    with _active_lock:
        _active_sessions.clear()
    if log_callback:
        if killed:
            log_callback(
                f"Stop: ended {killed} render process tree(s) started by this app."
            )
        else:
            log_callback("Stop: no active render sessions tracked by this app.")
    return bool(sessions)


def stop_render(log_callback=None):
    """User stop: kill processes and remove incomplete outputs from this session."""
    global _stop_requested
    _stop_requested = True
    had_sessions = kill_active_renders(log_callback=log_callback)
    cleanup_incomplete_outputs(log_callback=log_callback, aggressive=True)
    return had_sessions


def _wait_sessions_idle(log_callback=None, max_wait=180):
    """Wait only for aerender trees this app started — not other After Effects windows."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline and _sessions_still_running():
        if log_callback:
            log_callback(
                "Waiting for this app's aerender process(es) to exit before next step…"
            )
        time.sleep(2.0)


def finalize_queue_render(log_callback=None):
    """After a successful queue run: wait for our aerender trees, then clear session list."""
    _wait_sessions_idle(log_callback=log_callback)
    kill_active_renders(log_callback=log_callback)


def reset_stop_flag():
    global _stop_requested
    _stop_requested = False


def run_render(
    project,
    rq_index=None,
    comp="",
    start_frame=0,
    end_frame=100,
    increment=1,
    output_path="",
    rs_template="",
    rs_template_scanned="",
    log_callback=None,
    send2bot=0,
    frame_callback=None,
    progress_callback=None,
    progress_row=None,
    skip_existing_frames=False,
    job_label="",
    full_queue=True,
    use_proxy=False,
    om_template_scanned="",
):
    global _stop_requested

    job = {
        "project": project,
        "rq_index": rq_index,
        "comp": comp,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "increment": increment,
        "output_path": output_path,
        "rs_template": rs_template,
        "skip_existing": skip_existing_frames,
        "skip_val": "1" if skip_existing_frames else "0",
        "full_queue": full_queue,
        "use_proxy": use_proxy,
        "rs_template_scanned": rs_template_scanned or rs_template,
        "om_template_scanned": om_template_scanned or "",
        "increment": increment,
    }

    prefix = f"[{job_label}] " if job_label else ""
    use_full_queue = _is_full_queue_job(job)
    send2bot = max(0, int(send2bot or 0))
    resolved_start, resolved_end = resolve_frame_range(start_frame, end_frame)
    if not use_full_queue:
        job["start_frame"] = resolved_start
        job["end_frame"] = resolved_end
        if log_callback and resolved_start is None and resolved_end is None:
            log_callback(
                f"{prefix}Frame range from AE render queue "
                "(fill Start/End columns to override)"
            )

    if use_full_queue and log_callback:
        log_callback(
            f"{prefix}Rendering entire AE render queue (checked items, saved settings in .aep)"
        )

    if not use_full_queue and skip_existing_frames:
        if log_callback:
            if is_use_queue_rs(rs_template):
                log_callback(
                    f"{prefix}Skip + render settings from AE queue "
                    f"({USE_QUEUE_RS}; enable Skip Existing Files / Use Proxies in AE)"
                )
            else:
                log_callback(
                    f"{prefix}Skip with RS template «{rs_template}» "
                    "(template must have Skip Existing Files enabled in AE)"
                )
        if output_path and resolved_start is not None and resolved_end is not None:
            existing, total = count_existing_frames(
                output_path, resolved_start, resolved_end, increment
            )
            if total > 0 and existing == total:
                if log_callback:
                    log_callback(
                        f"{prefix}Skip: all {total} frame(s) already on disk — {output_path}"
                    )
                skip_msg = format_job_telegram(
                    job, "finish", status="Skipped", duration_sec=0
                )
                _notify_telegram(skip_msg, log_callback)
                return 0
            if log_callback and total > 0 and existing > 0:
                missing = first_missing_frame(
                    output_path, resolved_start, resolved_end, increment
                )
                log_callback(
                    f"{prefix}Partial skip: {existing}/{total} frame(s) exist; "
                    f"will render from frame {missing} to {resolved_end}"
                )

    preview_watcher = None
    tg_previewer = None
    telegram_preview_cb = (
        make_deduped_preview_callback(frame_callback)
        if send2bot > 0 and frame_callback
        else None
    )
    log_preview = AerenderLogPreviewWatcher(
        send2bot,
        on_frame_ready=telegram_preview_cb,
        skip_existing_at_start=False,
        stable_polls=2,
        start_frame=None if use_full_queue else resolved_start,
        end_frame=None if use_full_queue else resolved_end,
    )
    if log_callback and send2bot > 0:
        if resolved_start is not None and not use_full_queue:
            first_milestone = int(resolved_start) + max(1, int(send2bot))
            log_callback(
                f"{prefix}Telegram every {send2bot} frame(s) "
                f"(from frame {first_milestone}, range {resolved_start}–{resolved_end})"
            )
        else:
            log_callback(f"{prefix}Telegram every {send2bot} frame(s)")
    if (
        not use_full_queue
        and output_path
        and resolved_start is not None
        and resolved_end is not None
    ):
        # Disk tracking only — Telegram milestones use tg_previewer (not FramePreviewWatcher).
        preview_watcher = FramePreviewWatcher(
            output_path,
            resolved_start,
            resolved_end,
            send2bot,
            on_frame_ready=None,
            skip_existing_frames=skip_existing_frames,
        )
        if telegram_preview_cb:
            tg_previewer = TelegramMilestonePreviewer(
                output_path,
                resolved_start,
                resolved_end,
                send2bot,
                on_frame_ready=telegram_preview_cb,
                skip_existing_at_start=skip_existing_frames,
            )

    def _poll_telegram_previews():
        if preview_watcher:
            preview_watcher.poll(force=True)
        if tg_previewer:
            tg_previewer.poll()
        if log_preview and log_preview.send2bot > 0:
            log_preview.poll_log_file()

    proc = None
    session = None
    stopped = False
    try:
        if is_stop_requested():
            return -1

        begin_render_session()

        if output_path:
            ensure_output_directory(output_path)
            register_job_output(
                output_path,
                resolved_start,
                resolved_end,
                increment,
            )

        render_started_at = time.monotonic()
        start_msg = format_job_telegram(job, "start", status="Starting")
        if log_callback:
            log_callback(prefix + start_msg.replace("\n", " | "))
        _notify_telegram(start_msg, log_callback)

        log_name = f"render_{uuid.uuid4().hex[:8]}.log"
        log_path = os.path.join(logs_dir(), log_name)
        if progress_row is not None:
            prog_start = 0 if use_full_queue else (resolved_start if resolved_start is not None else 0)
            prog_end = 999999 if use_full_queue else (
                resolved_end if resolved_end is not None else 999999
            )
            register_job(progress_row, log_path, prog_start, prog_end)

        def _emit_progress(
            ratio,
            allow_complete=False,
            force=False,
            disk_existing=-1,
            disk_total=-1,
        ):
            try:
                ratio = float(ratio)
            except (TypeError, ValueError):
                return
            if not allow_complete:
                ratio = min(0.99, max(0.0, ratio))
            else:
                ratio = min(1.0, max(0.0, ratio))
            if progress_row is not None:
                set_ratio(progress_row, ratio, force=force)
            if progress_callback:
                try:
                    progress_callback(ratio, disk_existing, disk_total)
                except TypeError:
                    progress_callback(ratio)

        _emit_progress(0.0)

        MAX_AERENDER_PASSES = 100
        STALL_WITHOUT_NEW_FRAMES = 2
        frame_start, frame_end = resolved_start, resolved_end
        can_verify_frames = (
            not use_full_queue
            and output_path
            and frame_start is not None
            and frame_end is not None
        )
        max_passes = MAX_AERENDER_PASSES if can_verify_frames else 1
        pass_num = 0
        retcode = 0
        existing, total = None, None
        last_existing_on_disk = -1
        stall_passes = 0

        popen_kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1}
        if os.name == "nt":
            popen_kw["creationflags"] = CREATE_NO_WINDOW

        prog_start = 0 if use_full_queue else (resolved_start if resolved_start is not None else 0)
        prog_end = 999999 if use_full_queue else (
            resolved_end if resolved_end is not None else 999999
        )
        last_ratio = 0.0
        log_state = RenderLogState()

        while pass_num < max_passes:
            pass_num += 1
            if is_stop_requested():
                stopped = True
                break

            if can_verify_frames:
                existing, total = count_existing_frames(
                    output_path, frame_start, frame_end, increment
                )
                if all_frames_exist(output_path, frame_start, frame_end, increment):
                    if log_callback and pass_num > 1:
                        log_callback(
                            f"{prefix}All {total} frame(s) present on disk "
                            f"({frame_start}–{frame_end})."
                        )
                    break

            segment_start = None
            segment_end = None
            if can_verify_frames and (skip_existing_frames or pass_num > 1):
                if pass_num > 1:
                    _wait_sessions_idle(log_callback=log_callback, max_wait=180)
                segment_start = first_missing_frame(
                    output_path, frame_start, frame_end, increment
                )
                if segment_start is None:
                    break
                segment_end = frame_end
                if log_callback:
                    if pass_num == 1 and skip_existing_frames:
                        if existing and total and existing > 0:
                            log_callback(
                                f"{prefix}Skip: {existing}/{total} frame(s) on disk; "
                                f"rendering missing span {segment_start}–{segment_end}"
                            )
                        else:
                            log_callback(
                                f"{prefix}Skip enabled; rendering "
                                f"{segment_start}–{segment_end}"
                            )
                    elif pass_num > 1:
                        log_callback(
                            f"{prefix}Pass {pass_num}/{max_passes}: missing frames "
                            f"{segment_start}–{segment_end} "
                            f"({existing}/{total} on disk, range {frame_start}–{frame_end})"
                        )

            if pass_num > 1:
                log_path = os.path.join(logs_dir(), f"render_{uuid.uuid4().hex[:8]}.log")

            cmd = build_aerender_cmd(
                job,
                log_path=log_path,
                segment_start=segment_start,
                segment_end=segment_end,
            )
            logger.info("Running: %s", " ".join(cmd))
            if log_callback:
                label = "Command" if pass_num == 1 else f"Pass {pass_num} command"
                log_callback(f"{prefix}{label}: {' '.join(cmd)}")
                rs_override = resolve_rs_template(job)
                om_override = resolve_om_template(job)
                rs_ui = (job.get("rs_template") or "").strip()
                if is_use_queue_rs(rs_ui):
                    log_callback(
                        f"{prefix}Render settings: (Use queue) from AE render queue"
                        + (
                            " — proxy enabled on this item"
                            if job.get("use_proxy") in (True, "1", 1)
                            else ""
                        )
                    )
                elif rs_override:
                    if om_override:
                        log_callback(
                            f"{prefix}RS override: {rs_override} | "
                            f"Output module: {om_override}"
                        )
                    else:
                        log_callback(
                            f"{prefix}Warning: render settings '{rs_override}' without an "
                            "output module template — set RS to (Use queue) or re-scan the AEP."
                        )

            proc = subprocess.Popen(cmd, **popen_kw)
            session = _RenderSession(proc, project)
            _register_session(session)

            if log_preview and pass_num == 1:
                log_preview.set_log_file(log_path)
                log_preview.on_start()

            if pass_num == 1:
                line_queue = queue.Queue()

                def _stdout_reader():
                    try:
                        for chunk in iter(proc.stdout.readline, ""):
                            line_queue.put(chunk)
                    except Exception:
                        pass
                    finally:
                        line_queue.put(None)

                threading.Thread(target=_stdout_reader, daemon=True).start()

                while True:
                    if is_stop_requested():
                        stopped = True
                        _kill_session(session, force=True)
                        break

                    _refresh_session_children(session)

                    try:
                        line = line_queue.get(timeout=0.25)
                    except queue.Empty:
                        if proc.poll() is not None:
                            break
                        continue

                    if line is None:
                        if proc.poll() is not None:
                            break
                        continue

                    if line:
                        text = line.strip()
                        if text:
                            logger.info(text)
                            if log_callback:
                                status_msg = log_state.update(text)
                                if status_msg:
                                    log_callback(f"{prefix}{status_msg}")
                                elif any(
                                    k in text.lower()
                                    for k in ("error", "warning", "failed")
                                ):
                                    log_callback(f"{prefix}  {text}")
                                elif any(
                                    k in text.lower()
                                    for k in ("finished", "complete")
                                ):
                                    log_callback(
                                        f"{prefix}AE log says finished — "
                                        "still verifying all frames on disk…"
                                    )
                            if progress_row is not None:
                                ingest_line(progress_row, text)
                            last_ratio = apply_progress_update(
                                text,
                                log_state,
                                prog_start,
                                prog_end,
                                last_ratio,
                                _emit_progress,
                            )
                            if log_preview:
                                log_preview.on_log_line(text)
                            frame_num = parse_aerender_progress_frame(text)
                            if frame_num is None:
                                frame_num = parse_frame_from_line(text)
                            if log_preview and frame_num is not None:
                                log_preview.on_progress_frame(frame_num, text)
                            elif (
                                use_full_queue
                                and frame_num is not None
                                and prog_end > prog_start
                            ):
                                ratio = (frame_num - prog_start) / float(
                                    prog_end - prog_start
                                )
                                ratio = min(0.99, max(last_ratio, ratio))
                                _emit_progress(ratio)
                                last_ratio = ratio
                            elif use_full_queue and "PROGRESS" in text.upper():
                                _emit_progress(min(0.99, last_ratio + 0.01))
                                last_ratio = min(0.99, last_ratio + 0.01)

                    if preview_watcher:
                        preview_watcher.poll()
                    if tg_previewer:
                        tg_previewer.poll()
                    if log_preview:

                        def _on_log_line(line):
                            nonlocal last_ratio
                            text = (line or "").strip()
                            if not text:
                                return
                            log_state.update(text)
                            if progress_row is not None:
                                ingest_line(progress_row, text)
                            last_ratio = apply_progress_update(
                                text,
                                log_state,
                                prog_start,
                                prog_end,
                                last_ratio,
                                _emit_progress,
                            )

                        log_preview.poll(on_line=_on_log_line)

            if stopped or is_stop_requested():
                _kill_session(session, force=True)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                _unregister_session(session)
                if log_callback:
                    log_callback(f"{prefix}Render stopped by user.")
                cleanup_incomplete_outputs(log_callback=log_callback)
                duration_sec = time.monotonic() - render_started_at
                status_label, err_detail = status_from_render_result(
                    -1, stopped=True
                )
                stop_msg = format_job_telegram(
                    job,
                    "error",
                    status=status_label,
                    duration_sec=duration_sec,
                    error_detail=err_detail,
                )
                _notify_telegram(stop_msg, log_callback)
                return -1

            retcode = proc.wait()
            if preview_watcher:
                preview_watcher.flush()
            if log_preview and pass_num == 1:
                log_preview.flush()

            if is_stop_requested():
                _unregister_session(session)
                duration_sec = time.monotonic() - render_started_at
                status_label, err_detail = status_from_render_result(
                    -1, stopped=True
                )
                stop_msg = format_job_telegram(
                    job,
                    "error",
                    status=status_label,
                    duration_sec=duration_sec,
                    error_detail=err_detail,
                )
                _notify_telegram(stop_msg, log_callback)
                return -1

            summary = parse_aerender_log_summary(log_path)
            if frame_start is None and summary.get("start") is not None:
                frame_start = summary["start"]
            if frame_end is None and summary.get("end") is not None:
                frame_end = summary["end"]
            early_msg = describe_aerender_early_exit(summary, frame_start, frame_end)
            if early_msg and log_callback:
                log_callback(f"{prefix}{early_msg}")

            if progress_row is not None:
                set_log_progress_frozen(progress_row, True)
                if can_verify_frames:
                    disk_existing, disk_total = count_existing_frames(
                        output_path, frame_start, frame_end, increment
                    )
                    if disk_total > 0:
                        set_ratio_from_disk(
                            progress_row, disk_existing, disk_total
                        )
                        _emit_progress(
                            disk_existing / float(disk_total),
                            allow_complete=False,
                            force=True,
                            disk_existing=disk_existing,
                            disk_total=disk_total,
                        )

            if retcode == 0 and log_callback and can_verify_frames:
                log_callback(
                    f"{prefix}aerender process exited; verifying frame output on disk…"
                )

            if retcode != 0:
                _unregister_session(session)
                _emit_progress(last_ratio)
                break

            if preview_watcher:
                preview_watcher.flush(timeout=30)
            if tg_previewer:
                tg_previewer.flush(timeout=90)
            if log_preview and send2bot > 0:
                log_preview.flush(timeout=30)

            if can_verify_frames:
                existing, total = wait_for_render_output_idle(
                    output_path,
                    frame_start,
                    frame_end,
                    increment,
                    log_path=log_path,
                    log_callback=log_callback,
                    prefix=prefix,
                    progress_callback=lambda r, ex, tot: _emit_progress(
                        r,
                        allow_complete=False,
                        force=True,
                        disk_existing=ex,
                        disk_total=tot,
                    ),
                    on_poll=_poll_telegram_previews,
                    ae_busy_check=lambda s=session: _session_tree_busy(s),
                )
                _unregister_session(session)
                if progress_row is not None and total and existing is not None:
                    set_ratio_from_disk(progress_row, existing, total)
                    _emit_progress(
                        existing / float(total),
                        allow_complete=existing >= total,
                        force=True,
                        disk_existing=existing,
                        disk_total=total,
                    )
            else:
                existing, total = None, None
                _unregister_session(session)

            if progress_row is not None:
                set_log_progress_frozen(progress_row, False)

            if can_verify_frames:
                if all_frames_exist(output_path, frame_start, frame_end, increment):
                    existing, total = count_existing_frames(
                        output_path, frame_start, frame_end, increment
                    )
                    break
                if existing is not None and existing <= last_existing_on_disk:
                    stall_passes += 1
                    if stall_passes >= STALL_WITHOUT_NEW_FRAMES:
                        if log_callback:
                            log_callback(
                                f"{prefix}No new frames after {stall_passes} aerender "
                                f"pass(es); {existing}/{total} on disk "
                                f"(range {frame_start}–{frame_end})."
                            )
                        break
                else:
                    stall_passes = 0
                if existing is not None:
                    last_existing_on_disk = existing

        if retcode == 0:
            if can_verify_frames:
                existing, total = count_existing_frames(
                    output_path, frame_start, frame_end, increment
                )
            elif existing is None or total is None:
                existing, total = _check_output_frame_count(job, log_callback, prefix)
            if (
                can_verify_frames
                and total
                and existing is not None
                and not all_frames_exist(
                    output_path, frame_start, frame_end, increment
                )
            ):
                missing = first_missing_frame(
                    output_path, frame_start, frame_end, increment
                )
                if log_callback:
                    log_callback(
                        f"{prefix}Incomplete output: {existing}/{total} frame(s) on disk "
                        f"(range {frame_start}–{frame_end}"
                        + (f", first missing frame {missing}" if missing is not None else "")
                        + ")."
                    )
                if progress_row is not None:
                    _emit_progress(min(0.99, existing / float(total)))
                if log_callback:
                    log_callback(
                        f"{prefix}Render incomplete — not all frames in range are on disk."
                    )
                    for hint in format_render_failure_hints(
                        log_path,
                        retcode,
                        output_path,
                        frame_start,
                        frame_end,
                        job=job,
                    ):
                        log_callback(f"{prefix}  {hint}")
                duration_sec = time.monotonic() - render_started_at
                detail = (
                    f"{existing}/{total} frame(s) on disk"
                    if existing is not None and total
                    else "Not all frames are on disk"
                )
                status_label, err_detail = status_from_render_result(
                    2, incomplete=True, error_detail=detail
                )
                inc_msg = format_job_telegram(
                    job,
                    "error",
                    status=status_label,
                    duration_sec=duration_sec,
                    error_detail=err_detail,
                )
                _notify_telegram(inc_msg, log_callback)
                return 2
            if (
                output_path
                and frame_start is not None
                and frame_end is not None
                and can_verify_frames
            ):
                mark_job_output_complete(
                    output_path, frame_start, frame_end, increment
                )
            if progress_row is not None and total and existing is not None:
                _emit_progress(1.0, allow_complete=True)
            duration_sec = time.monotonic() - render_started_at
            status_label, err_detail = status_from_render_result(0)
            finish_msg = format_job_telegram(
                job,
                "finish",
                status=status_label,
                duration_sec=duration_sec,
            )
            if log_callback:
                log_callback(prefix + finish_msg.replace("\n", " | "))
            _notify_telegram(finish_msg, log_callback)
        else:
            duration_sec = time.monotonic() - render_started_at
            summary = parse_aerender_log_summary(log_path)
            early = describe_aerender_early_exit(summary, frame_start, frame_end)
            err_detail = early or ""
            status_label, err_detail = status_from_render_result(
                retcode,
                incomplete=(retcode == 2),
                error_detail=err_detail,
            )
            fail_msg = format_job_telegram(
                job,
                "error",
                status=status_label,
                duration_sec=duration_sec,
                error_detail=err_detail,
            )
            if log_callback:
                log_callback(prefix + fail_msg.replace("\n", " | "))
            _notify_telegram(fail_msg, log_callback)
        return retcode

    except Exception as e:
        logger.exception("Render error")
        err_text = str(e)
        if log_callback:
            log_callback(f"{prefix}Error: {err_text}")
        err_msg = format_job_telegram(
            job,
            "error",
            status="Error",
            error_detail=err_text,
        )
        _notify_telegram(err_msg, log_callback)
        if session:
            _kill_session(session, force=True)
        return -1
    finally:
        if progress_row is not None:
            end_job(progress_row)
        if session:
            if proc and proc.poll() is None and is_stop_requested():
                _kill_session(session, force=True)
            _unregister_session(session)


def stop_all_renders(log_callback=None):
    """Hard stop — used when closing the app. Only kills renders this app started."""
    stop_render(log_callback=log_callback)
    return True
