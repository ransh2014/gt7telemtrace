"""Keep the PC from idle-sleeping/hibernating while telemetry is flowing.

Sleep/hibernate stops the UDP socket, so a race or lap recording silently
ends up incomplete. This only blocks *idle* sleep: closing the lid or
choosing Sleep/Hibernate yourself still suspends the PC.
"""
import os
import subprocess
import sys

_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_proc = None
_on = False


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
    except Exception:
        pass
