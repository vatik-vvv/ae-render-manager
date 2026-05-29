import json
import os
import re
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ae_paths import detect_ae_installs, get_aerender_path, get_max_parallel
from ae_render_settings import USE_QUEUE_RS, get_rs_template_options, resolve_frame_range
from app_paths import config_path
from parallel_pool import ParallelRenderWorker
from queue_finalize import handle_queue_finished, run_system_sleep
from queue_job_source import QueueJobBridge
from ui_widgets import AepDropList
from render_progress_tracker import clear_jobs, is_log_frozen, poll_all
from render_runner import stop_all_renders, stop_render
from scan_worker import ScanWorker
from telegram_notifier import check_preview_dependencies, reload_config, send_message
from ui_theme import (
    RENDER_PROGRESS_ROLE,
    StatusProgressDelegate,
    apply_action_buttons,
    style_log_panel,
    style_muted_label,
)

CONFIG_FILE = config_path()

COL_ENABLED = 0
COL_AEP = 1
COL_RQ = 2
COL_COMP = 3
COL_START = 4
COL_END = 5
COL_TOTAL = 6
COL_SKIP = 7
COL_PROXY = 8
COL_RS = 9
COL_OUTPUT = 10
COL_STATUS = 11
COL_SEND2BOT = 12
COL_START_TIME = 13
COL_END_TIME = 14
COL_DURATION = 15
QUEUE_COL_COUNT = 16

QUEUE_DEFAULT_COL_WIDTHS = (
    36, 220, 40, 140, 50, 50, 44, 40, 44, 130, 220, 88, 58, 72, 72, 64,
)

MODE_FULL_QUEUE = "Full AE queue"
TOGGLE_COLUMNS = {COL_ENABLED, COL_SKIP, COL_PROXY}

# AE scan_render_queue.jsx status labels → manager queue status
_AE_SCAN_TO_MANAGER_STATUS = {
    "QUEUED": "Pending",
    "NEEDS_OUTPUT": "Pending",
    "UNQUEUED": "Pending",
    "DONE": "Completed",
    "RENDERING": "Running",
    "ERR_STOPPED": "Failed",
    "USER_STOPPED": "Stopped",
}

TRANSLATIONS = {
    "en": {
        "title": "After Effects Render Manager",
        "zone1": "1 - After Effects environment",
        "browse_ae": "Browse aerender…",
        "save_settings": "Save settings",
        "check_env": "Check aerender",
        "telegram": "Telegram…",
        "language": "RU",
        "zone2": "2 - Projects (.aep)",
        "add_aep": "Add AEP",
        "remove_aep": "Remove",
        "scan_selected": "Scan selected → queue",
        "scan_all": "Scan all → queue",
        "add_full_queue": "Add full project queue",
        "zone5": "3 - Render queue",
        "max_parallel": "Parallel:",
        "start": "Start render",
        "stop": "Stop",
        "remove_queue": "Remove selected",
        "queue_headers": [
            "On", "AEP", "RQ#", "Comp", "Start", "End", "Total", "Skip", "Proxy",
            "RS preset", "Output", "Status", "Send2Bot",
            "Start", "End", "Render time",
        ],
        "queue_hint": (
            "Scan an AEP to load comps from its AE Render Queue. "
            "RS preset «Use queue» keeps skip/proxy/output from the .aep (recommended with Skip). "
            "Parallel runs separate aerender jobs (different comps/AEPs). "
            "Right-click queue rows to move up/down."
        ),
        "confirm_stop": "Stop all active renders?",
        "duplicate": "Project already in queue.",
        "duplicate_rq": "Render queue item already in table.",
        "render_progress_idle": "Render progress: idle",
        "render_progress": "Job {cur}/{total}: {name} — {status} ({pct}%)",
        "reset_status": "Reset status",
        "reset_status_all": "Reset status (all rows)",
        "reset_status_log": "Reset status on {n} queue row(s).",
        "cannot_edit_while_rendering": "Cannot edit queue while rendering.",
        "cannot_edit_running_row": "Cannot edit the row that is currently rendering.",
        "drop_aep_added": "Added {n} project(s) via drag-and-drop.",
        "move_up": "Move up",
        "move_down": "Move down",
        "move_queue_log": "Moved {n} queue row(s).",
        "sleep_on_finish": "Sleep entire PC when queue finishes",
        "confirm_sleep": (
            "All enabled renders finished and frames are on disk.\n\n"
            "Put the whole computer to sleep now? (The app will suspend with Windows — "
            "this is not the same as closing only the render manager.)"
        ),
        "confirm_sleep_title": "Sleep computer?",
    },
    "ru": {
        "title": "Менеджер рендера After Effects",
        "zone1": "1 - Окружение After Effects",
        "browse_ae": "Обзор aerender…",
        "save_settings": "Сохранить",
        "check_env": "Проверить aerender",
        "telegram": "Telegram…",
        "language": "EN",
        "zone2": "2 - Проекты (.aep)",
        "add_aep": "Добавить AEP",
        "remove_aep": "Удалить",
        "scan_selected": "Скан выбранного → очередь",
        "scan_all": "Скан всех → очередь",
        "add_full_queue": "Вся очередь проекта",
        "zone5": "3 - Очередь рендера",
        "max_parallel": "Параллельно:",
        "start": "Старт",
        "stop": "Стоп",
        "remove_queue": "Удалить выбранные",
        "queue_headers": [
            "Вкл", "AEP", "RQ#", "Comp", "Start", "End", "Total", "Skip", "Proxy",
            "RS", "Output", "Статус", "Send2Bot",
            "Начало", "Конец", "Время рендера",
        ],
        "queue_hint": (
            "Сканируйте AEP для загрузки comps из Render Queue. "
            "«Use queue» сохраняет skip/proxy/output из .aep (рекомендуется с Skip). "
            "Parallel — отдельные aerender для разных comps/AEP. "
            "ПКМ по строке — выше/ниже."
        ),
        "confirm_stop": "Остановить все активные рендеры?",
        "duplicate": "Проект уже в очереди.",
        "duplicate_rq": "Пункт Render Queue уже в таблице.",
        "render_progress_idle": "Прогресс рендера: ожидание",
        "render_progress": "Задача {cur}/{total}: {name} — {status} ({pct}%)",
        "reset_status": "Сбросить статус",
        "reset_status_all": "Сбросить статус (все строки)",
        "reset_status_log": "Статус сброшен для {n} строк(и) очереди.",
        "cannot_edit_while_rendering": "Нельзя менять очередь во время рендера.",
        "cannot_edit_running_row": "Нельзя менять строку, которая сейчас рендерится.",
        "drop_aep_added": "Добавлено проектов перетаскиванием: {n}.",
        "move_up": "Выше",
        "move_down": "Ниже",
        "move_queue_log": "Перемещено строк: {n}.",
        "sleep_on_finish": "Усыпить весь ПК после очереди",
        "confirm_sleep": (
            "Все включённые рендеры завершены, кадры на диске.\n\n"
            "Усыпить весь компьютер? (Приложение уйдёт в сон вместе с Windows — "
            "это не закрытие только менеджера рендера.)"
        ),
        "confirm_sleep_title": "Усыпить компьютер?",
    },
}


class RenderManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_language = "en"
        self.initialized = False
        self._saving_state = False
        self.queue_thread = None
        self.scan_thread = None
        self._scan_queue = []
        self._scan_added = 0
        self._jobs_total = 0
        self._jobs_done = 0
        self._active_render_row = -1
        self._queue_bridge = None
        self._queue_snapshot_lock = threading.Lock()
        self._queue_jobs_snapshot = []
        self._queue_all_finished = False
        self._writing_disk_row = -1
        self._writing_disk_counts = {}
        self._active_render_ratio = 0.0
        self._progress_ui_row = -1
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(200)
        self._progress_timer.timeout.connect(self._tick_render_progress)
        self._build_ui()
        self._load_state()
        self.initialized = True
        self._update_aep_remove_btn()
        self._update_queue_remove_btn()
        self._apply_language()

    def _queue_rendering(self):
        return bool(self.queue_thread and self.queue_thread.isRunning())

    def _update_queue_remove_btn(self):
        rows = self._selected_queue_rows()
        if not rows:
            self.remove_queue_btn.setEnabled(False)
            return
        if self._queue_rendering():
            self.remove_queue_btn.setEnabled(
                all(self._is_queue_row_editable(r) for r in rows)
            )
        else:
            self.remove_queue_btn.setEnabled(True)

    def _update_aep_remove_btn(self):
        self.remove_aep_btn.setEnabled(bool(self.aep_list.selectedItems()))

    def tr(self, key):
        return TRANSLATIONS[self.current_language].get(key, key)

    @staticmethod
    def _format_elapsed_seconds(seconds):
        total = int(max(0, round(seconds)))
        minutes, sec = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{sec:02d}"
        return f"{minutes}:{sec:02d}"

    @classmethod
    def _render_time_between(cls, start_text, end_text):
        """Wall-clock elapsed from HH:MM:SS start/end (same day, +24h if end wraps)."""
        start_text = (start_text or "").strip()
        end_text = (end_text or "").strip()
        if not start_text or not end_text:
            return ""
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t0 = datetime.strptime(start_text, fmt)
                t1 = datetime.strptime(end_text, fmt)
                base = datetime(2000, 1, 1)
                delta = (base.replace(hour=t1.hour, minute=t1.minute, second=t1.second)
                         - base.replace(hour=t0.hour, minute=t0.minute, second=t0.second))
                seconds = delta.total_seconds()
                if seconds < 0:
                    seconds += 24 * 3600
                return cls._format_elapsed_seconds(seconds)
            except ValueError:
                continue
        return ""

    def _set_row_render_time(self, row, start_text=None, end_text=None):
        st_item = self.queue_table.item(row, COL_START_TIME)
        et_item = self.queue_table.item(row, COL_END_TIME)
        start_val = (start_text if start_text is not None else (st_item.text() if st_item else "")).strip()
        end_val = (end_text if end_text is not None else (et_item.text() if et_item else "")).strip()
        render_time = self._render_time_between(start_val, end_val)
        dur_item = self.queue_table.item(row, COL_DURATION)
        if dur_item:
            dur_item.setText(render_time)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        self._splitter_main = QSplitter(Qt.Vertical)
        root.addWidget(self._splitter_main)

        z1 = QGroupBox()
        self.zone1_box = z1
        z1l = QHBoxLayout(z1)
        self.ae_combo = QComboBox()
        self.ae_combo.setMinimumWidth(520)
        self.ae_combo.setEditable(True)
        self.ae_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        line_edit = self.ae_combo.lineEdit()
        if line_edit:
            line_edit.setReadOnly(True)
            line_edit.setPlaceholderText("Path to aerender.exe")
        for label, aerender, afterfx in detect_ae_installs():
            self._add_ae_combo_entry(aerender, afterfx, label)
        z1l.addWidget(self.ae_combo, 1)
        self.browse_ae_btn = QPushButton()
        self.save_btn = QPushButton()
        self.check_btn = QPushButton()
        self.telegram_btn = QPushButton()
        self.lang_btn = QPushButton()
        for btn in (self.browse_ae_btn, self.save_btn, self.check_btn, self.telegram_btn, self.lang_btn):
            z1l.addWidget(btn)
        self.browse_ae_btn.clicked.connect(self._browse_aerender)
        self.save_btn.clicked.connect(self.save_state)
        self.check_btn.clicked.connect(self._check_aerender)
        self.telegram_btn.clicked.connect(self._open_telegram_dialog)
        self.lang_btn.clicked.connect(self._switch_language)

        z1_host = QWidget()
        z1_layout = QVBoxLayout(z1_host)
        z1_layout.setContentsMargins(0, 0, 0, 0)
        z1_layout.addWidget(z1)
        self._splitter_main.addWidget(z1_host)

        self._splitter_content = QSplitter(Qt.Vertical)

        z2 = QGroupBox()
        self.zone2_box = z2
        z2l = QVBoxLayout(z2)
        row2 = QHBoxLayout()
        self.add_aep_btn = QPushButton()
        self.remove_aep_btn = QPushButton()
        self.remove_aep_btn.setEnabled(False)
        self.scan_selected_btn = QPushButton()
        self.scan_all_btn = QPushButton()
        self.add_full_queue_btn = QPushButton()
        row2.addWidget(self.add_aep_btn)
        row2.addWidget(self.remove_aep_btn)
        row2.addWidget(self.scan_selected_btn)
        row2.addWidget(self.scan_all_btn)
        row2.addWidget(self.add_full_queue_btn)
        row2.addStretch()
        z2l.addLayout(row2)
        self.aep_list = AepDropList(on_aep_paths=self._add_aep_paths)
        self.aep_list.itemSelectionChanged.connect(self._update_aep_remove_btn)
        z2l.addWidget(self.aep_list)
        self.queue_hint_label = QLabel()
        self.queue_hint_label.setWordWrap(True)
        z2l.addWidget(self.queue_hint_label)
        self.add_aep_btn.clicked.connect(self._add_aep_files)
        self.remove_aep_btn.clicked.connect(self._remove_aep)
        self.scan_selected_btn.clicked.connect(self._scan_selected_aep)
        self.scan_all_btn.clicked.connect(self._scan_all_aeps)
        self.add_full_queue_btn.clicked.connect(self._add_full_queue_row)

        z2_host = QWidget()
        z2_layout = QVBoxLayout(z2_host)
        z2_layout.setContentsMargins(0, 0, 0, 0)
        z2_layout.addWidget(z2)
        self._splitter_content.addWidget(z2_host)

        self._splitter_queue_log = QSplitter(Qt.Vertical)

        z5 = QGroupBox()
        self.zone5_box = z5
        z5l = QVBoxLayout(z5)
        ctrl = QHBoxLayout()
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 8)
        self.max_parallel_spin.setValue(get_max_parallel())
        self.max_parallel_spin.setToolTip(
            "Each parallel job starts a separate aerender instance (high RAM use)."
        )
        self.start_btn = QPushButton()
        self.stop_btn = QPushButton()
        self.remove_queue_btn = QPushButton()
        self.remove_queue_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.parallel_label = QLabel()
        ctrl.addWidget(self.parallel_label)
        ctrl.addWidget(self.max_parallel_spin)
        ctrl.addWidget(self.start_btn)
        ctrl.addWidget(self.stop_btn)
        ctrl.addWidget(self.remove_queue_btn)
        self.sleep_on_finish_chk = QCheckBox()
        self.sleep_on_finish_chk.setToolTip(
            "When the queue completes successfully, Windows will suspend the entire PC "
            "(monitor off, app frozen until wake). Not used when you press Stop."
        )
        self.sleep_on_finish_chk.toggled.connect(lambda _v: self.save_state())
        ctrl.addWidget(self.sleep_on_finish_chk)
        ctrl.addStretch()
        z5l.addLayout(ctrl)
        self.render_progress_label = QLabel()
        self.render_progress_bar = QProgressBar()
        self.render_progress_bar.setRange(0, 100)
        self.render_progress_bar.setValue(0)
        self.render_progress_bar.setTextVisible(True)
        self.render_progress_bar.setFormat("%p%")
        z5l.addWidget(self.render_progress_label)
        z5l.addWidget(self.render_progress_bar)
        self.queue_table = QTableWidget(0, QUEUE_COL_COUNT)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        header = self.queue_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setMinimumSectionSize(28)
        for col in range(QUEUE_COL_COUNT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate(QUEUE_DEFAULT_COL_WIDTHS):
            self.queue_table.setColumnWidth(col, width)
        header.sectionResized.connect(self._on_queue_column_resized)
        self.queue_table.cellChanged.connect(self._on_queue_cell_changed)
        self.queue_table.cellClicked.connect(self._on_queue_cell_clicked)
        self.queue_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_table.customContextMenuRequested.connect(self._queue_context_menu)
        self.status_progress_delegate = StatusProgressDelegate(self.queue_table)
        self.queue_table.setItemDelegateForColumn(COL_STATUS, self.status_progress_delegate)
        self.queue_table.itemSelectionChanged.connect(self._update_queue_remove_btn)
        z5l.addWidget(self.queue_table)
        self.start_btn.clicked.connect(self.start_render)
        self.stop_btn.clicked.connect(self._stop_render)
        self.remove_queue_btn.clicked.connect(self._remove_selected_queue_rows)

        z5_host = QWidget()
        z5_layout = QVBoxLayout(z5_host)
        z5_layout.setContentsMargins(0, 0, 0, 0)
        z5_layout.addWidget(z5)
        self._splitter_queue_log.addWidget(z5_host)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(80)
        log_host = QWidget()
        log_layout = QVBoxLayout(log_host)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("Log")
        log_label.setObjectName("logZoneLabel")
        style_muted_label(log_label)
        log_layout.addWidget(log_label)
        log_layout.addWidget(self.log_output)
        self._splitter_queue_log.addWidget(log_host)

        self._splitter_content.addWidget(self._splitter_queue_log)
        self._splitter_main.addWidget(self._splitter_content)

        self._splitter_main.setStretchFactor(0, 0)
        self._splitter_main.setStretchFactor(1, 1)
        self._splitter_content.setStretchFactor(0, 1)
        self._splitter_content.setStretchFactor(1, 3)
        self._splitter_queue_log.setStretchFactor(0, 4)
        self._splitter_queue_log.setStretchFactor(1, 1)
        self._splitter_main.setSizes([72, 700])
        self._splitter_content.setSizes([160, 520])
        self._splitter_queue_log.setSizes([380, 140])
        self._splitter_main.splitterMoved.connect(self._on_splitter_moved)
        self._splitter_content.splitterMoved.connect(self._on_splitter_moved)
        self._splitter_queue_log.splitterMoved.connect(self._on_splitter_moved)

        style_muted_label(self.queue_hint_label)
        style_muted_label(self.render_progress_label)
        self.render_progress_label.setStyleSheet("color: #00BFFF; font-size: 12px;")
        style_log_panel(self.log_output)
        apply_action_buttons(self.start_btn, self.stop_btn, self.remove_queue_btn)
        self.aep_list.setAlternatingRowColors(True)
        self._update_global_progress(0, 0, -1, "")

    def _apply_language(self):
        t = TRANSLATIONS[self.current_language]
        self.setWindowTitle(t["title"])
        self.zone1_box.setTitle(t["zone1"])
        self.zone2_box.setTitle(t["zone2"])
        self.zone5_box.setTitle(t["zone5"])
        self.parallel_label.setText(t["max_parallel"])
        self.browse_ae_btn.setText(t["browse_ae"])
        self.save_btn.setText(t["save_settings"])
        self.check_btn.setText(t["check_env"])
        self.telegram_btn.setText(t["telegram"])
        self.lang_btn.setText(t["language"])
        self.add_aep_btn.setText(t["add_aep"])
        self.remove_aep_btn.setText(t["remove_aep"])
        self.scan_selected_btn.setText(t["scan_selected"])
        self.scan_all_btn.setText(t["scan_all"])
        self.add_full_queue_btn.setText(t["add_full_queue"])
        self.start_btn.setText(t["start"])
        self.stop_btn.setText(t["stop"])
        self.remove_queue_btn.setText(t["remove_queue"])
        self.sleep_on_finish_chk.setText(t["sleep_on_finish"])
        self.queue_hint_label.setText(t["queue_hint"])
        self.queue_table.setHorizontalHeaderLabels(t["queue_headers"])

    def _switch_language(self):
        self.current_language = "ru" if self.current_language == "en" else "en"
        self._apply_language()
        self.save_state()

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {message}")
        if "aerender.exe has closed" in message or "Still rendering to disk" in message:
            row = self._active_render_row
            if row >= 0:
                self._writing_disk_row = row
                match = re.search(
                    r"Still rendering to disk…\s*(\d+)/(\d+)", message
                )
                if match:
                    self._writing_disk_counts[row] = (
                        int(match.group(1)),
                        int(match.group(2)),
                    )
        elif "Finished render:" in message or "Queue finished" in message:
            self._writing_disk_row = -1
            self._writing_disk_counts.clear()

    def _add_ae_combo_entry(self, aerender, afterfx="", label=""):
        aerender = os.path.normpath(aerender)
        for i in range(self.ae_combo.count()):
            data = self.ae_combo.itemData(i)
            if isinstance(data, dict) and os.path.normcase(data.get("aerender", "")) == os.path.normcase(aerender):
                return i
        self.ae_combo.addItem(aerender, {"aerender": aerender, "afterfx": afterfx or ""})
        idx = self.ae_combo.count() - 1
        if label:
            self.ae_combo.setItemData(idx, label, Qt.ItemDataRole.ToolTipRole)
        return idx

    def _set_ae_combo_path(self, aerender, afterfx=""):
        if not aerender:
            return
        idx = self._add_ae_combo_entry(aerender, afterfx)
        self.ae_combo.setCurrentIndex(idx)

    def _browse_aerender(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select aerender.exe", "", "Executable (aerender.exe);;All (*.*)"
        )
        if path:
            support = os.path.dirname(path)
            afterfx = ""
            for name in ("AfterFX.com", "AfterFX.exe"):
                cand = os.path.join(support, name)
                if os.path.isfile(cand):
                    afterfx = cand
                    break
            label = os.path.basename(os.path.dirname(os.path.dirname(path)))
            self._set_ae_combo_path(path, afterfx)
            if label:
                self.ae_combo.setItemData(
                    self.ae_combo.currentIndex(),
                    label,
                    Qt.ItemDataRole.ToolTipRole,
                )

    def _selected_ae_paths(self):
        data = self.ae_combo.currentData()
        if isinstance(data, dict):
            aerender = (data.get("aerender") or "").strip()
            afterfx = (data.get("afterfx") or "").strip()
            if aerender:
                return aerender, afterfx
        text = self.ae_combo.currentText().strip()
        if text and os.path.isfile(text):
            return os.path.normpath(text), ""
        return "", ""

    def _check_aerender(self):
        aerender, _ = self._selected_ae_paths()
        if not aerender or not os.path.isfile(aerender):
            aerender = get_aerender_path()
        if not os.path.isfile(aerender):
            self.log(f"aerender not found: {aerender}")
            return
        try:
            result = subprocess.run(
                [aerender, "-version"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            out = (result.stdout or result.stderr or "").strip()
            self.log(f"aerender OK: {out or 'no output'}")
        except Exception as e:
            self.log(f"aerender check failed: {e}")

    def _open_telegram_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Telegram")
        form = QFormLayout(dlg)
        token_edit = QLineEdit()
        chat_edit = QLineEdit()
        preview_spin = QSpinBox()
        preview_spin.setRange(64, 8000)
        preview_spin.setValue(2000)
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    cfg = json.load(f)
                tg = cfg.get("telegram", {})
                token_edit.setText(tg.get("bot_token", ""))
                chat_edit.setText(str(tg.get("chat_id", "")))
                preview_spin.setValue(int(tg.get("preview_max_side", 2000)))
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        form.addRow("Bot token:", token_edit)
        form.addRow("Chat ID:", chat_edit)
        form.addRow("Preview max side:", preview_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        test_btn = QPushButton("Test")
        buttons.addButton(test_btn, QDialogButtonBox.ActionRole)
        form.addRow(buttons)

        def save_tg():
            data = {}
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            data.setdefault("telegram", {})
            data["telegram"]["bot_token"] = token_edit.text().strip()
            data["telegram"]["chat_id"] = chat_edit.text().strip()
            data["telegram"]["preview_max_side"] = preview_spin.value()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            reload_config()
            self.log("Telegram settings saved.")

        def test_tg():
            save_tg()
            ok, err = send_message("Test from AE Render Manager")
            self.log("Telegram test OK" if ok else f"Telegram test failed: {err}")

        test_btn.clicked.connect(test_tg)
        buttons.accepted.connect(lambda: (save_tg(), dlg.accept()))
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def _add_aep_paths(self, paths):
        added = 0
        for path in paths:
            if path and self.aep_list.findItems(path, Qt.MatchExactly) == []:
                self.aep_list.addItem(path)
                added += 1
        if added:
            self.save_state()
            self._update_aep_remove_btn()
            self.log(self.tr("drop_aep_added").format(n=added))

    def _add_aep_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select AEP", "", "After Effects (*.aep)")
        self._add_aep_paths(paths)

    def _remove_aep(self):
        for item in self.aep_list.selectedItems():
            self.aep_list.takeItem(self.aep_list.row(item))
        self.save_state()
        self._update_aep_remove_btn()

    def _queue_has_aep(self, aep_path):
        norm = os.path.normcase(os.path.abspath(aep_path))
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, COL_AEP)
            comp_item = self.queue_table.item(row, COL_COMP)
            comp = comp_item.text().strip() if comp_item else ""
            if comp == MODE_FULL_QUEUE and item and os.path.normcase(os.path.abspath(item.text())) == norm:
                return True
        return False

    def _queue_has_rq_item(self, aep_path, rq_index):
        norm = os.path.normcase(os.path.abspath(aep_path))
        rq_text = str(rq_index)
        for row in range(self.queue_table.rowCount()):
            aep_item = self.queue_table.item(row, COL_AEP)
            rq_item = self.queue_table.item(row, COL_RQ)
            if not aep_item or not rq_item:
                continue
            if rq_item.text().strip() != rq_text:
                continue
            if os.path.normcase(os.path.abspath(aep_item.text())) == norm:
                return True
        return False

    def _toggle_cell(self, row, col):
        item = self.queue_table.item(row, col)
        if not item:
            return
        cur = item.data(Qt.UserRole) or "1"
        new = "0" if cur == "1" else "1"
        self.queue_table.blockSignals(True)
        try:
            item.setData(Qt.UserRole, new)
            item.setText("✓" if new == "1" else "")
        finally:
            self.queue_table.blockSignals(False)
        if col == COL_SKIP and new == "1":
            combo = self.queue_table.cellWidget(row, COL_RS)
            if isinstance(combo, QComboBox):
                idx = combo.findText(USE_QUEUE_RS)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self.save_state()
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)

    def _set_toggle_item(self, row, col, enabled):
        item = QTableWidgetItem("✓" if enabled else "")
        item.setData(Qt.UserRole, "1" if enabled else "0")
        self.queue_table.setItem(row, col, item)

    @staticmethod
    def _frame_total_from_range(start_text, end_text, increment=1):
        start, end = resolve_frame_range(start_text, end_text)
        if start is None or end is None or end < start:
            return ""
        inc = max(1, int(increment or 1))
        return str((end - start) // inc + 1)

    def _set_queue_total_cell(self, row, start_text=None, end_text=None):
        if start_text is None:
            start_text = self._cell_text(row, COL_START)
        if end_text is None:
            end_text = self._cell_text(row, COL_END)
        total = self._frame_total_from_range(start_text, end_text)
        item = self.queue_table.item(row, COL_TOTAL)
        if item is None:
            item = QTableWidgetItem("")
            self.queue_table.setItem(row, COL_TOTAL, item)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setText(total)

    def _append_full_queue_row(self, aep_path):
        if self._queue_has_aep(aep_path):
            self.log(self.tr("duplicate"))
            return False
        self.queue_table.blockSignals(True)
        try:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            self._set_toggle_item(row, COL_ENABLED, True)
            self.queue_table.setItem(row, COL_AEP, QTableWidgetItem(aep_path))
            rq_item = QTableWidgetItem("")
            rq_item.setFlags(rq_item.flags() & ~Qt.ItemIsEditable)
            self.queue_table.setItem(row, COL_RQ, rq_item)
            comp_item = QTableWidgetItem(MODE_FULL_QUEUE)
            comp_item.setFlags(comp_item.flags() & ~Qt.ItemIsEditable)
            self.queue_table.setItem(row, COL_COMP, comp_item)
            for col in (COL_START, COL_END, COL_OUTPUT):
                empty = QTableWidgetItem("")
                empty.setFlags(empty.flags() & ~Qt.ItemIsEditable)
                self.queue_table.setItem(row, col, empty)
            self._set_queue_total_cell(row)
            rs_combo = self._make_rs_combo(USE_QUEUE_RS, "")
            self.queue_table.setCellWidget(row, COL_RS, rs_combo)
            self._set_toggle_item(row, COL_SKIP, False)
            self._set_toggle_item(row, COL_PROXY, False)
            self.queue_table.setItem(row, COL_STATUS, QTableWidgetItem("Pending"))
            self.queue_table.setItem(row, COL_SEND2BOT, QTableWidgetItem("0"))
            for col in (COL_START_TIME, COL_END_TIME, COL_DURATION):
                self.queue_table.setItem(row, col, QTableWidgetItem(""))
        finally:
            self.queue_table.blockSignals(False)
        self.save_state()
        return True

    def _append_scan_item_row(self, aep_path, item):
        rq_index = item.get("rq_index")
        if self._queue_has_rq_item(aep_path, rq_index):
            return False
        self.queue_table.blockSignals(True)
        try:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            enabled = item.get("render_enabled", True) and item.get("queueable", True)
            self._set_toggle_item(row, COL_ENABLED, enabled)
            self.queue_table.setItem(row, COL_AEP, QTableWidgetItem(aep_path))
            rq_cell = QTableWidgetItem(str(rq_index))
            rq_cell.setFlags(rq_cell.flags() & ~Qt.ItemIsEditable)
            self.queue_table.setItem(row, COL_RQ, rq_cell)
            comp_cell = QTableWidgetItem(item.get("comp", ""))
            comp_cell.setFlags(comp_cell.flags() & ~Qt.ItemIsEditable)
            self.queue_table.setItem(row, COL_COMP, comp_cell)
            self.queue_table.setItem(row, COL_START, QTableWidgetItem(str(item.get("start", ""))))
            self.queue_table.setItem(row, COL_END, QTableWidgetItem(str(item.get("end", ""))))
            self._set_queue_total_cell(
                row,
                str(item.get("start", "")),
                str(item.get("end", "")),
            )
            self._set_toggle_item(row, COL_SKIP, bool(item.get("skip_existing")))
            self._set_toggle_item(row, COL_PROXY, bool(item.get("use_proxy")))
            scanned_rs = item.get("rs_template", "") or ""
            if item.get("skip_existing"):
                rs_pick = USE_QUEUE_RS
            else:
                rs_pick = scanned_rs or USE_QUEUE_RS
            rs_combo = self._make_rs_combo(rs_pick, scanned_rs)
            rs_combo.setProperty("rs_scanned", scanned_rs)
            self.queue_table.setCellWidget(row, COL_RS, rs_combo)
            self.queue_table.setItem(row, COL_OUTPUT, QTableWidgetItem(item.get("output", "") or ""))
            scan_status = item.get("status", "Pending")
            self.queue_table.setItem(
                row,
                COL_STATUS,
                QTableWidgetItem(self._normalize_queue_status(scan_status)),
            )
            self.queue_table.setItem(row, COL_SEND2BOT, QTableWidgetItem("0"))
            for col in (COL_START_TIME, COL_END_TIME, COL_DURATION):
                self.queue_table.setItem(row, col, QTableWidgetItem(""))
        finally:
            self.queue_table.blockSignals(False)
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)
        return True

    def _add_full_queue_row(self):
        item = self.aep_list.currentItem()
        if not item:
            self.log("Select an AEP in the list first.")
            return
        aep = item.text()
        if self._append_full_queue_row(aep):
            self.log(f"Queued full project: {os.path.basename(aep)}")

    def _scan_selected_aep(self):
        item = self.aep_list.currentItem()
        if not item:
            self.log("Select an AEP in the list first.")
            return
        self._start_scan([item.text()])

    def _scan_all_aeps(self):
        paths = [self.aep_list.item(i).text() for i in range(self.aep_list.count())]
        paths = [p for p in paths if p]
        if not paths:
            self.log("No projects in list.")
            return
        self._start_scan(paths)

    def _start_scan(self, aep_paths):
        if self.scan_thread and self.scan_thread.isRunning():
            self.log("Scan already running.")
            return
        self._scan_queue = list(aep_paths)
        self._scan_added = 0
        self._scan_next()

    def _scan_next(self):
        if not self._scan_queue:
            self.log(f"Scan finished — added {self._scan_added} queue item(s).")
            self.scan_selected_btn.setEnabled(True)
            self.scan_all_btn.setEnabled(True)
            return
        aep_path = self._scan_queue.pop(0)
        self.log(f"Scanning render queue: {os.path.basename(aep_path)}")
        self.scan_selected_btn.setEnabled(False)
        self.scan_all_btn.setEnabled(False)
        self.scan_thread = ScanWorker(aep_path)
        self.scan_thread.log_signal.connect(self.log)
        self.scan_thread.finished_signal.connect(self._on_scan_finished)
        self.scan_thread.error_signal.connect(self._on_scan_error)
        self.scan_thread.start()

    def _on_scan_finished(self, aep_path, data):
        added = 0
        for item in data.get("items", []):
            if not item.get("queueable", True):
                continue
            if self._append_scan_item_row(aep_path, item):
                added += 1
        self._scan_added += added
        self.log(
            f"Scan OK: {os.path.basename(aep_path)} — "
            f"{added} item(s) added ({len(data.get('items', []))} total in AE queue)."
        )
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)
        self._scan_next()

    def _on_scan_error(self, aep_path, message):
        self.log(f"Scan failed ({os.path.basename(aep_path)}): {message}")
        from app_paths import scan_args_path, scan_work_dir

        self.log(f"  Work folder: {scan_work_dir()}")
        self.log(f"  Args file: {scan_args_path()}")
        self.log(
            "  Manual fallback: open the .aep in AE, then File → Scripts → "
            "Run Script File → scan_render_queue.jsx in that folder."
        )
        self._scan_next()

    def _on_queue_cell_clicked(self, row, col):
        if not self.initialized or col not in TOGGLE_COLUMNS:
            return
        if self._queue_rendering() and not self._is_queue_row_editable(row):
            self.log(self.tr("cannot_edit_running_row"))
            return
        self._toggle_cell(row, col)

    def _selected_queue_rows(self):
        return sorted({index.row() for index in self.queue_table.selectedIndexes()})

    def _is_queue_row_editable(self, row):
        if not self._queue_rendering():
            return True
        return (self._cell_text(row, COL_STATUS) or "").strip() != "Running"

    def _lock_queue_row(self, row, locked):
        editable = Qt.ItemFlag.ItemIsEditable
        for col in range(QUEUE_COL_COUNT):
            item = self.queue_table.item(row, col)
            if not item:
                continue
            flags = item.flags()
            if locked or col == COL_TOTAL:
                item.setFlags(flags & ~editable)
            else:
                item.setFlags(flags | editable)
        combo = self.queue_table.cellWidget(row, COL_RS)
        if isinstance(combo, QComboBox):
            combo.setEnabled(not locked)

    def _queue_context_menu(self, pos):
        rows = self._selected_queue_rows()
        if not rows:
            row = self.queue_table.rowAt(pos.y())
            if row >= 0:
                rows = [row]
        if self._queue_rendering():
            rows = [r for r in rows if self._is_queue_row_editable(r)]
        if not rows:
            if self._queue_rendering():
                self.log(self.tr("cannot_edit_running_row"))
            return
        menu = QMenu(self)
        move_up = menu.addAction(self.tr("move_up"))
        move_up.triggered.connect(lambda _checked=False, r=rows: self._move_queue_rows(r, -1))
        move_down = menu.addAction(self.tr("move_down"))
        move_down.triggered.connect(lambda _checked=False, r=rows: self._move_queue_rows(r, 1))
        menu.addSeparator()
        reset_sel = menu.addAction(self.tr("reset_status"))
        reset_sel.triggered.connect(lambda _checked=False, r=rows: self._reset_queue_status(r))
        reset_all = menu.addAction(self.tr("reset_status_all"))
        all_rows = list(range(self.queue_table.rowCount()))
        reset_all.triggered.connect(
            lambda _checked=False, r=all_rows: self._reset_queue_status(r)
        )
        menu.exec(self.queue_table.viewport().mapToGlobal(pos))

    def _move_queue_rows(self, rows, direction):
        rows = sorted({r for r in rows if 0 <= r < self.queue_table.rowCount()})
        if self._queue_rendering():
            rows = [r for r in rows if self._is_queue_row_editable(r)]
            if not rows:
                self.log(self.tr("cannot_edit_running_row"))
                return
        if not rows or direction not in (-1, 1):
            return
        n = self.queue_table.rowCount()
        if direction < 0 and rows[0] == 0:
            return
        if direction > 0 and rows[-1] >= n - 1:
            return
        self.queue_table.blockSignals(True)
        try:
            if direction < 0:
                for row in rows:
                    self._swap_queue_row_with(row, row - 1)
            else:
                for row in reversed(rows):
                    self._swap_queue_row_with(row, row + 1)
        finally:
            self.queue_table.blockSignals(False)
        self.save_state()
        self.log(self.tr("move_queue_log").format(n=len(rows)))

    def _swap_queue_row_with(self, row_a, row_b):
        entry_a = self._queue_row_to_entry(row_a)
        entry_b = self._queue_row_to_entry(row_b)
        self._apply_queue_entry_at_row(row_a, entry_b)
        self._apply_queue_entry_at_row(row_b, entry_a)

    def _apply_queue_entry_at_row(self, row, entry):
        """Overwrite an existing queue row from a saved entry dict."""
        aep = entry.get("aep", "")
        self.queue_table.setItem(row, COL_AEP, QTableWidgetItem(aep))
        rq_cell = QTableWidgetItem(str(entry.get("rq_index", "")))
        rq_cell.setFlags(rq_cell.flags() & ~Qt.ItemIsEditable)
        self.queue_table.setItem(row, COL_RQ, rq_cell)
        comp_cell = QTableWidgetItem(entry.get("comp", ""))
        comp_cell.setFlags(comp_cell.flags() & ~Qt.ItemIsEditable)
        self.queue_table.setItem(row, COL_COMP, comp_cell)
        self.queue_table.setItem(
            row, COL_START, QTableWidgetItem(str(entry.get("start_frame", "")))
        )
        self.queue_table.setItem(
            row, COL_END, QTableWidgetItem(str(entry.get("end_frame", "")))
        )
        self._set_queue_total_cell(
            row,
            str(entry.get("start_frame", "")),
            str(entry.get("end_frame", "")),
        )
        self._set_toggle_item(row, COL_ENABLED, self._flag_is_on(entry.get("enabled", "1")))
        self._set_toggle_item(row, COL_SKIP, self._flag_is_on(entry.get("skip", "0")))
        self._set_toggle_item(row, COL_PROXY, self._flag_is_on(entry.get("use_proxy", "0")))
        scanned_rs = entry.get("rs_template_scanned", entry.get("rs_template", "")) or ""
        saved_rs = entry.get("rs_template", "") or USE_QUEUE_RS
        rs_combo = self._make_rs_combo(saved_rs, scanned_rs)
        rs_combo.setProperty("rs_scanned", scanned_rs)
        self.queue_table.setCellWidget(row, COL_RS, rs_combo)
        self.queue_table.setItem(
            row, COL_OUTPUT, QTableWidgetItem(entry.get("output_path", "") or "")
        )
        self.queue_table.setItem(
            row,
            COL_STATUS,
            QTableWidgetItem(self._normalize_queue_status(entry.get("status", "Pending"))),
        )
        self.queue_table.setItem(row, COL_SEND2BOT, QTableWidgetItem(str(entry.get("send2bot", "0"))))
        self.queue_table.setItem(row, COL_START_TIME, QTableWidgetItem(entry.get("start_time", "")))
        self.queue_table.setItem(row, COL_END_TIME, QTableWidgetItem(entry.get("end_time", "")))
        start_t = entry.get("start_time", "")
        end_t = entry.get("end_time", "")
        duration = entry.get("duration", "") or self._render_time_between(start_t, end_t)
        self.queue_table.setItem(row, COL_DURATION, QTableWidgetItem(duration))

    def _reset_queue_status(self, rows):
        rows = sorted({r for r in rows if 0 <= r < self.queue_table.rowCount()})
        if self._queue_rendering():
            rows = [r for r in rows if self._is_queue_row_editable(r)]
        if not rows:
            self.log("Select one or more rows in the render queue.")
            return
        self.queue_table.blockSignals(True)
        try:
            for row in rows:
                status_item = self.queue_table.item(row, COL_STATUS)
                if status_item:
                    status_item.setText("Pending")
                    status_item.setData(RENDER_PROGRESS_ROLE, None)
                for col in (COL_START_TIME, COL_END_TIME, COL_DURATION):
                    cell = self.queue_table.item(row, col)
                    if cell:
                        cell.setText("")
                self.queue_table.update(self.queue_table.model().index(row, COL_STATUS))
        finally:
            self.queue_table.blockSignals(False)
        self.save_state()
        self.log(self.tr("reset_status_log").format(n=len(rows)))
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)

    def _remove_selected_queue_rows(self):
        rows = sorted(self._selected_queue_rows(), reverse=True)
        if self._queue_rendering():
            rows = [r for r in rows if self._is_queue_row_editable(r)]
            if not rows:
                self.log(self.tr("cannot_edit_running_row"))
                return
        if not rows:
            self.log("Select one or more rows in the render queue.")
            return
        self.queue_table.blockSignals(True)
        try:
            for row in rows:
                self.queue_table.removeRow(row)
        finally:
            self.queue_table.blockSignals(False)
        self.save_state()
        self.log(f"Removed {len(rows)} item(s) from queue.")
        self._update_queue_remove_btn()

    def _on_queue_cell_changed(self, row, col):
        if not self.initialized or self.queue_table.signalsBlocked():
            return
        if self._queue_rendering() and not self._is_queue_row_editable(row):
            return
        if col in (COL_START, COL_END):
            self.queue_table.blockSignals(True)
            try:
                self._set_queue_total_cell(row)
            finally:
                self.queue_table.blockSignals(False)
        if col == COL_ENABLED:
            if self._queue_rendering():
                self.refresh_queue_snapshot(hot_append=True)
            return
        self.save_state()
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)

    def _cell_text(self, row, col):
        item = self.queue_table.item(row, col)
        return item.text().strip() if item else ""

    @staticmethod
    def _flag_is_on(value):
        if value is True or value == 1:
            return True
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return False

    def _cell_flag(self, row, col):
        item = self.queue_table.item(row, col)
        default = "1" if col == COL_ENABLED else "0"
        if not item:
            return default
        val = item.data(Qt.UserRole)
        if val is None:
            if col in TOGGLE_COLUMNS:
                text = item.text().strip()
                if text in ("✓", "✔"):
                    return "1"
                if text:
                    return "0"
            return default
        return "1" if self._flag_is_on(val) else "0"

    @staticmethod
    def _normalize_queue_status(status):
        """Map AE RQ scan labels (QUEUED, …) to manager statuses used for Start."""
        raw = (status or "Pending").strip()
        if not raw:
            return "Pending"
        key = re.sub(r"[^A-Z0-9_]", "", raw.upper().replace(" ", "_"))
        mapped = _AE_SCAN_TO_MANAGER_STATUS.get(key)
        if mapped:
            return mapped
        if key in ("QUEUED", "NEEDS_OUTPUT", "UNQUEUED", "NEEDSOUTPUT"):
            return "Pending"
        return raw

    def _queue_row_status(self, row):
        return self._normalize_queue_status(self._cell_text(row, COL_STATUS) or "Pending")

    def _is_active_render_status(self, status):
        norm = self._normalize_queue_status(status)
        return norm.startswith("Running") or norm.startswith("Writing") or (
            (status or "").strip().upper() == "RENDERING"
        )

    def _is_runnable_for_start(self, status):
        norm = self._normalize_queue_status(status)
        if self._is_active_render_status(status):
            return False
        if norm in ("Completed", "Skipped"):
            return False
        if norm in ("Pending", "Failed", "Incomplete", "Stopped", ""):
            return True
        key = re.sub(r"[^A-Z0-9_]", "", (status or "").strip().upper().replace(" ", "_"))
        return key in ("QUEUED", "NEEDS_OUTPUT", "UNQUEUED", "NEEDSOUTPUT")

    def _is_runnable_for_hot_append(self, status):
        norm = self._normalize_queue_status(status)
        if self._is_active_render_status(status):
            return False
        return norm in ("Pending", "")

    def _make_rs_combo(self, current="", scanned=""):
        combo = QComboBox()
        options = get_rs_template_options([current, scanned])
        combo.addItems(options)
        pick = (current or scanned or USE_QUEUE_RS).strip()
        if pick and pick not in options:
            combo.addItem(pick)
        idx = combo.findText(pick)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setToolTip(
            "Use queue = settings from AE render queue item (skip, proxies, output path)"
        )
        combo.currentTextChanged.connect(lambda _t: self.save_state())
        return combo

    def _cell_rs_template(self, row):
        widget = self.queue_table.cellWidget(row, COL_RS)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        return self._cell_text(row, COL_RS)

    def _rs_scanned_for_row(self, row):
        widget = self.queue_table.cellWidget(row, COL_RS)
        if isinstance(widget, QComboBox):
            return widget.property("rs_scanned") or ""
        item = self.queue_table.item(row, COL_RS)
        return item.data(Qt.UserRole) if item else ""

    def _queue_row_to_entry(self, row):
        return {
            "enabled": self._cell_flag(row, COL_ENABLED),
            "aep": self._cell_text(row, COL_AEP),
            "rq_index": self._cell_text(row, COL_RQ),
            "comp": self._cell_text(row, COL_COMP),
            "start_frame": self._cell_text(row, COL_START),
            "end_frame": self._cell_text(row, COL_END),
            "skip": self._cell_flag(row, COL_SKIP),
            "use_proxy": self._cell_flag(row, COL_PROXY),
            "rs_template": self._cell_rs_template(row),
            "rs_template_scanned": self._rs_scanned_for_row(row),
            "output_path": self._cell_text(row, COL_OUTPUT),
            "status": self._cell_text(row, COL_STATUS),
            "send2bot": self._cell_text(row, COL_SEND2BOT),
            "start_time": self._cell_text(row, COL_START_TIME),
            "end_time": self._cell_text(row, COL_END_TIME),
            "duration": self._cell_text(row, COL_DURATION),
            "full_queue": self._cell_text(row, COL_COMP) == MODE_FULL_QUEUE,
        }

    def _restore_queue_row(self, entry):
        aep = entry.get("aep", "")
        if not aep:
            return
        if entry.get("full_queue") or entry.get("comp") in (MODE_FULL_QUEUE, "AE Render Queue"):
            self._append_full_queue_row(aep)
            return
        if not entry.get("rq_index") and not entry.get("comp") and entry.get("mode"):
            self._append_full_queue_row(aep)
            return
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        entry.setdefault("aep", aep)
        self._apply_queue_entry_at_row(row, entry)

    def save_state(self):
        if not self.initialized or self._saving_state:
            return
        self._saving_state = True
        try:
            data = {}
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            aerender, afterfx = self._selected_ae_paths()
            data["aerender_path"] = aerender
            data["afterfx_path"] = afterfx
            data["max_parallel"] = self.max_parallel_spin.value()
            data["sleep_on_queue_finish"] = self.sleep_on_finish_chk.isChecked()
            data["language"] = self.current_language
            data["aep_files"] = [self.aep_list.item(i).text() for i in range(self.aep_list.count())]
            data["queue"] = [self._queue_row_to_entry(r) for r in range(self.queue_table.rowCount())]
            data.setdefault("ui", {})
            data["ui"]["width"] = self.width()
            data["ui"]["height"] = self.height()
            data["ui"]["splitter_main"] = self._splitter_main.sizes()
            data["ui"]["splitter_content"] = self._splitter_content.sizes()
            data["ui"]["splitter_queue_log"] = self._splitter_queue_log.sizes()
            data["ui"]["queue_column_widths"] = self._queue_column_widths()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log(f"Save error: {e}")
        finally:
            self._saving_state = False

    def _load_state(self):
        if not os.path.isfile(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self.current_language = data.get("language", "en")
        aerender = data.get("aerender_path", "")
        afterfx = data.get("afterfx_path", "")
        if aerender:
            self._set_ae_combo_path(aerender, afterfx)
        try:
            self.max_parallel_spin.setValue(int(data.get("max_parallel", 1)))
        except (TypeError, ValueError):
            pass
        self.sleep_on_finish_chk.setChecked(bool(data.get("sleep_on_queue_finish", False)))
        self.aep_list.clear()
        for path in data.get("aep_files", []):
            if path:
                self.aep_list.addItem(path)
        self.queue_table.blockSignals(True)
        try:
            self.queue_table.setRowCount(0)
            for entry in data.get("queue", []):
                if entry.get("aep"):
                    self._restore_queue_row(entry)
        finally:
            self.queue_table.blockSignals(False)
        ui = data.get("ui", {})
        w, h = ui.get("width"), ui.get("height")
        if w and h:
            self.resize(int(w), int(h))
        self._restore_splitter_sizes(ui)
        self._restore_queue_column_widths(ui)

    def _on_splitter_moved(self, _pos, _index):
        if self.initialized:
            self.save_state()

    def _on_queue_column_resized(self, _index, _old_size, _new_size):
        if self.initialized:
            self.save_state()

    def _queue_column_widths(self):
        return [self.queue_table.columnWidth(col) for col in range(QUEUE_COL_COUNT)]

    def _restore_queue_column_widths(self, ui):
        widths = ui.get("queue_column_widths")
        if isinstance(widths, list) and len(widths) == QUEUE_COL_COUNT - 1:
            widths = widths[:COL_TOTAL] + [QUEUE_DEFAULT_COL_WIDTHS[COL_TOTAL]] + widths[COL_TOTAL:]
        if not isinstance(widths, list) or len(widths) != QUEUE_COL_COUNT:
            return
        for col, width in enumerate(widths):
            try:
                self.queue_table.setColumnWidth(col, max(28, int(width)))
            except (TypeError, ValueError):
                pass

    def _restore_splitter_sizes(self, ui):
        if not ui:
            return
        for key, splitter in (
            ("splitter_main", self._splitter_main),
            ("splitter_content", self._splitter_content),
            ("splitter_queue_log", self._splitter_queue_log),
        ):
            sizes = ui.get(key)
            if isinstance(sizes, list) and len(sizes) == splitter.count():
                splitter.setSizes([int(s) for s in sizes])

    def _why_row_not_runnable(self, row, hot_append=False):
        if self._cell_flag(row, COL_ENABLED) == "0":
            return "disabled (On column)"
        raw_status = (self._cell_text(row, COL_STATUS) or "").strip()
        if hot_append:
            if not self._is_runnable_for_hot_append(raw_status):
                return f"status «{raw_status or 'empty'}» (hot-append needs Pending)"
        elif not self._is_runnable_for_start(raw_status):
            return f"status «{raw_status or 'empty'}» not runnable"
        aep = self._cell_text(row, COL_AEP)
        if not aep:
            return "missing AEP path"
        if not os.path.isfile(aep):
            return f"AEP not found: {aep}"
        return None

    def _queue_row_to_job(self, row, hot_append=False):
        if self._why_row_not_runnable(row, hot_append=hot_append):
            return None
        entry = self._queue_row_to_entry(row)
        aep_path = entry.get("aep", "")
        try:
            send2bot = int(entry.get("send2bot") or "0")
        except ValueError:
            send2bot = 0
        full_queue = entry.get("full_queue", False)
        rq_index = entry.get("rq_index")
        try:
            rq_index = int(rq_index) if rq_index not in (None, "") else None
        except ValueError:
            rq_index = None
        return {
            "row": row,
            "project": aep_path,
            "full_queue": full_queue,
            "send2bot": send2bot,
            "comp": "" if full_queue else entry.get("comp", ""),
            "rq_index": rq_index,
            "start_frame": entry.get("start_frame") or None,
            "end_frame": entry.get("end_frame") or None,
            "output_path": entry.get("output_path", ""),
            "rs_template": entry.get("rs_template", ""),
            "rs_template_scanned": entry.get("rs_template_scanned", ""),
            "skip_val": entry.get("skip", "0"),
            "use_proxy": entry.get("use_proxy", "0"),
        }

    def build_pending_queue_jobs(self, hot_append=False):
        jobs = []
        for row in range(self.queue_table.rowCount()):
            job = self._queue_row_to_job(row, hot_append=hot_append)
            if job:
                jobs.append(job)
        return jobs

    def refresh_queue_snapshot(self, hot_append=False):
        jobs = self.build_pending_queue_jobs(hot_append=hot_append)
        finished = self.all_enabled_queue_jobs_finished()
        with self._queue_snapshot_lock:
            self._queue_jobs_snapshot = jobs
            self._queue_all_finished = finished
        return jobs

    def get_queue_snapshot(self):
        with self._queue_snapshot_lock:
            return list(self._queue_jobs_snapshot)

    def get_queue_all_finished(self):
        with self._queue_snapshot_lock:
            return bool(self._queue_all_finished)

    def _log_no_runnable_jobs(self):
        enabled_rows = [
            row
            for row in range(self.queue_table.rowCount())
            if self._cell_flag(row, COL_ENABLED) == "1"
        ]
        if not enabled_rows:
            self.log("No enabled jobs in queue (check the On column).")
            return
        details = []
        for row in enabled_rows:
            why = self._why_row_not_runnable(row)
            if not why:
                continue
            label = self._cell_text(row, COL_COMP) or f"row {row + 1}"
            details.append(f"  • {label}: {why}")
        if details:
            self.log(
                f"No runnable jobs ({len(enabled_rows)} enabled):\n"
                + "\n".join(details)
                + "\n  QUEUED from AE scan is OK — if you see «AEP not found», fix the path. "
                "Otherwise rebuild the app from the latest sources."
            )
        else:
            self.log(
                f"No runnable jobs ({len(enabled_rows)} enabled) — unknown filter; "
                "try right-click → Reset status."
            )

    def all_enabled_queue_jobs_finished(self):
        any_enabled = False
        for row in range(self.queue_table.rowCount()):
            if self._cell_flag(row, COL_ENABLED) == "0":
                continue
            any_enabled = True
            status = self._normalize_queue_status(self._cell_text(row, COL_STATUS))
            if status not in ("Completed", "Skipped"):
                return False
        return any_enabled

    def collect_enabled_job_summaries(self):
        """Snapshot enabled queue rows for queue-finish Telegram and sleep checks."""
        summaries = []
        for row in range(self.queue_table.rowCount()):
            if self._cell_flag(row, COL_ENABLED) == "0":
                continue
            entry = self._queue_row_to_entry(row)
            start, end = resolve_frame_range(
                entry.get("start_frame"), entry.get("end_frame")
            )
            status = self._normalize_queue_status(entry.get("status"))
            summaries.append(
                {
                    "aep": entry.get("aep", ""),
                    "comp": entry.get("comp", ""),
                    "full_queue": entry.get("full_queue", False),
                    "start_frame": start,
                    "end_frame": end,
                    "increment": 1,
                    "output_path": entry.get("output_path", ""),
                    "status": status,
                    "duration_text": entry.get("duration", ""),
                }
            )
        return summaries

    def _on_queue_batch_updated(self, _pending, batch_total):
        if batch_total > 0:
            self._jobs_total = max(self._jobs_total, batch_total)

    def start_render(self):
        if self.queue_thread and self.queue_thread.isRunning():
            self.log("Queue already running.")
            return
        ok, msg, hint = check_preview_dependencies()
        if not ok:
            self.log(f"Preview deps: {msg} — {hint}")

        queue_data = self.refresh_queue_snapshot(hot_append=False)
        if not queue_data:
            self._log_no_runnable_jobs()
            return

        for job in queue_data:
            label = job.get("comp") or os.path.basename(job.get("project", ""))
            self.log(f"  → row {job['row'] + 1}: {label}")

        self._jobs_total = len(queue_data)
        self._jobs_done = 0
        self._active_render_row = -1
        self._active_render_ratio = 0.0
        self._clear_all_status_progress()
        self._update_global_progress(0, self._jobs_total, -1, "")

        self.save_state()
        self.log(
            f"Starting {len(queue_data)} job(s), parallel={self.max_parallel_spin.value()} "
            "(queue refreshes after each job; scan/add rows while running)"
        )
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._update_queue_remove_btn()
        apply_action_buttons(self.start_btn, self.stop_btn, self.remove_queue_btn)
        self._queue_bridge = QueueJobBridge(self)
        self.queue_thread = ParallelRenderWorker(
            self._queue_bridge,
            initial_jobs=queue_data,
            max_parallel=self.max_parallel_spin.value(),
        )
        self.queue_thread.log_signal.connect(self.log)
        self.queue_thread.update_row_signal.connect(self._on_worker_update_row)
        self.queue_thread.progress_signal.connect(self._on_render_progress)
        self.queue_thread.frame_progress_signal.connect(self._on_frame_progress)
        self.queue_thread.disk_progress_signal.connect(self._on_disk_progress)
        self.queue_thread.queue_count_signal.connect(self._on_queue_batch_updated)
        self.queue_thread.finished_signal.connect(self._on_queue_finished)
        clear_jobs()
        self._progress_timer.start()
        self.queue_thread.start()

    def _set_status_progress(self, row, ratio=None):
        item = self.queue_table.item(row, COL_STATUS)
        if not item:
            return
        if ratio is None:
            item.setData(RENDER_PROGRESS_ROLE, None)
        else:
            item.setData(RENDER_PROGRESS_ROLE, float(ratio))
            pct = int(float(ratio) * 100)
            if row == self._writing_disk_row and ratio < 1.0:
                counts = self._writing_disk_counts.get(row)
                if counts:
                    existing, total = counts
                    item.setText(
                        f"Writing to disk {existing}/{total} ({pct}%)"
                    )
                else:
                    item.setText(f"Writing to disk {pct}%")
            elif item.text().startswith("Running") or item.text().startswith("Writing"):
                item.setText(f"Running {pct}%")
        self.queue_table.update(self.queue_table.model().index(row, COL_STATUS))

    def _clear_all_status_progress(self):
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, COL_STATUS)
            if item and item.data(RENDER_PROGRESS_ROLE) is not None:
                item.setData(RENDER_PROGRESS_ROLE, None)
                self.queue_table.update(self.queue_table.model().index(row, COL_STATUS))

    def _update_global_progress(self, cur, total, row, status):
        t = TRANSLATIONS[self.current_language]
        if total <= 0 or not status:
            self.render_progress_label.setText(t["render_progress_idle"])
            self.render_progress_bar.setValue(0)
            self.render_progress_bar.setFormat("%p%")
            self.setWindowTitle(t["title"])
            return

        name = "?"
        if row >= 0:
            aep_item = self.queue_table.item(row, COL_AEP)
            if aep_item:
                name = os.path.basename(aep_item.text())

        pct = 0
        if status == "Running" and self._jobs_total > 0:
            pct = int(
                ((self._jobs_done + self._active_render_ratio) / self._jobs_total) * 100
            )
            pct = max(0, min(100, pct))
        elif self._jobs_total > 0:
            pct = int((self._jobs_done / self._jobs_total) * 100)

        self.render_progress_bar.setValue(pct)
        self.render_progress_label.setText(
            t["render_progress"].format(
                cur=cur, total=total, name=name, status=status, pct=pct
            )
        )
        self.setWindowTitle(f"{t['title']} — {status} ({cur}/{total})")

    def _on_worker_update_row(self, row, status, start_time, end_time):
        self.queue_table.blockSignals(True)
        try:
            item = self.queue_table.item(row, COL_STATUS)
            if item:
                item.setText(status)
            if status == "Running":
                self._lock_queue_row(row, True)
            else:
                self._lock_queue_row(row, False)
            if status != "Running":
                self._set_status_progress(row, None)
                if row == self._writing_disk_row:
                    self._writing_disk_row = -1
                    self._writing_disk_counts.pop(row, None)
            if status in ("Completed", "Skipped", "Failed", "Stopped", "Incomplete"):
                self._jobs_done += 1
            if start_time:
                st = self.queue_table.item(row, COL_START_TIME)
                if st:
                    st.setText(start_time)
                dur = self.queue_table.item(row, COL_DURATION)
                if dur:
                    dur.setText("")
            if end_time:
                et = self.queue_table.item(row, COL_END_TIME)
                if et:
                    et.setText(end_time)
                self._set_row_render_time(row)
        finally:
            self.queue_table.blockSignals(False)
        self.save_state()
        if self._queue_rendering():
            self.refresh_queue_snapshot(hot_append=True)

    def _apply_row_progress(self, row, ratio, disk_existing=None, disk_total=None):
        try:
            ratio = max(0.0, min(1.0, float(ratio)))
        except (TypeError, ValueError):
            ratio = 0.0
        if disk_total and disk_total > 0 and disk_existing is not None:
            self._writing_disk_row = row
            self._writing_disk_counts[row] = (int(disk_existing), int(disk_total))
        elif is_log_frozen(row):
            self._writing_disk_row = row
        self._active_render_row = row
        self._active_render_ratio = ratio
        self._set_status_progress(row, ratio)
        cur = min(self._jobs_done + 1, self._jobs_total) if self._jobs_total else 1
        self._update_global_progress(cur, self._jobs_total, row, "Running")

    def _on_disk_progress(self, row, existing, total):
        if total <= 0:
            return
        ratio = min(1.0, max(0.0, existing / float(total)))
        self._apply_row_progress(row, ratio, disk_existing=existing, disk_total=total)

    def _tick_render_progress(self):
        if not self.queue_thread or not self.queue_thread.isRunning():
            return
        for row, ratio in poll_all().items():
            self._apply_row_progress(row, ratio)

    def _on_render_progress(self, cur, total, row):
        self._active_render_row = row
        if self._progress_ui_row != row:
            self._active_render_ratio = 0.0
            for r in range(self.queue_table.rowCount()):
                if r != row:
                    self._set_status_progress(r, None)
            self._set_status_progress(row, 0.0)
        self._progress_ui_row = row
        self._update_global_progress(cur, total, row, "Running")
        self.queue_table.selectRow(row)
        scroll_item = self.queue_table.item(row, COL_ENABLED)
        if scroll_item:
            self.queue_table.scrollToItem(scroll_item)

    def _on_frame_progress(self, row, ratio):
        if is_log_frozen(row) or row == self._writing_disk_row:
            counts = self._writing_disk_counts.get(row)
            if counts and counts[1] > 0:
                existing = min(
                    counts[1], max(0, int(round(float(ratio) * counts[1])))
                )
                self._writing_disk_counts[row] = (existing, counts[1])
        self._apply_row_progress(row, ratio)

    def _on_queue_finished(self, was_stopped):
        try:
            self._progress_timer.stop()
            clear_jobs()
            self._progress_ui_row = -1
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            apply_action_buttons(self.start_btn, self.stop_btn, self.remove_queue_btn)
            self._update_queue_remove_btn()
            self._clear_all_status_progress()
            self._writing_disk_row = -1
            self._writing_disk_counts = {}
            self._jobs_done = self._jobs_total
            self._update_global_progress(0, 0, -1, "")
            self.log("Queue stopped." if was_stopped else "Queue finished.")
            summaries = self.collect_enabled_job_summaries()
            sleep_on = self.sleep_on_finish_chk.isChecked()
            should_sleep = handle_queue_finished(
                was_stopped, summaries, sleep_on, log_callback=self.log
            )
            if should_sleep:
                t = TRANSLATIONS[self.current_language]
                if (
                    QMessageBox.question(
                        self,
                        t["confirm_sleep_title"],
                        t["confirm_sleep"],
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    == QMessageBox.Yes
                ):
                    run_system_sleep(log_callback=self.log)
                else:
                    self.log("System sleep cancelled.")
        except Exception as exc:
            self.log(f"Queue finished handler error: {exc}")
        finally:
            self.save_state()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        stop_all_renders(log_callback=self.log)
        if self.queue_thread and self.queue_thread.isRunning():
            self.queue_thread.requestInterruption()
            self.queue_thread.wait(5000)
        stop_all_renders(log_callback=self.log)
        self.save_state()
        super().closeEvent(event)

    def _stop_render(self):
        if QMessageBox.question(self, "Stop", self.tr("confirm_stop")) != QMessageBox.Yes:
            return
        self._progress_timer.stop()
        self.log("Stopping renders and After Effects processes…")
        stop_render(log_callback=self.log)
        if self.queue_thread and self.queue_thread.isRunning():
            self.queue_thread.requestInterruption()
        self.log("Stop requested — aerender and After Effects should exit within a few seconds.")
