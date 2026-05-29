import json
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app_paths import app_dir, bundle_dir, config_path
from single_instance import SingleInstanceGuard
from ui_main import RenderManager
from ui_theme import apply_dark_theme

CONFIG_FILE = config_path()

_ICON_NAMES = ("AERM_icon.png", "AERM_icon.ico", "app_icon.png", "app_icon.ico")


def _find_app_icon_path():
    for base in (bundle_dir(), app_dir()):
        for name in _ICON_NAMES:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return path
    return None


def ensure_files_exist():
    if not os.path.exists(CONFIG_FILE):
        example = os.path.join(app_dir(), "config.example.json")
        if os.path.exists(example):
            with open(example, encoding="utf-8") as src:
                data = json.load(src)
        else:
            data = {
                "aerender_path": "",
                "afterfx_path": "",
                "max_parallel": 1,
                "telegram": {"bot_token": "", "chat_id": "", "preview_max_side": 2000},
                "ui": {"width": 1200, "height": 800},
                "language": "en",
                "aep_files": [],
                "queue": [],
            }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


def main():
    ensure_files_exist()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("After Effects Render Manager")
    apply_dark_theme(app)
    icon_path = _find_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    instance_guard = SingleInstanceGuard()
    if not instance_guard.try_acquire():
        QMessageBox.information(
            None,
            "After Effects Render Manager",
            "The application is already running.\n\n"
            "The existing window has been brought to the front.",
        )
        return 1

    window = RenderManager()

    def _raise_existing_window():
        window.showNormal()
        window.raise_()
        window.activateWindow()

    instance_guard.set_raise_callback(_raise_existing_window)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
