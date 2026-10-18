"""Keep the PC from idle-sleeping/hibernating while TRACE needs it.

Sleep/hibernate stops the UDP socket, so a race or lap recording silently
ends up incomplete. This only blocks *idle* sleep: closing the lid or
choosing Sleep/Hibernate yourself still suspends the PC. The hold is dropped
as soon as set_awake(False) is called, when the window closes, and (macOS and
Linux) if TRACE itself is killed.
"""
import atexit
import os
import shutil
import subprocess
import sys

_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_proc = None
_on = False


def _linux_inhibit_cmd(pid, which=shutil.which):
    """systemd-inhibit command that holds an idle/sleep block for as long as `pid` lives,
    or None when systemd-inhibit isn't there. The shell loop ends when TRACE does, so a
    crash or kill can't leave the machine unable to sleep."""
    if not which("systemd-inhibit"):
        return None
    return ["systemd-inhibit", "--what=idle:sleep", "--who=TRACE", "--why=Recording GT7 telemetry",
            "--mode=block", "sh", "-c", f"while kill -0 {pid} 2>/dev/null; do sleep 5; done"]


def set_awake(on: bool) -> None:
    global _proc, _on
    if on == _on:
        return
    _on = on
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED if on else _ES_CONTINUOUS)
        elif sys.platform == "darwin":
            if on:
                # -w ties caffeinate to this process, so a crash or kill cannot leave the Mac awake
                _proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            elif _proc:
                _proc.terminate()
                _proc = None
        else:
            if on:
                cmd = _linux_inhibit_cmd(os.getpid())
                if cmd:
                    _proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif _proc:
                _proc.terminate()
                _proc = None
    except Exception:
        pass


def _release_on_exit():
    try:
        set_awake(False)
    except Exception:
        pass


atexit.register(_release_on_exit)
