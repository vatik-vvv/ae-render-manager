"""Shared helpers for render output image paths (no imports from frame_preview)."""
import os

PREVIEW_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".exr",
}

RENDER_OUTPUT_EXTENSIONS = PREVIEW_EXTENSIONS | {
    ".dpx",
    ".tga",
    ".psd",
    ".mov",
    ".mp4",
    ".m4v",
    ".avi",
    ".mxf",
    ".mkv",
    ".wav",
    ".aiff",
    ".aif",
    ".aac",
    ".m4a",
}


def is_render_output(path):
    basename = os.path.basename(path).lower()
    if "aov" in basename:
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in RENDER_OUTPUT_EXTENSIONS


def is_preview_frame(frame_path):
    if not is_render_output(frame_path):
        return False
    ext = os.path.splitext(frame_path)[1].lower()
    return ext in PREVIEW_EXTENSIONS
