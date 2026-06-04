"""After Effects install discovery and output path helpers."""
import glob
import json
import os
import re
import subprocess
import time

from app_paths import config_path


def detect_ae_installs():
    """Return list of (label, aerender_path, afterfx_path) newest first."""
    pattern = r"C:\Program Files\Adobe\Adobe After Effects *\Support Files"
    found = []
    for support in sorted(glob.glob(pattern), reverse=True):
        aerender = os.path.join(support, "aerender.exe")
        afterfx = os.path.join(support, "AfterFX.com")
        if not os.path.isfile(afterfx):
            afterfx = os.path.join(support, "AfterFX.exe")
        if os.path.isfile(aerender):
            label = os.path.basename(os.path.dirname(os.path.dirname(support)))
            found.append((label, aerender, afterfx if os.path.isfile(afterfx) else ""))
    return found


def load_config():
    cfg_file = config_path()
    if not os.path.isfile(cfg_file):
        return {}
    try:
        with open(cfg_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_aerender_path():
    cfg = load_config()
    path = cfg.get("aerender_path", "")
    if path and os.path.isfile(path):
        return path
    installs = detect_ae_installs()
    return installs[0][1] if installs else "aerender"


def get_afterfx_path():
    cfg = load_config()
    path = cfg.get("afterfx_path", "")
    if path and os.path.isfile(path):
        return path
    installs = detect_ae_installs()
    if installs and installs[0][2]:
        return installs[0][2]
    if installs:
        support = os.path.dirname(installs[0][1])
        for name in ("AfterFX.com", "AfterFX.exe"):
            cand = os.path.join(support, name)
            if os.path.isfile(cand):
                return cand
    return "AfterFX.com"


def get_max_parallel():
    cfg = load_config()
    try:
        value = int(cfg.get("max_parallel", 1))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(8, value))


def has_frame_tokens(path):
    if not path:
        return False
    return bool(re.search(r"\[#+\]|#+|%0\d+d", path))


def expand_frame_in_path(path, frame):
    if not path:
        return path
    result = path
    m = re.search(r"\[(#+)\]", result)
    if m:
        width = len(m.group(1))
        result = result.replace("[" + m.group(1) + "]", str(frame).zfill(width))
    if "#" in result and "[" not in path:
        hashes = re.search(r"(#+)", result)
        if hashes:
            width = len(hashes.group(1))
            result = result.replace(hashes.group(1), str(frame).zfill(width))
    result = re.sub(
        r"%0(\d+)d",
        lambda match: f"{int(frame):0{int(match.group(1))}d}",
        result,
    )
    return os.path.normpath(result)


def ensure_output_directory(output_path):
    if not output_path:
        return
    probe = output_path
    if has_frame_tokens(probe):
        probe = expand_frame_in_path(probe, 1)
    dir_path = os.path.dirname(os.path.normpath(probe))
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)


def frame_output_exists(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def all_frames_exist(output_path, start_frame, end_frame, increment=1):
    if not output_path:
        return False
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    increment = max(1, int(increment or 1))
    if not has_frame_tokens(output_path):
        return frame_output_exists(output_path)
    for frame in range(start_frame, end_frame + 1, increment):
        candidate = expand_frame_in_path(output_path, frame)
        if not frame_output_exists(candidate):
            return False
    return True


AERENDER_LOG_DONE_RE = re.compile(
    r"Finished composition|Finished Rendering|Total Time Elapsed",
    re.I,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def aerender_log_shows_finished(log_path, min_bytes=500):
    if not log_path or not os.path.isfile(log_path):
        return False
    try:
        size = os.path.getsize(log_path)
        if size < min_bytes:
            return False
        with open(log_path, encoding="utf-8", errors="ignore") as handle:
            if size > 65536:
                handle.seek(size - 65536)
            tail = handle.read()
    except OSError:
        return False
    return bool(AERENDER_LOG_DONE_RE.search(tail))


def ae_render_process_running():
    """True while aerender.exe or AfterFX is still listed in the Windows task list."""
    if os.name != "nt":
        return False
    for image in ("aerender.exe", "AfterFX.exe", "AfterFX.com"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=12,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = (result.stdout or "").lower()
        if image.lower() in out and "no tasks are running" not in out:
            return True
    return False


# No new frames on disk for this long (while AE is idle) → stop waiting.
RENDER_OUTPUT_IDLE_TIMEOUT_SEC = 600
# Safety cap only — waiting is driven by frame count, not this limit, while AE is active.
RENDER_OUTPUT_MAX_WAIT_SEC = 48 * 3600


def wait_for_render_output_idle(
    output_path,
    start_frame,
    end_frame,
    increment=1,
    log_path=None,
    log_callback=None,
    prefix="",
    timeout=RENDER_OUTPUT_IDLE_TIMEOUT_SEC,
    poll_interval=2.0,
    stable_polls=5,
    progress_callback=None,
    max_wait=RENDER_OUTPUT_MAX_WAIT_SEC,
    on_poll=None,
    ae_busy_check=None,
):
    """
    After aerender.exe exits, AfterFX may still be writing frames.
    Wait until processes are gone and the on-disk frame count stops increasing.
    The idle timeout slides while AE is running or new frames appear on disk.
    """
    if not output_path or start_frame is None or end_frame is None:
        return None, None

    wait_started = time.monotonic()
    deadline = wait_started + timeout
    last_count = -1
    stable = 0
    last_status_log = 0.0

    if log_callback:
        log_callback(
            f"{prefix}aerender.exe has closed; After Effects is still writing frames "
            "(render not finished yet)."
        )

    from render_runner import is_stop_requested

    while time.monotonic() < deadline:
        if is_stop_requested():
            break
        if time.monotonic() - wait_started >= max_wait:
            break
        existing, total = count_existing_frames(
            output_path, start_frame, end_frame, increment
        )
        if progress_callback and total > 0:
            ratio = min(0.99, existing / float(total))
            try:
                progress_callback(ratio, existing, total)
            except TypeError:
                progress_callback(ratio)
        if ae_busy_check is not None:
            try:
                ae_busy = bool(ae_busy_check())
            except Exception:
                ae_busy = False
        else:
            ae_busy = False
        log_done = aerender_log_shows_finished(log_path)

        if existing > last_count or ae_busy:
            deadline = time.monotonic() + timeout

        if existing >= total and not ae_busy:
            if log_callback:
                log_callback(f"{prefix}All {total} frame(s) on disk.")
            return existing, total

        if existing == last_count:
            stable += 1
        else:
            stable = 0
            last_count = existing

        # AE/aerender stopped and frame count on disk is no longer rising.
        if not ae_busy and stable >= stable_polls:
            if log_callback:
                if existing < total:
                    log_callback(
                        f"{prefix}After Effects exited with incomplete output "
                        f"({existing}/{total} frames on disk)."
                    )
                else:
                    log_callback(
                        f"{prefix}Frame output stable at {existing}/{total} "
                        f"(After Effects no longer running)."
                    )
            return existing, total

        # aerender log may say "Finished" while AfterFX is still writing — keep waiting.
        if log_done and not ae_busy and existing >= total:
            if log_callback:
                log_callback(
                    f"{prefix}Render log finished; {existing}/{total} frame(s) on disk."
                )
            return existing, total

        now = time.monotonic()
        if on_poll:
            try:
                on_poll()
            except Exception:
                pass

        if log_callback and now - last_status_log >= 30.0:
            last_status_log = now
            log_callback(
                f"{prefix}Still rendering to disk… {existing}/{total} frames "
                f"(After Effects running={ae_busy})"
            )
        time.sleep(poll_interval)

    existing, total = count_existing_frames(
        output_path, start_frame, end_frame, increment
    )
    if log_callback:
        if time.monotonic() - wait_started >= max_wait:
            hours = max(1, int(max_wait) // 3600)
            log_callback(
                f"{prefix}Post-render wait limit ({hours} h): "
                f"{existing}/{total} frame(s) on disk."
            )
        else:
            mins = max(1, int(timeout) // 60)
            log_callback(
                f"{prefix}No new frames for {mins} min: "
                f"{existing}/{total} frame(s) on disk."
            )
    return existing, total


def first_missing_frame(output_path, start_frame, end_frame, increment=1):
    """First frame index in [start, end] with no non-empty output file, or None if complete."""
    if not output_path or start_frame is None or end_frame is None:
        return None
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    increment = max(1, int(increment or 1))
    if not has_frame_tokens(output_path):
        return None if frame_output_exists(output_path) else start_frame
    for frame in range(start_frame, end_frame + 1, increment):
        candidate = expand_frame_in_path(output_path, frame)
        if not frame_output_exists(candidate):
            return frame
    return None


def _glob_pattern_from_output_template(output_path):
    """Turn 140_left_[#####].png into 140_left_*.png for directory scans."""
    base = os.path.basename(output_path.replace("\\", "/"))
    if "[" in base:
        base = re.sub(r"\[[#]+\]", "*", base)
    base = re.sub(r"(?<!%)#+", "*", base)
    base = re.sub(r"%0(\d+)d", "*", base)
    return base


def count_files_in_output_dir(output_path):
    """Count non-empty output files matching the template name pattern in its folder."""
    if not output_path or not has_frame_tokens(output_path):
        return 0
    dir_path = os.path.dirname(os.path.normpath(expand_frame_in_path(output_path, 0)))
    if not dir_path or not os.path.isdir(dir_path):
        return 0
    pattern = _glob_pattern_from_output_template(output_path)
    count = 0
    for path in glob.glob(os.path.join(dir_path, pattern)):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            count += 1
    return count


def count_existing_frames(output_path, start_frame, end_frame, increment=1):
    if not output_path:
        return 0, 0
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    increment = max(1, int(increment or 1))
    total = 0
    existing = 0
    if not has_frame_tokens(output_path):
        total = 1
        if frame_output_exists(output_path):
            existing = 1
        return existing, total
    for frame in range(start_frame, end_frame + 1, increment):
        total += 1
        candidate = expand_frame_in_path(output_path, frame)
        if frame_output_exists(candidate):
            existing += 1
    if total > 0 and existing == 0:
        loose = count_files_in_output_dir(output_path)
        if loose > 0:
            existing = min(loose, total)
    return existing, total
