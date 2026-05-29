"""Paths for dev run vs PyInstaller frozen executable."""
import os
import sys


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """Config and writable files: folder with the .exe when frozen."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Bundled resources inside the PyInstaller extract."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return os.path.dirname(os.path.abspath(__file__))


def bundled_script(filename):
    path = os.path.join(bundle_dir(), filename)
    if os.path.isfile(path):
        return path
    fallback = os.path.join(app_dir(), filename)
    return fallback if os.path.isfile(fallback) else path


def config_path():
    return os.path.join(app_dir(), "config.json")


def queue_path():
    return os.path.join(app_dir(), "queue.json")


def scan_work_dir():
    """Shared folder for Python ↔ ExtendScript scan handoff (must match JSX Local AppData path)."""
    local_app = os.environ.get("LOCALAPPDATA", "")
    if not local_app:
        local_app = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    base = os.path.join(local_app, "AERenderManager")
    os.makedirs(base, exist_ok=True)
    hint = os.path.join(base, "scan_workdir.txt")
    try:
        with open(hint, "w", encoding="utf-8") as handle:
            handle.write(base.replace("\\", "/"))
    except OSError:
        pass
    return base


def scan_args_path():
    return os.path.join(scan_work_dir(), "scan_args.json")


def logs_dir():
    path = os.path.join(app_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path
