"""Queue completion: verify outputs, Telegram summary, optional system sleep."""

import os

from ae_paths import all_frames_exist
from ae_render_settings import resolve_frame_range
from app_paths import config_path
from system_sleep import request_system_sleep
from telegram_notifier import send_message
from telegram_report import format_job_telegram


def load_sleep_on_queue_finish():
    path = config_path()
    if not os.path.isfile(path):
        return False
    try:
        import json

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return bool(data.get("sleep_on_queue_finish", False))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False


def verify_job_outputs_on_disk(job_summary):
    """Return (ok, detail) for one enabled row summary dict."""
    status = (job_summary.get("status") or "").strip()
    if status == "Skipped":
        return True, ""
    if status != "Completed":
        return False, f"status is {status or 'unknown'}"

    if job_summary.get("full_queue"):
        return True, ""

    output_path = (job_summary.get("output_path") or "").strip()
    start, end = resolve_frame_range(
        job_summary.get("start_frame"), job_summary.get("end_frame")
    )
    increment = max(1, int(job_summary.get("increment") or 1))
    if not output_path or start is None or end is None:
        return True, ""

    if all_frames_exist(output_path, start, end, increment):
        return True, ""
    return False, f"missing frames on disk ({start}–{end})"


def queue_can_sleep(summaries):
    """
    True when every enabled job is Completed or Skipped and Completed jobs
    have full frame output on disk.
    """
    if not summaries:
        return False, "no enabled jobs"

    for job in summaries:
        status = (job.get("status") or "").strip()
        if status not in ("Completed", "Skipped"):
            label = job.get("comp") or os.path.basename(job.get("aep") or "") or "job"
            return False, f"{label}: {status or 'not finished'}"

    for job in summaries:
        ok, detail = verify_job_outputs_on_disk(job)
        if not ok:
            label = job.get("comp") or os.path.basename(job.get("aep") or "") or "job"
            return False, f"{label}: {detail}"

    return True, ""


def _summary_as_job(summary):
    return {
        "project": summary.get("aep") or "",
        "comp": summary.get("comp") or "",
        "start_frame": summary.get("start_frame"),
        "end_frame": summary.get("end_frame"),
        "increment": summary.get("increment") or 1,
        "full_queue": summary.get("full_queue", False),
    }


def format_queue_complete_telegram(summaries):
    lines = ["Queue finished", f"Jobs: {len(summaries)}", ""]
    for summary in summaries:
        status = (summary.get("status") or "—").strip()
        event = "finish" if status in ("Completed", "Skipped") else "error"
        block = format_job_telegram(
            _summary_as_job(summary),
            event,
            status=status,
            duration_text=summary.get("duration_text") or "",
            error_detail=summary.get("error_detail") or "",
        )
        lines.append(block)
        lines.append("")
    return "\n".join(lines).rstrip()


def _queue_had_user_stop(summaries):
    """True if any enabled row was stopped or did not finish cleanly."""
    for job in summaries:
        status = (job.get("status") or "").strip()
        if status in ("Stopped", "Failed", "Incomplete", "Running", "Pending"):
            return True
    return False


def handle_queue_finished(was_stopped, summaries, sleep_on_finish, log_callback=None):
    """
    Run after the worker thread exits (call from UI thread).

    Returns True if the caller should put the PC to sleep.
    """
    try:
        if was_stopped:
            if log_callback:
                log_callback("Queue stopped — PC sleep will not run.")
            return False

        if not summaries:
            if log_callback:
                log_callback("Queue finished with no enabled jobs.")
            return False

        if _queue_had_user_stop(summaries):
            if log_callback:
                log_callback(
                    "Queue has stopped or unfinished jobs — PC sleep will not run."
                )
            return False

        ok, reason = queue_can_sleep(summaries)
        msg = format_queue_complete_telegram(summaries)
        tg_ok, tg_err = send_message(msg)
        if log_callback:
            if tg_ok:
                log_callback("Telegram: queue summary sent.")
            elif tg_err:
                log_callback(f"Telegram queue summary failed: {tg_err}")

        if not sleep_on_finish:
            return False

        if not ok:
            if log_callback:
                log_callback(f"PC sleep skipped — {reason}")
            return False

        if log_callback:
            log_callback(
                "All frames verified on disk. Putting the PC to sleep…"
            )
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"Queue finalize error: {exc}")
        return False


def run_system_sleep(log_callback=None):
    """Suspend the workstation."""
    sleep_ok, sleep_err = request_system_sleep()
    if log_callback:
        if sleep_ok:
            log_callback("Sleep command sent.")
        else:
            log_callback(f"Could not sleep: {sleep_err}")
    return sleep_ok
