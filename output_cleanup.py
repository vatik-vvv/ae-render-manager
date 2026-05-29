"""Remove incomplete frame files left after a stopped render."""

import logging

import os

import time



from ae_paths import expand_frame_in_path, has_frame_tokens

from preview_media import is_render_output



logger = logging.getLogger(__name__)



_lock = __import__("threading").Lock()

_session_start = 0.0

_output_dirs = set()

_seen_files = set()

_completed_files = set()

_pending_files = set()

_baseline_files = {}

_baselined_dirs = set()

_job_outputs = []





def begin_render_session():

    """Reset per-job tracking at the start of each aerender job."""

    global _session_start

    with _lock:

        _session_start = time.time()

        _seen_files.clear()

        _completed_files.clear()

        _pending_files.clear()





def begin_queue_session():

    """Reset all tracking when a new render queue run starts."""

    global _session_start

    with _lock:

        _session_start = time.time()

        _output_dirs.clear()

        _seen_files.clear()

        _completed_files.clear()

        _pending_files.clear()

        _baseline_files.clear()

        _baselined_dirs.clear()

        _job_outputs.clear()





def clear_tracking():

    begin_queue_session()





def register_job_output(output_path, start_frame=None, end_frame=None, increment=1):

    """Remember output template so cleanup can find sequence / movie files."""

    path = (output_path or "").strip()

    if not path:

        return

    with _lock:

        s = int(start_frame) if start_frame is not None else None

        e = int(end_frame) if end_frame is not None else None

        entry = (path, s, e, max(1, int(increment or 1)))

        if entry not in _job_outputs:

            _job_outputs.append(entry)

    track_output_dir(_output_dir_for_path(path))





def _output_dir_for_path(output_path):

    probe = output_path

    if has_frame_tokens(probe):

        probe = expand_frame_in_path(probe, 0)

    return os.path.dirname(os.path.normpath(probe))





def _norm_path(file_path):

    return os.path.normcase(os.path.abspath(os.path.normpath(file_path)))





def _snapshot_dir_baseline(directory):

    directory = os.path.normcase(os.path.abspath(directory))

    if directory in _baselined_dirs or not os.path.isdir(directory):

        return

    _baselined_dirs.add(directory)

    try:

        for name in os.listdir(directory):

            path = os.path.join(directory, name)

            if not os.path.isfile(path) or not is_render_output(path):

                continue

            try:

                st = os.stat(path)

                _baseline_files[_norm_path(path)] = (st.st_size, st.st_mtime)

            except OSError:

                pass

    except OSError:

        pass





def track_output_dir(directory):

    if not directory:

        return

    directory = os.path.normcase(os.path.abspath(directory))

    with _lock:

        _output_dirs.add(directory)

    _snapshot_dir_baseline(directory)





def mark_file_seen(file_path):

    if not file_path:

        return

    norm = _norm_path(file_path)

    track_output_dir(os.path.dirname(file_path))

    with _lock:

        _seen_files.add(norm)

        if norm not in _completed_files:

            _pending_files.add(norm)





def mark_file_complete(file_path):

    if not file_path:

        return

    norm = _norm_path(file_path)

    with _lock:

        _seen_files.add(norm)

        _completed_files.add(norm)

        _pending_files.discard(norm)





def track_output_file(file_path):

    mark_file_seen(file_path)





def _is_unchanged_baseline(norm_path):

    base = _baseline_files.get(norm_path)

    if base is None:

        return False

    try:

        st = os.stat(norm_path)

    except OSError:

        return False

    base_size, base_mtime = base

    return st.st_size == base_size and st.st_mtime <= base_mtime + 0.001





def _snapshot_state():

    with _lock:

        return (

            set(_output_dirs),

            set(_seen_files),

            set(_completed_files),

            set(_pending_files),

            dict(_baseline_files),

            _session_start,

            list(_job_outputs),

        )





def _reopen_session_outputs_for_cleanup(session_start):

    """Files marked complete during this session may still be partial after Stop."""

    with _lock:

        for norm in list(_completed_files):

            if _is_unchanged_baseline(norm):

                continue

            try:

                st = os.stat(norm)

            except OSError:

                _completed_files.discard(norm)

                _pending_files.add(norm)

                continue

            if st.st_size <= 0:

                _completed_files.discard(norm)

                _pending_files.add(norm)





def _remove_file(path, removed, removed_norms):

    if not path or not os.path.isfile(path):

        return

    if not is_render_output(path):

        return

    norm = _norm_path(path)

    if norm in removed_norms or _is_unchanged_baseline(norm):

        return

    try:

        os.remove(path)

        removed.append(path)

        removed_norms.add(norm)

    except OSError as exc:

        logger.debug("Could not remove %s: %s", path, exc)





def _paths_from_job_outputs(job_outputs):

    paths = []

    for template, start, end, increment in job_outputs:

        if not template:

            continue

        if not has_frame_tokens(template):

            paths.append(os.path.normpath(template))

            continue

        if start is None or end is None:

            continue

        if end < start:

            end = start

        for frame in range(int(start), int(end) + 1, int(increment)):

            paths.append(expand_frame_in_path(template, frame))

    return paths





def _newest_session_file_in_dir(directory, session_start):

    if not os.path.isdir(directory):

        return None

    best_path = None

    best_mtime = 0.0

    try:

        names = os.listdir(directory)

    except OSError:

        return None

    for name in names:

        path = os.path.join(directory, name)

        if not os.path.isfile(path) or not is_render_output(path):

            continue

        if _is_unchanged_baseline(_norm_path(path)):

            continue

        try:

            st = os.stat(path)

        except OSError:

            continue

        if st.st_mtime < session_start - 1.0:

            continue

        if st.st_mtime >= best_mtime:

            best_mtime = st.st_mtime

            best_path = path

    return best_path





def mark_job_output_complete(output_path, start_frame, end_frame, increment=1):
    """Mark rendered sequence files complete so stop-cleanup will not delete them."""
    path = (output_path or "").strip()
    if not path or start_frame is None or end_frame is None:
        return
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    increment = max(1, int(increment or 1))
    with _lock:
        if not has_frame_tokens(path):
            norm = _norm_path(os.path.normpath(path))
            if os.path.isfile(norm):
                _seen_files.add(norm)
                _completed_files.add(norm)
                _pending_files.discard(norm)
            return
        for frame in range(start_frame, end_frame + 1, increment):
            candidate = expand_frame_in_path(path, frame)
            if not candidate or not os.path.isfile(candidate):
                continue
            norm = _norm_path(candidate)
            _seen_files.add(norm)
            _completed_files.add(norm)
            _pending_files.discard(norm)


def cleanup_incomplete_outputs(log_callback=None, settle_seconds=0.8, aggressive=False):

    """After user stop: delete 0-byte placeholders and partial outputs from this session."""

    if settle_seconds > 0:

        time.sleep(settle_seconds)



    _reopen_session_outputs_for_cleanup(_snapshot_state()[5])

    dirs, seen, completed, pending, _baseline, session_start, job_outputs = _snapshot_state()



    for template, _start, _end, _inc in job_outputs:

        directory = _output_dir_for_path(template)

        if directory:

            dirs.add(os.path.normcase(os.path.abspath(directory)))



    removed = []

    removed_norms = set()



    def _delete(path):

        _remove_file(path, removed, removed_norms)



    for norm in list(pending):

        _delete(norm)



    for norm in seen:

        if norm not in completed:

            _delete(norm)



    for template, start, end, increment in job_outputs:

        if not template:

            continue

        if not has_frame_tokens(template):

            path = os.path.normpath(template)

            if not os.path.isfile(path):

                continue

            norm = _norm_path(path)

            if _is_unchanged_baseline(norm):

                continue

            try:

                st = os.stat(path)

            except OSError:

                continue

            if st.st_mtime < session_start - 1.0:

                continue

            if st.st_size <= 0 or norm in pending:

                _delete(path)

            continue

        for path in _paths_from_job_outputs([(template, start, end, increment)]):

            if not os.path.isfile(path):

                continue

            try:

                if os.path.getsize(path) <= 0:

                    _delete(path)

            except OSError:

                pass



    for directory in dirs:

        if not os.path.isdir(directory):

            continue

        try:

            names = os.listdir(directory)

        except OSError:

            continue

        for name in names:

            path = os.path.join(directory, name)

            if not os.path.isfile(path) or not is_render_output(path):

                continue

            norm = _norm_path(path)

            if norm in removed_norms or _is_unchanged_baseline(norm):

                continue

            try:

                st = os.stat(path)

            except OSError:

                continue

            if st.st_mtime < session_start - 1.0:

                continue

            if st.st_size <= 0:

                _delete(path)

                continue

            if norm in pending or (norm in seen and norm not in completed):

                _delete(path)



        if aggressive:

            newest = _newest_session_file_in_dir(directory, session_start)

            if newest:

                norm_new = _norm_path(newest)

                try:

                    recent = os.path.getmtime(newest) >= time.time() - 20.0

                except OSError:

                    recent = False

                if norm_new in pending or norm_new not in completed or recent:

                    _delete(newest)



    if removed and log_callback:

        log_callback(f"Removed {len(removed)} incomplete output file(s) after stop.")

        for path in removed[:10]:

            log_callback(f"  deleted: {path}")

        if len(removed) > 10:

            log_callback(f"  … and {len(removed) - 10} more")

    elif log_callback:

        log_callback(

            "Stop: no incomplete outputs removed "

            f"(tracked {len(dirs)} folder(s), {len(job_outputs)} job output(s))."

        )



    return removed


