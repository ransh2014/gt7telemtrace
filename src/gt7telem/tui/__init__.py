"""The terminal edition of TRACE (Textual). Start it with `gt7telem tui` or `python -m gt7telem.tui`."""
import os
import sys


def _has_terminal() -> bool:
    try:
        return bool(sys.stdout and sys.stdout.isatty() and sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def ensure_console() -> bool:
    """The Windows TRACE.exe is a windowed program with no console, so Textual has nothing to draw on. Attach to the
    console it was started from, or open a new console window, and point stdin/stdout at it. Returns True if this call
    opened a console (the caller closes it with release_console). Does nothing when a terminal already exists."""
    if os.name != "nt" or _has_terminal():
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        if not k32.AttachConsole(-1):          # -1 = the parent's console, if it has one
            if not k32.AllocConsole():
                return False
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout
        sys.__stdin__, sys.__stdout__, sys.__stderr__ = sys.stdin, sys.stdout, sys.stderr
        try:
            k32.SetConsoleTitleW("TRACE")
        except Exception:
            pass
        return True
    except Exception:
        return False


def release_console(opened: bool) -> None:
    if opened and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass


def main() -> int:
    opened = ensure_console()
    try:
        from .app import main as _main
        return _main()
    finally:
        release_console(opened)
