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

1. In After Effects, add comps to **Window → Render Queue**, enable the items you want, set output paths and templates, **save the .aep**.
2. In the manager: add AEP(s) → **Add to render queue** → **Start render**.
3. Each job runs:
   ```text
   aerender -project "your.aep" -v ERRORS_AND_PROGRESS
   ```
   Adobe renders **all enabled queue items** in that project using the settings stored in the file (no ExtendScript scan).

## Build executable

```powershell
pip install pyinstaller
pyinstaller main.spec
Copy-Item config.example.json dist\config.json
```

Output: `dist\AERenderManager.exe` (dark-themed GUI, no console). Place `config.json` next to the exe.

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `ui_main.py` | PySide6 UI |
| `render_runner.py` | `aerender` subprocess |
| `parallel_pool.py` | Parallel queue worker |
| `ae_paths.py` | AE install detection |
| `telegram_notifier.py` | Telegram Bot API |
| `app_paths.py` | Dev vs frozen exe paths |

Legacy scan scripts (`scan_queue.py`, `scan_render_queue.jsx`) are unused by the UI but kept for reference.

## Parallel renders

`max_parallel > 1` runs multiple `aerender` processes at once. Each uses a separate AE instance — watch RAM. Do not render the same `.aep` twice in parallel.
