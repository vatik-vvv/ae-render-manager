# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

def _local_py_hiddenimports():
    """Bundle every project .py module (flat layout) so new files are not missed."""
    names = []
    for name in os.listdir(_SPEC_DIR):
        if name.endswith(".py") and name not in ("main.py", "scan_cli.py"):
            names.append(name[:-3])
    return sorted(set(names))

extra_datas = [
    ("config.example.json", "."),
    ("scan_render_queue.jsx", "."),
]
extra_binaries = []
hiddenimports = _local_py_hiddenimports() + [
    "PIL",
    "PIL.Image",
    "numpy",
    "cv2",
    "frame_preview",
    "app_paths",
    "ae_paths",
    "ae_render_settings",
    "aerender_log",
    "aerender_log_preview",
    "parallel_pool",
    "queue_job_source",
    "ui_widgets",
    "ui_theme",
    "render_runner",
    "render_progress",
    "render_progress_tracker",
    "telegram_notifier",
    "telegram_previews",
    "send2bot_milestones",
    "preview_media",
    "output_cleanup",
    "process_tree",
    "scan_worker",
    "scan_queue",
    "psutil",
    "single_instance",
    "PySide6.QtNetwork",
]
hiddenimports = sorted(set(hiddenimports))

def _ensure_exe_icon():
    """Windows EXE icon: prefer AERM_icon.ico, generate from AERM_icon.png if needed."""
    if os.path.isfile("AERM_icon.ico"):
        return "AERM_icon.ico"
    if os.path.isfile("AERM_icon.png"):
        try:
            from PIL import Image

            img = Image.open("AERM_icon.png").convert("RGBA")
            sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
            img.save("AERM_icon.ico", format="ICO", sizes=sizes)
            return "AERM_icon.ico"
        except Exception:
            pass
    if os.path.isfile("app_icon.ico"):
        return "app_icon.ico"
    return None


icon_file = _ensure_exe_icon()
for asset in ("AERM_icon.png", "AERM_icon.ico", "app_icon.ico", "app_icon.png"):
    if os.path.isfile(asset):
        extra_datas.append((asset, "."))

for pkg in ("PIL", "cv2"):
    try:
        datas, binaries, hidden = collect_all(pkg)
        extra_datas += datas
        extra_binaries += binaries
        hiddenimports += hidden
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[_SPEC_DIR],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy.tests", "numpy.f2py.tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AERenderManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
