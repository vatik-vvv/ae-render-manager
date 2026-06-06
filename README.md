# After Effects Render Manager

PySide6 desktop app to queue `.aep` projects and render their **After Effects Render Queue** via `aerender.exe`, with optional parallel jobs and Telegram notifications.

## Requirements

- Python 3.10+
- Windows (primary target)
- Adobe After Effects with `aerender.exe`

## Setup

```powershell
cd E:\ai_proj\AE_Rmanager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Edit `config.json`: `aerender_path`, Telegram `bot_token` / `chat_id`, optional `max_parallel` (default `1`).

## Run

```powershell
python main.py
```

## How it works

### Load queue items (pick one)

**Push from open AE (fast, no second After Effects process)**

1. In After Effects: add comps to **Window → Render Queue**, set output paths, **save the .aep**.
2. Open **Window → AE Render Manager** and click **Send to AE Render Manager** (panel installed on first app start when permitted).
3. AE Render Manager launches or raises automatically and imports rows once per push (or click **Import from AE** if needed).

Run the manager at least once so it registers its exe path for auto-launch. In AE: **Edit → Preferences → Scripting → Allow Scripts to Write Files and Access Network** (required for launch from the panel).

**Scan from disk (launches headless AfterFX)**

1. Add the `.aep` to zone 2 in the manager.
2. **Scan selected** or **Scan all** — reads the saved render queue from the project file.

### Render

1. Review the render queue table (RS **Use queue** is recommended).
2. **Start render** — each row runs `aerender` with the scanned output path and frame range.

## Build executable

```powershell
python build_icon.py
pip install pyinstaller
pyinstaller main.spec
Copy-Item config.example.json dist\config.json
```

`build_icon.py` builds `AERM_icon.ico` with 16–256 px layers for sharp Explorer icons (including extra-large view). PyInstaller regenerates the ICO from `AERM_icon.png` when the PNG is newer.

Output: `dist\AERenderManager.exe` (dark-themed GUI, no console). Place `config.json` next to the exe.

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `ui_main.py` | PySide6 UI |
| `render_runner.py` | `aerender` subprocess |
| `parallel_pool.py` | Parallel queue worker |
| `scan_queue.py` | Scan + push script deploy |
| `scan_render_queue.jsx` | ExtendScript queue reader |
| `push_render_queue.jsx` | Push from open AE session |
| `push_render_panel.jsx` | Dockable AE panel (Window menu) |
| `ae_paths.py` | AE install detection |
| `telegram_notifier.py` | Telegram Bot API |
| `app_paths.py` | Dev vs frozen exe paths |

## Parallel renders

`max_parallel > 1` runs multiple `aerender` processes at once. Each uses a separate AE instance — watch RAM. Do not render the same `.aep` twice in parallel.
