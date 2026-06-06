"""Run ExtendScript scan of an AEP render queue via AfterFX."""
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from ae_paths import get_afterfx_path
from app_paths import (
    app_dir,
    bundled_script,
    is_frozen,
    manager_exe_path_file,
    scan_args_path,
    scan_push_marker_path,
    scan_result_path,
    scan_work_dir,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5
GRACE_AFTER_EXIT_SEC = 90


def _read_scan_result(out_json):
    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


def _valid_result_file(path):
    if not os.path.isfile(path) or os.path.getsize(path) < 3:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, OSError):
        return False


def _write_handoff_bootstrap(work_dir, main_jsx_path):
    """Bootstrap in LocalAppData — some AE builds only run -r from short paths."""
    main_path_file = os.path.join(work_dir, "scan_main_path.txt")
    with open(main_path_file, "w", encoding="utf-8") as f:
        f.write(main_jsx_path.replace("\\", "/"))

    bootstrap = os.path.join(work_dir, "run_scan.jsx")
    bootstrap_js = r"""(function () {
  function workDir() {
    var list = [];
    try { if (Folder.localAppData) list.push(Folder.localAppData); } catch (e0) {}
    try { if (Folder.userData) list.push(Folder.userData); } catch (e1) {}
    try { if (Folder.myDocuments) list.push(Folder.myDocuments); } catch (e2) {}
    var i, base = "";
    for (i = 0; i < list.length; i++) {
      try {
        if (list[i] && list[i].fsName) { base = list[i].fsName; break; }
      } catch (e3) {}
    }
    if (!base) throw new Error("Cannot resolve AppData folder");
    return base + "/AERenderManager";
  }
  try {
    var wd = workDir();
    var pFile = new File(wd + "/scan_main_path.txt");
    pFile.open("r");
    var mainPath = pFile.read().replace(/^\s+|\s+$/g, "");
    pFile.close();
    var main = new File(mainPath);
    if (!main.exists) throw new Error("scan_render_queue.jsx not found: " + mainPath);
    $.evalFile(main);
  } catch (e) {
    try {
      var errF = new File(workDir() + "/scan_last_error.txt");
      errF.encoding = "UTF-8";
      errF.open("w");
      errF.write("bootstrap: " + e.toString());
      errF.close();
    } catch (e2) {}
    try { alert("Scan bootstrap failed: " + e.toString()); } catch (a) {}
  }
})();"""
    with open(bootstrap, "w", encoding="utf-8", newline="\n") as f:
        f.write(bootstrap_js)
    return bootstrap


def _deploy_scriptui_panel(afterfx_path, panel_jsx_src):
    """Copy dockable panel to AE Scripts/ScriptUI Panels/ (Window menu)."""
    panels_root = os.path.join(
        os.path.dirname(afterfx_path), "Scripts", "ScriptUI Panels"
    )
    try:
        os.makedirs(panels_root, exist_ok=True)
        dest = os.path.join(panels_root, "AE Render Manager.jsx")
        shutil.copy2(panel_jsx_src, dest)
        return dest
    except (PermissionError, OSError) as exc:
        logger.warning("Cannot deploy ScriptUI panel: %s", exc)
        return None


def _deploy_to_ae_scripts(afterfx_path, jsx_src, push_jsx_src=None):
    """
    Optionally copy scanner + push scripts into AE Scripts/AERenderManager/.
    Returns bootstrap path, or None if Program Files is not writable (no admin).
    """
    scripts_root = os.path.join(os.path.dirname(afterfx_path), "Scripts", "AERenderManager")
    try:
        os.makedirs(scripts_root, exist_ok=True)
        dest_main = os.path.join(scripts_root, "scan_render_queue.jsx")
        shutil.copy2(jsx_src, dest_main)
        if push_jsx_src and os.path.isfile(push_jsx_src):
            shutil.copy2(push_jsx_src, os.path.join(scripts_root, "push_render_queue.jsx"))
        bootstrap = os.path.join(scripts_root, "run_ae_rm_scan.jsx")
        bootstrap_js = r"""(function () {
  var dir = new File($.fileName).parent;
  var main = new File(dir.fsName + "/scan_render_queue.jsx");
  if (!main.exists) throw new Error("Missing scan_render_queue.jsx in " + dir.fsName);
  $.evalFile(main);
})();"""
        with open(bootstrap, "w", encoding="utf-8", newline="\n") as f:
            f.write(bootstrap_js)
        push_boot = os.path.join(scripts_root, "Push to Render Manager.jsx")
        push_boot_js = r"""(function () {
  var dir = new File($.fileName).parent;
  var main = new File(dir.fsName + "/push_render_queue.jsx");
  if (!main.exists) throw new Error("Missing push_render_queue.jsx in " + dir.fsName);
  $.evalFile(main);
})();"""
        with open(push_boot, "w", encoding="utf-8", newline="\n") as f:
            f.write(push_boot_js)
        return bootstrap
    except (PermissionError, OSError) as exc:
        logger.warning("Cannot deploy to AE Scripts folder: %s", exc)
        return None


def _read_err_file(err_file):
    if not os.path.isfile(err_file):
        return ""
    try:
        with open(err_file, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _js_path_literal(path):
    return path.replace("\\", "/").replace('"', '\\"')


def _write_eval_launcher(work_dir, jsx_path):
    """Run main scanner via -r (more reliable on Windows than inline -s)."""
    launcher = os.path.join(work_dir, "launch_scan.jsx")
    main = _js_path_literal(os.path.normpath(jsx_path))
    launcher_js = (
        "(function () {\n"
        "  try {\n"
        f'    var main = new File("{main}");\n'
        "    if (!main.exists) throw new Error('scan_render_queue.jsx not found: ' + main.fsName);\n"
        "    $.evalFile(main);\n"
        "  } catch (e) {\n"
        "    var wd = (function(){var l=[];try{if(Folder.userData)l.push(Folder.userData);}catch(e){} "
        "try{if(Folder.myDocuments)l.push(Folder.myDocuments);}catch(e){} "
        "return (l[0]&&l[0].fsName?l[0].fsName:'')+'/AERenderManager';})(); "
        "var errF = new File(wd + '/scan_last_error.txt');\n"
        "    errF.encoding = 'UTF-8';\n"
        "    errF.open('w');\n"
        "    errF.write('launcher: ' + e.toString());\n"
        "    errF.close();\n"
        "    try { app.quit(); } catch (q) {}\n"
        "  }\n"
        "})();"
    )
    with open(launcher, "w", encoding="utf-8", newline="\n") as f:
        f.write(launcher_js)
    return launcher


def _build_evalfile_cmd(afterfx, jsx_path):
    """Legacy fallback: inline -s expression."""
    p = _js_path_literal(jsx_path)
    expr = (
        "try { $.evalFile(new File('" + p + "')); } "
        "catch (e) { "
        "var ef = new File('" + _js_path_literal(os.path.join(scan_work_dir(), "scan_last_error.txt")) + "'); "
        "ef.encoding='UTF-8'; ef.open('w'); ef.write('evalFile: '+e.toString()); ef.close(); "
        "try { app.quit(); } catch (q) {} }"
    )
    return [afterfx, "-noui", "-m", "-s", expr]


def _scan_cmd(afterfx, script_path, aep_path=None, use_noui=True):
    cmd = [afterfx, "-m"]
    if use_noui:
        cmd.insert(1, "-noui")
    if aep_path:
        cmd.extend(["-project", os.path.normpath(aep_path)])
    cmd.extend(["-r", os.path.normpath(script_path)])
    return cmd


def _needs_jsx_refresh(src, dest, required_marker=None):
    if not os.path.isfile(dest):
        return True
    try:
        with open(src, encoding="utf-8") as handle:
            src_text = handle.read()
        with open(dest, encoding="utf-8") as handle:
            dest_text = handle.read()
    except OSError:
        return True
    if required_marker:
        src_ok = required_marker in src_text
        dest_ok = required_marker in dest_text
        if dest_ok and not src_ok:
            return False
        if src_ok and not dest_ok:
            return True
    return os.path.getmtime(src) > os.path.getmtime(dest)


def _copy_bundled_jsx_to_work(filename, required_marker=None):
    src = bundled_script(filename)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"{filename} not found: {src}")
    work = scan_work_dir()
    stable = os.path.join(work, filename)
    try:
        if _needs_jsx_refresh(src, stable, required_marker=required_marker):
            shutil.copy2(src, stable)
    except OSError as exc:
        logger.warning("Could not copy %s to work dir: %s", filename, exc)
        return src
    if is_frozen():
        exe_copy = os.path.join(app_dir(), filename)
        try:
            if _needs_jsx_refresh(src, exe_copy, required_marker=required_marker):
                shutil.copy2(src, exe_copy)
        except OSError:
            pass
    return stable if os.path.isfile(stable) else src


def _resolve_scan_jsx():
    """
    Return a stable path to scan_render_queue.jsx (PyInstaller bundles it in _MEIPASS).
    Also copies beside the .exe and into LocalAppData for AE -r / evalFile.
    """
    stable = _copy_bundled_jsx_to_work(
        "scan_render_queue.jsx", required_marker="function detectUseProxy"
    )
    if not os.path.isfile(stable):
        raise FileNotFoundError(
            "scan_render_queue.jsx not found.\n"
            "Rebuild the app so main.spec includes scan_render_queue.jsx in datas."
        )
    return stable


def _resolve_push_jsx():
    """Return stable path to push_render_queue.jsx in LocalAppData."""
    return _copy_bundled_jsx_to_work(
        "push_render_queue.jsx", required_marker="AERM_MODE"
    )


def _resolve_panel_jsx():
    """Return stable path to push_render_panel.jsx in LocalAppData."""
    return _copy_bundled_jsx_to_work(
        "push_render_panel.jsx", required_marker="Send to AE Render Manager"
    )


def deploy_push_scripts(log_callback=None):
    """Copy scan + push JSX to LocalAppData and optionally AE Scripts menu."""
    write_manager_exe_path()
    scan_jsx = _resolve_scan_jsx()
    push_jsx = _resolve_push_jsx()
    panel_jsx = _resolve_panel_jsx()
    afterfx = get_afterfx_path()
    scripts_boot = None
    panel_dest = None
    if os.path.isfile(afterfx):
        scripts_boot = _deploy_to_ae_scripts(afterfx, scan_jsx, push_jsx_src=push_jsx)
        panel_dest = _deploy_scriptui_panel(afterfx, panel_jsx)
    if log_callback:
        log_callback(f"  Push script: {push_jsx}")
        log_callback(f"  AE panel script: {panel_jsx}")
        if panel_dest:
            log_callback("  AE panel: Window → AE Render Manager")
        elif scripts_boot:
            log_callback(
                "  AE menu: File → Scripts → Push to Render Manager "
                f"({os.path.dirname(scripts_boot)})"
            )
        elif os.path.isfile(afterfx):
            log_callback(
                "  AE panel not installed (no write access to Program Files). "
                "Copy push_render_panel.jsx to AE Support Files/Scripts/ScriptUI Panels/ "
                "as AE Render Manager.jsx, or run the manager as administrator once."
            )
        else:
            log_callback(
                "  AE panel: start After Effects, then restart this app to deploy"
            )
    return scan_jsx, push_jsx


def read_push_result():
    """Load scan_result.json written by push_render_queue.jsx from open AE."""
    out_json = scan_result_path()
    if not _valid_result_file(out_json):
        return None
    try:
        return _read_scan_result(out_json)
    except RuntimeError:
        return None


def write_manager_exe_path():
    """Write frozen exe path for AE push scripts to launch or raise the manager."""
    path_file = manager_exe_path_file()
    if not is_frozen():
        try:
            if os.path.isfile(path_file):
                os.remove(path_file)
        except OSError:
            pass
        return
    exe = os.path.normpath(sys.executable)
    try:
        with open(path_file, "w", encoding="utf-8") as handle:
            handle.write(exe.replace("\\", "/"))
    except OSError as exc:
        logger.warning("Cannot write manager_exe_path.txt: %s", exc)


def read_push_marker():
    """Load scan_push.json marker written after a successful AE push."""
    marker = scan_push_marker_path()
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def push_marker_id(marker=None):
    """Stable id for a push marker (used to skip already-imported pushes)."""
    if marker is None:
        marker = read_push_marker()
    if not marker:
        path = scan_push_marker_path()
        if os.path.isfile(path):
            return str(os.path.getmtime(path))
        return ""
    pushed_at = marker.get("pushed_at")
    if pushed_at:
        return str(pushed_at)
    project = marker.get("project", "")
    count = marker.get("item_count", "")
    return f"{project}|{count}"


def clear_push_marker():
    marker = scan_push_marker_path()
    try:
        if os.path.isfile(marker):
            os.remove(marker)
    except OSError:
        pass


def _filter_stderr(stderr):
    if not stderr:
        return ""
    lines = []
    for line in stderr.splitlines():
        text = line.strip()
        if not text:
            continue
        if "GPU Warning" in text and "sanity test" in text:
            continue
        lines.append(text)
    return "\n".join(lines)


def _poll_for_output(out_json, proc, timeout_sec, log_callback=None):
    """
    AfterFX.com often returns before the GUI finishes the script.
    Wait for scan_result.json instead of trusting process exit alone.
    """
    deadline = time.monotonic() + timeout_sec
    work = os.path.dirname(out_json)
    err_file = os.path.join(work, "scan_last_error.txt")
    proc_exited_at = None

    while time.monotonic() < deadline:
        if _valid_result_file(out_json):
            return True
        if os.path.isfile(err_file) and os.path.getsize(err_file) > 0:
            err_text = _read_err_file(err_file)
            if log_callback and err_text:
                log_callback(f"  Script error: {err_text}")
            return False

        code = proc.poll()
        if code is not None and proc_exited_at is None:
            proc_exited_at = time.monotonic()
            if log_callback:
                log_callback(
                    "  AfterFX.com exited; waiting up to "
                    f"{GRACE_AFTER_EXIT_SEC}s for script output…"
                )

        if proc_exited_at is not None:
            if time.monotonic() - proc_exited_at > GRACE_AFTER_EXIT_SEC:
                break

        time.sleep(POLL_INTERVAL)

    return _valid_result_file(out_json)


def _run_scan_attempt(cmd, out_json, timeout_sec, log_callback):
    if log_callback:
        log_callback(f"Scan: {' '.join(cmd)}")
    logger.info("Scan command: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ok = _poll_for_output(out_json, proc, timeout_sec, log_callback)

    try:
        stdout, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    if not ok:
        err_file = os.path.join(os.path.dirname(out_json), "scan_last_error.txt")
        err_text = _read_err_file(err_file)
        if err_text and log_callback:
            log_callback(f"  Scan error log: {err_text}")

    if stdout and log_callback:
        for line in stdout.splitlines():
            if line.strip():
                log_callback(f"  {line.strip()}")
    stderr_useful = _filter_stderr(stderr)
    if stderr_useful and log_callback:
        for line in stderr_useful.splitlines():
            log_callback(f"  [stderr] {line}")

    return ok, stdout, stderr_useful, proc.returncode


def _write_python_scan_log(message):
    work = scan_work_dir()
    log_path = os.path.join(work, "scan_debug.log")
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [Python] {message}\n")
    except OSError:
        pass
    return work


def scan_project(aep_path, log_callback=None, timeout=600):
    aep_path = os.path.normpath(os.path.abspath(aep_path))
    if not os.path.isfile(aep_path):
        raise FileNotFoundError(f"AEP not found: {aep_path}")

    work = _write_python_scan_log(f"Scan started: {aep_path}")
    if log_callback:
        log_callback(f"  Work folder: {work}")

    afterfx = get_afterfx_path()
    if not os.path.isfile(afterfx):
        raise FileNotFoundError(
            f"AfterFX not found: {afterfx}. Set afterfx_path in config.json."
        )

    jsx_path = os.path.normpath(_resolve_scan_jsx())
    push_jsx = os.path.normpath(_resolve_push_jsx())

    out_json = os.path.join(work, "scan_result.json")
    err_file = os.path.join(work, "scan_last_error.txt")
    args_file = scan_args_path()

    for stale in (out_json, err_file):
        try:
            if os.path.isfile(stale):
                os.remove(stale)
        except OSError:
            pass

    args_payload = {
        "project": aep_path.replace("\\", "/"),
        "output": out_json.replace("\\", "/"),
        "auto_quit": True,
    }
    with open(args_file, "w", encoding="utf-8") as f:
        json.dump(args_payload, f, indent=2)
    _write_python_scan_log(f"Wrote scan_args.json -> {args_file}")

    if log_callback:
        log_callback(f"  scan_args.json: {args_file}")

    handoff_bootstrap = _write_handoff_bootstrap(work, jsx_path)
    eval_launcher = _write_eval_launcher(work, jsx_path)
    scripts_bootstrap = _deploy_to_ae_scripts(afterfx, jsx_path, push_jsx_src=push_jsx)

    if log_callback:
        log_callback(f"  Scanner: {jsx_path}")
        log_callback(f"  Handoff: {handoff_bootstrap}")
        log_callback(f"  Launcher: {eval_launcher}")
        if scripts_bootstrap:
            log_callback(f"  AE Scripts deploy: {os.path.dirname(scripts_bootstrap)}")
        else:
            log_callback(
                "  AE Scripts folder skipped (no write access — using LocalAppData only)."
            )

    # Run scan_render_queue.jsx directly (bootstrap hops are fragile on AE Beta).
    cmd_variants = [
        (
            "Direct JSX + project (UI)",
            _scan_cmd(afterfx, jsx_path, aep_path=aep_path, use_noui=False),
        ),
        (
            "Direct JSX + project",
            _scan_cmd(afterfx, jsx_path, aep_path=aep_path, use_noui=True),
        ),
        ("Direct JSX only", _scan_cmd(afterfx, jsx_path, use_noui=True)),
        (
            "Handoff + project",
            _scan_cmd(afterfx, handoff_bootstrap, aep_path=aep_path, use_noui=False),
        ),
        ("evalFile -s (fallback)", _build_evalfile_cmd(afterfx, jsx_path)),
    ]
    if scripts_bootstrap:
        cmd_variants.extend(
            [
                ("AE Scripts folder", _scan_cmd(afterfx, scripts_bootstrap)),
                (
                    "AE Scripts + project",
                    _scan_cmd(afterfx, scripts_bootstrap, aep_path=aep_path),
                ),
            ]
        )

    per_attempt_timeout = min(300, max(120, timeout // max(1, len(cmd_variants))))
    last_stderr = ""
    last_stdout = ""
    last_code = None

    for label, cmd in cmd_variants:
        if log_callback:
            log_callback(f"  Trying: {label}")
        ok, stdout, stderr, code = _run_scan_attempt(
            cmd, out_json, per_attempt_timeout, log_callback
        )
        last_stdout, last_stderr, last_code = stdout, stderr, code
        if ok:
            break
        if log_callback:
            log_callback("  No valid scan_result.json — next method…")
            err_text = _read_err_file(err_file)
            if err_text:
                log_callback(f"  Last error: {err_text}")
        try:
            if os.path.isfile(out_json):
                os.remove(out_json)
            if os.path.isfile(err_file):
                os.remove(err_file)
        except OSError:
            pass

    if not _valid_result_file(out_json):
        detail = ""
        if os.path.isfile(err_file):
            with open(err_file, encoding="utf-8") as f:
                detail = f.read().strip()
        if not detail:
            detail = last_stderr or last_stdout or f"exit code {last_code}"
        raise RuntimeError(
            "Scan produced no output JSON.\n"
            f"Handoff folder: {work}\n"
            f"Detail: {detail}\n\n"
            "The GPU message is unrelated to GPU count — AE logs it when GPU "
            "acceleration fails a self-test.\n\n"
            "Try:\n"
            "1. Edit → Preferences → Scripting → enable "
            "'Allow Scripts to Write Files and Access Network'\n"
            "2. Close all After Effects windows, then scan again\n"
            "3. Open the .aep in AE, then File → Scripts → Run Script File →\n"
            f"   {jsx_path}\n"
            "   (not run_scan.jsx — use scan_render_queue.jsx)\n"
            "4. Check scan_debug.log in the work folder (see log above)\n"
            "5. Manual: open .aep in AE, then run scan_render_queue.jsx\n"
            f"   (scan_args.json should exist at {args_file})"
        )

    try:
        data = _read_scan_result(out_json)
    finally:
        try:
            os.remove(out_json)
        except OSError:
            pass

    data["project"] = data.get("project") or aep_path
    if log_callback:
        log_callback(f"Scan OK: {len(data.get('items', []))} render queue item(s).")
    return data
