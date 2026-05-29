"""Parse aerender log files for work area, last frame, and premature 'finished' lines."""
import os
import re

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
