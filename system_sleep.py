"""Put the workstation to sleep (Windows)."""

import os
import subprocess


def request_system_sleep():
    """
    Suspend the computer (sleep). Returns (ok, error_message).

    Windows only; uses SetSuspendState (sleep, not hibernate).
    """
    if os.name != "nt":
        return False, "System sleep is only supported on Windows"

    try:
        import ctypes

        # FALSE = sleep (not hibernate), TRUE = force, FALSE = do not disable wake events
        ctypes.windll.powrprof.SetSuspendState(False, True, False)
        return True, ""
    except Exception as exc:
        try:
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True, ""
        except Exception as exc2:
            return False, f"{exc}; fallback: {exc2}"
