"""Kill aerender / After Effects process trees on Windows."""

import logging

import os

import subprocess



logger = logging.getLogger(__name__)



CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)



AE_NAME_MARKERS = ("aerender", "afterfx", "dynamiclink")

AE_IMAGE_NAMES = ("aerender.exe", "AfterFX.com", "AfterFX.exe")

# Never treat our own app (or similar names) as Adobe render processes.
_PROCESS_NAME_EXCLUDES = ("aerendermanager",)





def _taskkill_tree(pid, force=True):

    if pid is None or pid <= 0:

        return False

    args = ["taskkill", "/PID", str(int(pid)), "/T"]

    if force:

        args.append("/F")

    try:

        result = subprocess.run(

            args,

            capture_output=True,

            text=True,

            creationflags=CREATE_NO_WINDOW,

            timeout=15,

        )

        if result.returncode in (0, 128, 255):

            return True

        err = (result.stderr or result.stdout or "").strip()

        if err and "not found" not in err.lower():

            logger.warning("taskkill %s: %s", pid, err)

        return result.returncode == 0

    except Exception as e:

        logger.warning("taskkill failed for pid %s: %s", pid, e)

        return False





def _taskkill_images():

    if os.name != "nt":

        return

    for image in AE_IMAGE_NAMES:

        try:

            subprocess.run(

                ["taskkill", "/IM", image, "/F", "/T"],

                capture_output=True,

                text=True,

                creationflags=CREATE_NO_WINDOW,

                timeout=15,

            )

        except Exception as e:

            logger.debug("taskkill /IM %s: %s", image, e)





def pid_is_alive(pid):
    """True if a process id is still running."""
    if pid is None or pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except ImportError:
        if os.name != "nt":
            return False
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        out = (result.stdout or "").lower()
        return str(int(pid)) in out and "no tasks are running" not in out


def kill_process_tree(pid, force=True):

    if pid is None or pid <= 0:

        return

    try:

        import psutil



        proc = psutil.Process(int(pid))

        children = proc.children(recursive=True)

        for child in children:

            try:

                if force:

                    child.kill()

                else:

                    child.terminate()

            except Exception:

                pass

        try:

            if force:

                proc.kill()

            else:

                proc.terminate()

        except Exception:

            pass

        psutil.wait_procs(children + [proc], timeout=3)

    except ImportError:

        _taskkill_tree(pid, force=force)

    except Exception as e:

        logger.warning("psutil kill_tree %s: %s", pid, e)

        _taskkill_tree(pid, force=force)





def _is_ae_process_name(name):
    lower = (name or "").lower()
    if any(excl in lower for excl in _PROCESS_NAME_EXCLUDES):
        return False
    if lower in {img.lower() for img in AE_IMAGE_NAMES}:
        return True
    return any(marker in lower for marker in AE_NAME_MARKERS)





def _cmdline_matches_project(cmd_norm, project_path):

    if not project_path:

        return True

    abspath = os.path.normcase(os.path.abspath(project_path))

    if abspath in cmd_norm:

        return True

    alt = abspath.replace("\\", "/")

    if alt in cmd_norm.replace("\\", "/"):

        return True

    base = os.path.normcase(os.path.basename(project_path))

    return bool(base and base in cmd_norm)





def collect_ae_child_pids(root_pid):

    pids = set()

    if root_pid is None or root_pid <= 0:

        return pids

    try:

        import psutil



        proc = psutil.Process(int(root_pid))

        for child in proc.children(recursive=True):

            try:

                if _is_ae_process_name(child.name()):

                    pids.add(int(child.pid))

            except (psutil.NoSuchProcess, psutil.AccessDenied):

                continue

    except Exception as e:

        logger.debug("collect_ae_child_pids %s: %s", root_pid, e)

    return pids





def kill_aerender_processes(project_path=None, extra_pids=None):

    """Stop aerender/AfterFX tied to this render session (or all AE render procs if project_path is None)."""

    killed = set()

    if extra_pids:

        for pid in extra_pids:

            if pid and int(pid) not in killed:

                kill_process_tree(int(pid), force=True)

                killed.add(int(pid))



    if project_path is None:

        return kill_all_ae_render_processes(already_killed=killed)



    if os.name != "nt" and not project_path:

        return killed



    try:

        import psutil

    except ImportError:

        _taskkill_images()

        return killed



    for proc in psutil.process_iter(["pid", "name", "cmdline"]):

        try:

            pid = proc.info.get("pid")

            name = proc.info.get("name") or ""

            if not pid or int(pid) in killed:

                continue

            if not _is_ae_process_name(name):

                continue

            cmdline = proc.info.get("cmdline") or []

            cmd = " ".join(str(c) for c in cmdline)

            cmd_norm = os.path.normcase(cmd)

            if not _cmdline_matches_project(cmd_norm, project_path):

                continue

            kill_process_tree(pid, force=True)

            killed.add(int(pid))

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):

            continue

        except Exception as e:

            logger.debug("process_iter: %s", e)



    return killed





def kill_all_ae_render_processes(log_callback=None, already_killed=None):

    """Force-stop all aerender / AfterFX / dynamiclink render helper processes."""

    killed = set(already_killed or ())



    try:

        import psutil

    except ImportError:

        _taskkill_images()

        if log_callback:

            log_callback("Stop: taskkill aerender / AfterFX (psutil unavailable).")

        return killed



    for proc in psutil.process_iter(["pid", "name"]):

        try:

            pid = proc.info.get("pid")

            name = proc.info.get("name") or ""

            if not pid or int(pid) in killed:

                continue

            if not _is_ae_process_name(name):

                continue

            kill_process_tree(pid, force=True)

            killed.add(int(pid))

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):

            continue

        except Exception as e:

            logger.debug("kill_all iter: %s", e)



    _taskkill_images()



    if log_callback and killed:

        log_callback(f"Stop: ended {len(killed)} aerender/AfterFX process(es).")

    elif log_callback:

        log_callback("Stop: no aerender/AfterFX processes found (may already be closed).")



    return killed


