"""Format Telegram notifications for render jobs and queue completion."""

import os

from ae_render_settings import resolve_frame_range, resolve_frame_value


def frame_count(start, end, increment=1):
    start_val = resolve_frame_value(start)
    end_val = resolve_frame_value(end)
    if start_val is None or end_val is None or end_val < start_val:
        return None
    inc = max(1, int(increment or 1))
    return (end_val - start_val) // inc + 1


def duration_text_to_seconds(text):
    """Parse duration from UI (M:SS or H:MM:SS) to seconds."""
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return None


def _format_elapsed(seconds):
    total = int(max(0, round(seconds)))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _avg_frame_time(duration_sec, total_frames):
    if not duration_sec or not total_frames or total_frames <= 0:
        return "—"
    return _format_elapsed(duration_sec / total_frames)


def _comp_label(job):
    if job.get("full_queue"):
        return "Full AE render queue"
    comp = (job.get("comp") or "").strip()
    if comp:
        return comp
    rq = job.get("rq_index")
    if rq not in (None, ""):
        return f"RQ#{rq}"
    return "—"


def _frame_range_lines(job):
    start = resolve_frame_value(job.get("start_frame"))
    end = resolve_frame_value(job.get("end_frame"))
    increment = max(1, int(job.get("increment") or 1))
    total = frame_count(start, end, increment)
    start_s = str(start) if start is not None else "—"
    end_s = str(end) if end is not None else "—"
    total_s = str(total) if total is not None else "—"
    return start_s, end_s, total_s, total


def format_job_telegram(
    job,
    event,
    *,
    status="",
    duration_sec=None,
    error_detail="",
    duration_text="",
):
    """
    Build a Telegram body for one queue job.

    event: "start" | "finish" | "error"
    """
    aep = (job.get("project") or "").strip()
    aep_name = os.path.basename(aep) if aep else "—"
    comp = _comp_label(job)
    start_s, end_s, total_s, total = _frame_range_lines(job)

    lines = []
    if event == "start":
        lines.append("Start render")
    elif event == "error":
        lines.append("Render error")
    else:
        lines.append("Render finished")

    lines.extend(
        [
            f"AEP: {aep_name}",
            f"Comp: {comp}",
            f"Start frame: {start_s}",
            f"End frame: {end_s}",
            f"Total frames: {total_s}",
        ]
    )

    # Timing is only meaningful when a job has finished (or failed after running).
    if event != "start":
        if duration_text:
            lines.append(f"Total render time: {duration_text}")
        elif duration_sec is not None:
            lines.append(f"Total render time: {_format_elapsed(duration_sec)}")
        elif event == "finish":
            lines.append("Total render time: —")

        avg_sec = duration_sec
        if avg_sec is None and duration_text:
            avg_sec = duration_text_to_seconds(duration_text)
        if total and avg_sec:
            lines.append(f"Avg frame time: {_avg_frame_time(avg_sec, total)}")
        elif event == "finish":
            lines.append("Avg frame time: —")

    if status:
        lines.append(f"Status: {status}")
    if error_detail:
        lines.append(f"Error: {error_detail}")

    return "\n".join(lines)


def status_from_render_result(retcode, *, stopped=False, incomplete=False, error_detail=""):
    if stopped or retcode == -1:
        return "Stopped", error_detail or "Stopped by user"
    if incomplete or retcode == 2:
        detail = error_detail or "Not all frames are on disk"
        return "Incomplete", detail
    if retcode == 0:
        return "Finished", ""
    detail = error_detail or f"aerender exit code {retcode}"
    return "Error", detail
