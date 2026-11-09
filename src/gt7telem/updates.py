"""updates.py -- the launcher's "am I on the latest release?" check (headless-safe, no tkinter).

Asks GitHub for the latest published release of ransh2014/gt7telemtrace once per process (the answer is cached,
so re-opening the launcher from a tool's Menu button doesn't hit the network again). Any failure -- offline, rate
limited, odd JSON -- gives ``None`` and the launcher shows nothing extra. Drafts and pre-releases are ignored.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.request

API_URL = "https://api.github.com/repos/ransh2014/gt7telemtrace/releases/latest"
RELEASES_URL = "https://github.com/ransh2014/gt7telemtrace/releases/latest"
TIMEOUT_S = 5

CURRENT = "current"
AVAILABLE = "available"

_lock = threading.Lock()
_cache: dict[str, tuple] = {}   # installed version -> result tuple (or None)


def is_store_install() -> bool:
    """True when running from the Microsoft Store MSIX package (the Store handles updates there).
    Uses kernel32.GetCurrentPackageFullName: 15700 (APPMODEL_ERROR_NO_PACKAGE) = not packaged;
    0 or 122 (ERROR_INSUFFICIENT_BUFFER) = packaged. False on non-Windows or any failure."""
    try:
        if sys.platform != "win32":
            return False
        import ctypes
        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return rc in (0, 122)
    except Exception:
        return False


def ver_tuple(s) -> tuple:
    """'v1.0.10' -> (1, 0, 10, 0); ignores a leading v and any -rc1 / +build suffix. Compared as ints
    (1.0.10 > 1.0.9) and padded so 1.0 == 1.0.0."""
    core = re.split(r"[-+]", str(s).strip().lstrip("vV"), maxsplit=1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(candidate, installed) -> bool:
    return ver_tuple(candidate) > ver_tuple(installed)


def evaluate(release: dict, installed: str):
    """Release JSON -> ('current', None, url) | ('available', 'x.y.z', url) | None (unusable / pre-release)."""
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    tag = str(release.get("tag_name") or "").strip().lstrip("vV")
    if not tag or not re.search(r"\d", tag):
        return None
    url = release.get("html_url") or RELEASES_URL
    if is_newer(tag, installed):
        return (AVAILABLE, tag, url)
    return (CURRENT, None, url)


def fetch_latest():
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "TRACE-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(installed: str, callback, fetch=fetch_latest) -> None:
    """Run the check on a background thread and call ``callback(result_or_None)`` from that thread.
    Cached per installed version for the life of the process; never raises. Store installs make no request."""
    if is_store_install():
        return

    def work():
        with _lock:
            if installed in _cache:
                res = _cache[installed]
            else:
                try:
                    res = evaluate(fetch(), installed)
                except Exception:
                    res = None
                _cache[installed] = res
        try:
            callback(res)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True, name="trace-update-check").start()
