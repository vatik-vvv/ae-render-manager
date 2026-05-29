"""Compute send2bot preview frame milestones relative to a render range."""


def send2bot_milestone_frames(start_frame, end_frame, interval):
    """Frame indices to notify: start + interval, start + 2*interval, … up to end."""
    interval = max(1, int(interval or 1))
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    if end_frame < start_frame:
        return
    frame = start_frame + interval
    while frame <= end_frame:
        yield frame
        frame += interval


def is_send2bot_milestone(frame, start_frame, end_frame, interval):
    """True when frame is a send2bot milestone relative to the render start."""
    try:
        frame = int(frame)
        start_frame = int(start_frame)
        end_frame = int(end_frame)
    except (TypeError, ValueError):
        return False
    interval = max(1, int(interval or 1))
    if frame < start_frame + interval or frame > end_frame:
        return False
    return (frame - start_frame) % interval == 0
