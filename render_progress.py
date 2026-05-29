"""Map aerender log lines to progress and human-readable status."""

import re



FRAME_OF_RE = re.compile(r"frame\s*:?\s*(\d+)\s+of\s+(\d+)", re.I)

RENDERING_FRAME_OF_RE = re.compile(r"rendering\s+frame\s+(\d+)\s+of\s+(\d+)", re.I)
BARE_OF_RE = re.compile(r"(?:^|\s)(\d+)\s+of\s+(\d+)(?:\s|$)", re.I)
FRAME_SLASH_RE = re.compile(r"frame\s+(\d+)\s*/\s*(\d+)", re.I)

# aerender: PROGRESS:  00879 (880): 4 Seconds  /  PROGRESS:  (Skipping 00881) (882): 0 Seconds
AERENDER_PROGRESS_FRAME_RE = re.compile(
    r"PROGRESS:\s+(?:\(Skipping\s+)?0*(\d+)\s+\(\d+\):",
    re.I,
)

AERENDER_WORK_AREA_END_RE = re.compile(r"PROGRESS:\s+End:\s+0*(\d+)", re.I)

FRAME_HINT_RES = (

    re.compile(r"rendering\s+frame\s*:?\s*(\d+)", re.I),

    re.compile(r"frame\s*:?\s*(\d+)", re.I),

    re.compile(r"Time\s+Elapsed.*?Frame\s+(\d+)", re.I),

)



COMP_START_RES = (

    re.compile(

        r"(?:starting|beginning)\s+(?:to\s+render\s+)?(?:composition|comp(?:osition)?)\s*[\"']?([^\"'\n\r]+?)[\"']?\s*$",

        re.I,

    ),

    re.compile(r"rendering\s+[\"']?([^\"'\n\r]+?)[\"']?\s*(?:\(|$)", re.I),

    re.compile(r"comp(?:osition)?\s*[\"']([^\"'\n\r]+)[\"']", re.I),

)



RQ_ITEM_RES = (

    re.compile(r"render\s+queue\s+item\s*[#:]?\s*(\d+)", re.I),

    re.compile(r"\bRQ\s*item\s*(\d+)", re.I),

    re.compile(r"^\s*(\d+)\s+/\s+.+", re.I),

)



# aerender: PROGRESS:  Render Queue Item 1 (25):  00:00:05:23 / 00:00:22:10

PROGRESS_PERCENT_RE = re.compile(r"PROGRESS:.*?\((\d+)\)", re.I)

PROGRESS_SEGMENT_RE = re.compile(

    r"PROGRESS:.*?\(\d+\)\s*:\s*(\d+)\s*/\s*(\d+)",

    re.I,

)

TIMECODE_PAIR_RE = re.compile(

    r"(\d+):(\d{2}):(\d{2}):(\d{2})\s*/\s*(\d+):(\d{2}):(\d{2}):(\d{2})",

)





def _timecode_parts_to_frames(h, m, s, f, fps=30):

    return int(h) * 3600 * fps + int(m) * 60 * fps + int(s) * fps + int(f)





def ratio_for_frame(frame, start_frame, end_frame):

    try:

        frame = int(frame)

        start_frame = int(start_frame)

        end_frame = int(end_frame)

    except (TypeError, ValueError):

        return 0.0

    if end_frame < start_frame:

        return 1.0

    span = end_frame - start_frame

    if span == 0:

        return 1.0

    return min(1.0, max(0.0, (frame - start_frame) / float(span)))





def parse_aerender_progress_frame(line):
    """Frame index from aerender PROGRESS lines (not the parenthetical sequence counter)."""
    if not line:
        return None
    match = AERENDER_PROGRESS_FRAME_RE.search(line)
    if match:
        return int(match.group(1))
    return None


def parse_aerender_work_area_end(line):
    if not line:
        return None
    match = AERENDER_WORK_AREA_END_RE.search(line)
    if match:
        return int(match.group(1))
    return None


def parse_frame_pair_from_line(line):

    if not line:

        return None, None

    for pattern in (RENDERING_FRAME_OF_RE, FRAME_OF_RE, FRAME_SLASH_RE, BARE_OF_RE):
        match = pattern.search(line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None





def parse_frame_from_line(line):

    if not line:

        return None

    frame, _total = parse_frame_pair_from_line(line)

    if frame is not None:

        return frame

    match = PROGRESS_SEGMENT_RE.search(line)

    if match:

        return int(match.group(1))

    if "PROGRESS:" in line.upper():

        return None

    for pattern in FRAME_HINT_RES:

        match = pattern.search(line)

        if match:

            return int(match.group(1))

    return None





def parse_frame_total_from_line(line):

    if not line:

        return None

    _frame, total = parse_frame_pair_from_line(line)

    if total is not None:

        return total

    match = re.search(r"frame\s*:?\s*\d+\s+of\s+(\d+)", line, re.I)

    if match:

        return int(match.group(1))

    match = PROGRESS_SEGMENT_RE.search(line)

    if match:

        return int(match.group(2))

    return None





def ratio_from_timecode_line(line):

    match = TIMECODE_PAIR_RE.search(line or "")

    if not match:

        return None

    current = _timecode_parts_to_frames(*match.groups()[:4])

    total = _timecode_parts_to_frames(*match.groups()[4:8])

    if total <= 0:

        return None

    return min(1.0, current / float(total))





def progress_ratio_from_log_state(log_state, start_frame=None, end_frame=None):

    if log_state is None:

        return None

    frame = getattr(log_state, "frame", None)

    total = getattr(log_state, "frame_total", None)

    if frame is not None and total and int(total) > 0:

        return min(1.0, int(frame) / float(int(total)))

    if frame is not None and start_frame is not None and end_frame is not None:

        try:

            start_frame = int(start_frame)

            end_frame = int(end_frame)

        except (TypeError, ValueError):

            return None

        if int(frame) < start_frame or int(frame) > end_frame:

            return None

        return ratio_for_frame(frame, start_frame, end_frame)

    return None





def progress_ratio_from_line(line, start_frame=None, end_frame=None, log_state=None):

    if not line:

        return progress_ratio_from_log_state(log_state, start_frame, end_frame)

    ae_frame = parse_aerender_progress_frame(line)
    if ae_frame is not None and start_frame is not None and end_frame is not None:
        try:
            return ratio_for_frame(ae_frame, int(start_frame), int(end_frame))
        except (TypeError, ValueError):
            pass

    frame, total = parse_frame_pair_from_line(line)

    if frame is not None and total and total > 0:

        return min(1.0, frame / float(total))



    lower = line.lower()
    upper = line.upper()

    if "PROGRESS:" in upper:
        # Do not treat "(97)" in "Render Queue Item 4 (97):" or frame lines as a percent.
        if (
            not AERENDER_PROGRESS_FRAME_RE.search(line)
            and "render queue item" not in lower
        ):
            match = PROGRESS_PERCENT_RE.search(line)
            if match:
                pct = int(match.group(1))
                if 0 <= pct <= 100 and "%" in line:
                    return pct / 100.0

        tc_ratio = ratio_from_timecode_line(line)

        if tc_ratio is not None:

            return tc_ratio

        match = PROGRESS_SEGMENT_RE.search(line)

        if match:

            current = int(match.group(1))

            seg_total = int(match.group(2))

            if seg_total > 0:

                return min(1.0, current / float(seg_total))



    frame = parse_frame_from_line(line)

    total = parse_frame_total_from_line(line)

    if frame is not None and total and total > 0:

        return min(1.0, frame / float(total))



    if frame is not None and start_frame is not None and end_frame is not None:

        try:

            start_frame = int(start_frame)

            end_frame = int(end_frame)

        except (TypeError, ValueError):

            return None

        if frame < start_frame or frame > end_frame:

            state_ratio = progress_ratio_from_log_state(log_state, start_frame, end_frame)

            if state_ratio is not None:

                return state_ratio

            return None

        return ratio_for_frame(frame, start_frame, end_frame)



    return progress_ratio_from_log_state(log_state, start_frame, end_frame)





def parse_comp_from_line(line):

    if not line:

        return None

    for pattern in COMP_START_RES:

        match = pattern.search(line)

        if match:

            name = match.group(1).strip().rstrip(":")

            if name and len(name) < 200:

                return name

    if "PROGRESS:" in line.upper():

        parts = line.split(":", 2)

        if len(parts) >= 3:

            tail = parts[-1].strip()

            if tail and not tail[0].isdigit() and "/" not in tail[:3]:

                return tail[:120]

    return None





def parse_rq_item_from_line(line):

    if not line:

        return None

    for pattern in RQ_ITEM_RES:

        match = pattern.search(line)

        if match:

            return int(match.group(1))

    return None





def progress_from_line(line, start_frame, end_frame, log_state=None):

    ratio = progress_ratio_from_line(line, start_frame, end_frame, log_state=log_state)

    if ratio is not None:

        return ratio

    frame = parse_frame_from_line(line)

    if frame is None:

        return progress_ratio_from_log_state(log_state, start_frame, end_frame)

    try:

        start_frame = int(start_frame)

        end_frame = int(end_frame)

    except (TypeError, ValueError):

        return None

    if frame < start_frame or frame > end_frame:

        return progress_ratio_from_log_state(log_state, start_frame, end_frame)

    return ratio_for_frame(frame, start_frame, end_frame)





def apply_progress_update(text, log_state, prog_start, prog_end, last_ratio, progress_callback):

    """Parse one log line, update state, emit monotonic progress ratio."""

    if not progress_callback:

        return last_ratio

    ratio = progress_ratio_from_line(text, prog_start, prog_end, log_state=log_state)

    if ratio is None:

        ratio = progress_from_line(text, prog_start, prog_end, log_state=log_state)

    if ratio is not None and ratio >= last_ratio:
        ratio = min(0.99, ratio)
        progress_callback(ratio)

        return ratio

    return last_ratio





class RenderLogState:

    """Track comp/frame from aerender lines; emit concise status when they change."""



    def __init__(self):

        self.comp = ""

        self.rq_item = None

        self.frame = None

        self.frame_total = None

        self._last_message = ""



    def update(self, line):

        if not line:

            return None

        changed = False

        comp = parse_comp_from_line(line)

        if comp and comp != self.comp:

            self.comp = comp

            changed = True

        rq = parse_rq_item_from_line(line)

        if rq is not None and rq != self.rq_item:

            self.rq_item = rq

            changed = True

        frame = parse_aerender_progress_frame(line)
        if frame is None:
            frame, total = parse_frame_pair_from_line(line)
            if frame is not None:
                if frame != self.frame:
                    self.frame = frame
                    changed = True
            else:
                frame = parse_frame_from_line(line)
                if frame is not None and frame != self.frame:
                    self.frame = frame
                    changed = True
            if total is not None:
                self.frame_total = total
            else:
                parsed_total = parse_frame_total_from_line(line)
                if parsed_total is not None:
                    self.frame_total = parsed_total
        else:
            if frame != self.frame:
                self.frame = frame
                changed = True
            wa_end = parse_aerender_work_area_end(line)
            if wa_end is not None:
                self.frame_total = wa_end + 1

        if frame is not None and not changed:

            changed = True

        if not changed and not any(

            kw in line.lower()

            for kw in ("progress:", "rendering", "frame", "composition", "output")

        ):

            return None

        message = self.format_status()

        if message and message != self._last_message:

            self._last_message = message

            return message

        return None



    def format_status(self):

        parts = []

        if self.rq_item is not None:

            parts.append(f"RQ item {self.rq_item}")

        if self.comp:

            parts.append(f"comp «{self.comp}»")

        if self.frame is not None:

            if self.frame_total:

                parts.append(f"frame {self.frame}/{self.frame_total}")

            else:

                parts.append(f"frame {self.frame}")

        if parts:

            return "Rendering — " + ", ".join(parts)

        return None

