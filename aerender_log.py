"""Parse aerender log files for work area, last frame, and premature 'finished' lines."""
import os
import re

from ae_paths import expand_frame_in_path, has_frame_tokens
from render_progress import (
    AERENDER_PROGRESS_FRAME_RE,
    AERENDER_WORK_AREA_END_RE,
)

AERENDER_WORK_AREA_START_RE = re.compile(r"PROGRESS:\s+Start:\s+0*(\d+)", re.I)
AERENDER_DURATION_RE = re.compile(r"PROGRESS:\s+Duration:\s+0*(\d+)", re.I)
AERENDER_LOG_DONE_RE = re.compile(
    r"Finished composition|Finished Rendering|Total Time Elapsed",
    re.I,
)


def parse_aerender_log_summary(log_path):
    """
    Scan an aerender log for work-area bounds, last frame touched, and 'finished' markers.

    Returns dict with keys: start, end, duration, last_frame, log_finished (bool).
    """
    result = {
        "start": None,
        "end": None,
        "duration": None,
        "last_frame": None,
        "log_finished": False,
    }
    if not log_path or not os.path.isfile(log_path):
        return result
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                m = AERENDER_WORK_AREA_START_RE.search(text)
                if m:
                    result["start"] = int(m.group(1))
                m = AERENDER_WORK_AREA_END_RE.search(text)
                if m:
                    result["end"] = int(m.group(1))
                m = AERENDER_DURATION_RE.search(text)
                if m:
                    result["duration"] = int(m.group(1))
                m = AERENDER_PROGRESS_FRAME_RE.search(text)
                if m:
                    result["last_frame"] = int(m.group(1))
                if AERENDER_LOG_DONE_RE.search(text):
                    result["log_finished"] = True
    except OSError:
        pass
    return result


def describe_aerender_early_exit(summary, expected_start, expected_end):
    """Human-readable reason when aerender log says done but work area is incomplete."""
    if not summary:
        return None
    last = summary.get("last_frame")
    end = summary.get("end") if summary.get("end") is not None else expected_end
    if not summary.get("log_finished"):
        return None
    if last is None or end is None:
        return (
            "aerender log contains a 'finished' line before the expected frame range "
            "was verified on disk."
        )
    if last < end:
        return (
            f"aerender reported finished after frame {last}, but the queue work area "
            f"ends at frame {end} ({end - last} frame(s) not reached in the log)."
        )
    if expected_start is not None and last < expected_start:
        return f"aerender finished at frame {last}, below range start {expected_start}."
    return None


def format_render_failure_hints(
    log_path,
    retcode,
    output_path,
    frame_start,
    frame_end,
    job=None,
):
    """Actionable lines when aerender exits but expected frames are missing."""
    from ae_render_settings import (
        is_use_queue_rs,
        resolve_om_template,
        resolve_rs_template,
    )

    hints = []
    if retcode not in (None, 0):
        hints.append(f"aerender exit code: {retcode}")
    if log_path:
        try:
            size = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
        except OSError:
            size = -1
        hints.append(f"aerender log: {log_path} ({max(0, size)} bytes)")
        if size == 0:
            hints.append(
                "Log is empty — open the command in a terminal or check stderr lines above."
            )
        elif size > 0:
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as handle:
                    tail = handle.read()[-4096:]
                for line in tail.splitlines():
                    low = line.lower()
                    if any(k in low for k in ("error", "failed", "could not", "unable")):
                        hints.append(f"log: {line.strip()[:240]}")
            except OSError:
                pass
    if output_path and has_frame_tokens(output_path):
        probe = expand_frame_in_path(output_path, frame_start or 1)
        out_dir = os.path.dirname(probe)
        if out_dir and os.path.isdir(out_dir):
            try:
                names = sorted(os.listdir(out_dir))
            except OSError:
                names = []
            hints.append(f"Output folder: {out_dir} ({len(names)} file(s))")
            for name in names[:8]:
                hints.append(f"  {name}")
            if len(names) > 8:
                hints.append(f"  … and {len(names) - 8} more")
        elif out_dir:
            hints.append(f"Output folder does not exist: {out_dir}")
    if job:
        rs = resolve_rs_template(job)
        om = resolve_om_template(job)
        if rs:
            hints.append(f"Render settings override: {rs}")
            if om:
                hints.append(f"Output module override: {om}")
            else:
                hints.append(
                    "No output module template — with a custom RS, also pass -OMtemplate "
                    "or set RS to (Use queue)."
                )
        elif not is_use_queue_rs(job.get("rs_template")):
            from ae_render_settings import USE_QUEUE_RS

            hints.append(
                f"Tip: set RS to {USE_QUEUE_RS} to use the AE render queue output module and path."
            )
    if frame_start is not None and frame_end is not None:
        hints.append(f"Expected frames: {frame_start}–{frame_end}")
    return hints
