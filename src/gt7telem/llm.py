"""Local AI wording (opt-in, off by default): a small language model that only REWORDS the rule-based coach tips.

Pure Python (no tkinter). The rules in coach.py decide what to say; the model may only say it in friendlier words, and
a guard throws its answer away unless every number, corner label (T4) and direction word (earlier / later / sooner)
survives unchanged. It never runs live (the spoken cues stay rules-based and instant), it has no chat, and it never
invents advice.

Model: Llama 3.2 1B Instruct, 4-bit (Q4_K_M GGUF), "Built with Llama" -- see the notice in the README and About.
Three backends behind one interface:

1. ``llama-cpp-python`` when it is importable (the optional ``ai`` extra),
2. a pinned official ``llama-server`` build, downloaded on first enable and talked to over 127.0.0.1 only, for the
   frozen TRACE.exe (hidden in the Microsoft Store build: see ``backend_for``),
3. neither: the toggle explains why and stays off.

Nothing here touches the network until ``EnableFlow.confirm`` is called, and every download is checked against a pinned
SHA-256 before it is used.
"""

from __future__ import annotations

import atexit
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# ---- the pinned downloads ---------------------------------------------------------------------------------------
# Model: bartowski's GGUF of Llama 3.2 1B Instruct (the most downloaded quantisation; Meta publishes no GGUF itself).
# The URL is pinned to a commit of that repository and the SHA-256 is the LFS object id Hugging Face reports for the file.
MODEL_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
MODEL_COMMIT = "067b946cf014b7c697f3654f621d577a3e3afd1c"
MODEL_FILE = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{MODEL_FILE}"
MODEL_SHA256 = "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83"
MODEL_BYTES = 807_694_464

# Runtime for the frozen Windows build: the official llama.cpp release asset, pinned by tag; the SHA-256 is the asset
# digest GitHub reports. (llama.cpp's "latest" release carries no binaries, so a build tag is pinned instead.)
SERVER_TAG = "b12600"
SERVER_ZIP = f"llama-{SERVER_TAG}-bin-win-cpu-x64.zip"
SERVER_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{SERVER_TAG}/{SERVER_ZIP}"
SERVER_SHA256 = "a31a14f1630408422228d02ff2a3fc41c06a6b3b3849d8d2782c37f0f6e30951"
SERVER_BYTES = 25_012_254
SERVER_EXE = "llama-server.exe"

NEED_FREE_GB = 1.5
REMOVE_LABEL = "REMOVE AI MODEL"
LLAMA_NOTICE = "Built with Llama"
LLAMA_LICENSE_URL = "https://www.llama.com/llama3_2/license/"
LLAMA_LICENSE_NAME = "Llama 3.2 Community License"

# ---- how the model is asked ---------------------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a friendly racing coach. Rewrite the driving tip you are given as ONE short, friendly sentence of plain "
    "English. Keep every number, every corner label such as T4, every unit and every direction word (earlier, later, "
    "sooner, more) exactly as given. Do not add advice, numbers or new facts. Reply with the sentence only."
)
TEMPERATURE = 0.2
MAX_TOKENS = 80
TIMEOUT_S = 20.0
CONTEXT = 1024
MAX_THREADS = 6


# ---- spec check ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rating:
    level: str       # "green" | "amber" | "red"
    text: str


RECOMMENDED_SPECS = (
    "Minimum: 4 cores, 6 GB RAM, no GPU. It works; replies take several seconds.\n"
    "Recommended: 4+ cores (any recent Intel i5 or Ryzen 5), 8 GB RAM. A GPU is not needed; replies take 1-3 seconds.\n"
    "Best: 6+ cores, 16 GB RAM, and optionally an NVIDIA GPU with 2 GB+ VRAM."
)


def rate_specs(cores: int | None, ram_gb: float | None) -> Rating:
    """red: fewer than 4 cores or under 6 GB; amber: under 8 GB (or RAM unreadable); green: 4+ cores and 8+ GB."""
    c = cores or 0
    if c < 4:
        return Rating("red", f"Too slow: {c} CPU core(s) found, 4 are needed. TRACE may become slow. Stay on Rules mode.")
    if ram_gb is None:
        return Rating("amber", "Couldn't read how much RAM this PC has. It may work, but could be slow.")
    if ram_gb < 6:
        return Rating("red", f"Too little memory: {ram_gb:.1f} GB of RAM, 6 GB are needed. Stay on Rules mode.")
    if ram_gb < 8:
        return Rating("amber", f"It should work ({c} cores, {ram_gb:.1f} GB RAM) but replies will take several seconds and the PC may run warm.")
    return Rating("green", f"Good to go: {c} cores and {ram_gb:.1f} GB of RAM.")


def read_ram_bytes(platform: str | None = None, run: Callable | None = None) -> int | None:
    """Total physical RAM: GlobalMemoryStatusEx (Windows), /proc/meminfo (Linux), sysctl hw.memsize (macOS). No psutil."""
    plat = platform or sys.platform
    try:
        if plat == "win32":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return int(st.ullTotalPhys)
        if plat.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
            return None
        if plat == "darwin":
            out = (run or subprocess.check_output)(["sysctl", "-n", "hw.memsize"], text=True, timeout=5)
            return int(str(out).strip())
    except Exception:
        return None
    return None


def check_specs(cores: int | None = None, ram_bytes: int | None = None, read_ram: Callable[[], int | None] | None = None):
    """(cores, ram_gb, Rating) for this PC. Every input can be faked in tests."""
    c = cores if cores is not None else (os.cpu_count() or 0)
    rb = ram_bytes if ram_bytes is not None else (read_ram or read_ram_bytes)()
    gb = None if rb is None else rb / (1024 ** 3)
    return c, gb, rate_specs(c, gb)


def free_disk_gb(path) -> float | None:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / (1024 ** 3)
    except OSError:
        return None


# ---- the guard: a reworded tip must keep every fact --------------------------------------------------------------
DIRECTION_WORDS = ("earlier", "later", "sooner", "more", "less", "early", "late", "faster", "slower", "higher", "lower")
MAX_CHARS = 240
_CORNER = re.compile(r"\bT(\d+)\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_WORDS = re.compile(r"[A-Za-z']+")
_ENGLISH = {"the", "a", "an", "to", "you", "your", "at", "in", "on", "of", "for", "by", "is", "it", "and", "try", "with",
            "into", "through", "before", "after", "bit", "turn", "corner", "brake", "throttle", "speed", "lift", "carry",
            "get", "back", "more", "later", "earlier", "sooner", "than"}


def _num_key(s: str) -> str:
    f = float(s)
    return str(int(f)) if f == int(f) else repr(f)


def facts(text: str) -> tuple[frozenset, frozenset, frozenset]:
    """(numbers, corner labels, direction words) in a tip. Corner labels are removed before numbers are read."""
    corners = frozenset("T" + m for m in _CORNER.findall(text))
    rest = _CORNER.sub(" ", text)
    numbers = frozenset(_num_key(m) for m in _NUMBER.findall(rest))
    words = {w.lower() for w in _WORDS.findall(rest)}
    return numbers, corners, frozenset(w for w in DIRECTION_WORDS if w in words)


def looks_english(text: str) -> bool:
    if not text or sum(ord(c) < 128 for c in text) / len(text) < 0.97:
        return False
    words = [w.lower() for w in _WORDS.findall(text)]
    return len(words) >= 3 and any(w in _ENGLISH for w in words)


def guard(rules_text: str, candidate: str | None) -> tuple[str | None, str]:
    """(accepted wording, "") or (None, why it was rejected). Only text that keeps every number, corner label and
    direction word of the rules tip, in one short English sentence, is accepted."""
    if candidate is None:
        return None, "no answer"
    c = candidate.strip().strip('"“”').strip()
    if not c:
        return None, "empty"
    if "\n" in c or "\r" in c:
        return None, "more than one line"
    if len(c) > MAX_CHARS or len(c) > 3 * len(rules_text) + 40:
        return None, "too long"
    if len(re.findall(r"[.!?](?:\s+[A-Z])", c)) >= 1:
        return None, "more than one sentence"
    if not looks_english(c):
        return None, "not English"
    if facts(c) != facts(rules_text):
        return None, "numbers, corner labels or direction words changed"
    return c, ""


# ---- paths and availability --------------------------------------------------------------------------------------
def llm_dir() -> Path:
    """Model and runtime live next to settings.json, in the TRACE data folder."""
    from . import config
    return config.data_dir() / "llm"


def model_path(base: Path | None = None) -> Path:
    return (base or llm_dir()) / MODEL_FILE


def server_dir(base: Path | None = None) -> Path:
    return (base or llm_dir()) / "runtime" / SERVER_TAG


def find_server_exe(base: Path | None = None) -> Path | None:
    d = server_dir(base)
    if d.is_dir():
        for p in d.rglob(SERVER_EXE):
            return p
    return None


def has_llama_cpp() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("llama_cpp") is not None
    except Exception:
        return False


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_store_build() -> bool:
    try:
        from . import updates
        return bool(updates.is_store_install())
    except Exception:
        return False


@dataclass(frozen=True)
class Backend:
    kind: str          # "llama-cpp" | "server" | "none"
    reason: str = ""   # why "none"


def backend_for(*, llama_cpp: bool | None = None, frozen: bool | None = None, store: bool | None = None,
                platform: str | None = None) -> Backend:
    """Which backend this install can use. The downloadable llama-server is hidden in the Microsoft Store build:
    that build must not fetch or run extra programs (Store policies 10.2.2 and 10.2.3), and its install folder is
    read-only anyway."""
    lc = has_llama_cpp() if llama_cpp is None else llama_cpp
    if lc:
        return Backend("llama-cpp")
    fz = is_frozen() if frozen is None else frozen
    st = is_store_build() if store is None else store
    plat = platform or sys.platform
    if st:
        return Backend("none", "The Microsoft Store version of TRACE can't download or run extra programs, so local AI wording "
                               "isn't available in it. The downloadable TRACE.exe from GitHub supports it.")
    if fz and plat == "win32":
        return Backend("server")
    if fz:
        return Backend("none", "Local AI wording isn't available in this build on this system. "
                               "Install TRACE with pip and add the AI extra instead.")
    return Backend("none", "Local AI wording needs the llama-cpp-python package. Install it with:  pip install \"gt7tracetelem[ai]\"")


@dataclass(frozen=True)
class Status:
    backend: Backend
    model_ready: bool
    runtime_ready: bool
    download_bytes: int      # what confirming would download

    @property
    def available(self) -> bool:
        return self.backend.kind != "none"

    @property
    def ready(self) -> bool:
        return self.available and self.model_ready and (self.backend.kind != "server" or self.runtime_ready)


def status(base: Path | None = None, backend: Backend | None = None) -> Status:
    be = backend or backend_for()
    model_ok = model_path(base).is_file()
    runtime_ok = be.kind != "server" or find_server_exe(base) is not None
    need = (0 if model_ok else MODEL_BYTES) + (SERVER_BYTES if (be.kind == "server" and not runtime_ok) else 0)
    return Status(be, model_ok, runtime_ok, need)


def remove_model(base: Path | None = None) -> bool:
    """Delete the model and the runtime (Settings > Remove AI model). True when nothing is left behind."""
    d = base or llm_dir()
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


# ---- verified download -------------------------------------------------------------------------------------------
class DownloadError(Exception):
    pass


def _check_url(url: str, allow_local: bool) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme == "https":
        return
    if allow_local and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1"):
        return
    raise DownloadError(f"refusing to download from {url!r}: only https is allowed")


def download_verified(url: str, dest: Path, sha256: str, *, progress: Callable[[int, int], None] | None = None,
                      opener: Callable | None = None, allow_local: bool = False, chunk: int = 1 << 20,
                      cancel: Callable[[], bool] | None = None) -> Path:
    """Stream `url` to `dest`, check the SHA-256 and only then put it in place. A wrong hash deletes the partial file."""
    _check_url(url, allow_local)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    done = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TRACE"})
        with (opener or urllib.request.urlopen)(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                if cancel and cancel():
                    raise DownloadError("cancelled")
                b = r.read(chunk)
                if not b:
                    break
                f.write(b)
                h.update(b)
                done += len(b)
                if progress:
                    progress(done, total)
        if h.hexdigest().lower() != sha256.lower():
            raise DownloadError("the downloaded file does not match its expected checksum; it was deleted")
        os.replace(part, dest)
        return dest
    except DownloadError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as e:
        raise DownloadError(f"download failed: {e}") from e
    finally:
        part.unlink(missing_ok=True)


def extract_zip_safely(zip_path: Path, dest: Path) -> None:
    """Unzip without ever writing outside `dest` (no '..' or absolute paths)."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if dest != target and dest not in target.parents:
                raise DownloadError(f"unsafe path in the archive: {info.filename}")
        z.extractall(dest)


# ---- the model backends ------------------------------------------------------------------------------------------
class Reworder(Protocol):
    def reword(self, tip: str) -> str | None: ...
    def close(self) -> None: ...


def n_threads(cores: int | None = None) -> int:
    return max(1, min(MAX_THREADS, (cores or os.cpu_count() or 2) - 1))


def build_messages(tip: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": tip}]


class LlamaCppBackend:
    """llama-cpp-python, in this process. One generation at a time, with a hard timeout."""

    name = "llama-cpp"

    def __init__(self, model: Path, threads: int | None = None, llama_cls=None, timeout: float = TIMEOUT_S):
        if llama_cls is None:
            from llama_cpp import Llama as llama_cls  # type: ignore[import-not-found]
        self._llm = llama_cls(model_path=str(model), n_ctx=CONTEXT, n_threads=threads or n_threads(), verbose=False)
        self._timeout = timeout
        self._lock = threading.Lock()

    def reword(self, tip: str) -> str | None:
        out: list = []

        def work():
            try:
                r = self._llm.create_chat_completion(messages=build_messages(tip), temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                out.append(r["choices"][0]["message"]["content"])
            except Exception:
                out.append(None)

        with self._lock:
            t = threading.Thread(target=work, daemon=True)
            t.start()
            t.join(self._timeout)
            return out[0] if out else None     # still running after the timeout: give up on this tip

    def close(self) -> None:
        self._llm = None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _local(url: str) -> str:
    if urllib.parse.urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the AI server is only ever contacted on 127.0.0.1")
    return url


def _http_json(url: str, payload: dict | None, timeout: float):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_local(url), data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class ServerBackend:
    """A pinned llama-server started and stopped by TRACE, reached over 127.0.0.1 only."""

    name = "server"

    def __init__(self, exe: Path, model: Path, threads: int | None = None, popen=None, http=None, port: int | None = None,
                 timeout: float = TIMEOUT_S, start_timeout: float = 90.0, sleep=time.sleep):
        self._http = http or _http_json
        self._timeout = timeout
        self._lock = threading.Lock()
        self.port = port or _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        args = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port), "-c", str(CONTEXT),
                "-t", str(threads or n_threads())]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self.argv = args
        self._proc = (popen or subprocess.Popen)(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL, creationflags=flags)
        atexit.register(self.close)
        deadline = time.monotonic() + start_timeout
        while True:
            try:
                self._http(self.base + "/health", None, 2.0)
                break
            except Exception:
                if self._proc.poll() is not None or time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError("the local AI server did not start") from None
                sleep(0.5)

    def reword(self, tip: str) -> str | None:
        with self._lock:
            try:
                r = self._http(self.base + "/v1/chat/completions",
                               {"messages": build_messages(tip), "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
                                "stream": False}, self._timeout)
                return r["choices"][0]["message"]["content"]
            except Exception:
                return None

    def close(self) -> None:
        p, self._proc = getattr(self, "_proc", None), None
        if p is not None:
            try:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
            except Exception:
                pass


def create_backend(base: Path | None = None, backend: Backend | None = None) -> Reworder | None:
    """The real backend for this install, or None if it isn't ready (model or runtime not downloaded, no backend)."""
    be = backend or backend_for()
    mp = model_path(base)
    if be.kind == "llama-cpp" and mp.is_file():
        return LlamaCppBackend(mp)
    if be.kind == "server" and mp.is_file():
        exe = find_server_exe(base)
        if exe is not None:
            return ServerBackend(exe, mp)
    return None


# ---- applying the guard ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Worded:
    text: str
    ai: bool           # True only when the model's wording passed the guard
    why: str = ""      # why the rules text was kept


class TipWorder:
    """Rewords one tip at a time, falling back to the rules text whenever anything is off. Never raises."""

    def __init__(self, backend: Reworder | None = None, backend_factory: Callable[[], Reworder | None] | None = None,
                 enabled: Callable[[], bool] = lambda: True):
        self._backend, self._factory, self._enabled = backend, backend_factory, enabled
        self._failed = False
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return bool(self._enabled()) and not self._failed and (self._backend is not None or self._factory is not None)

    def reword(self, tip: str) -> Worded:
        if not self._enabled():
            return Worded(tip, False, "off")
        try:
            with self._lock:
                if self._backend is None and self._factory is not None and not self._failed:
                    self._backend = self._factory()
                    if self._backend is None:
                        self._failed = True
                be = self._backend
            if be is None:
                return Worded(tip, False, "no backend")
            ok, why = guard(tip, be.reword(tip))
        except Exception as e:
            self._failed = True
            return Worded(tip, False, f"error: {e}")
        return Worded(ok, True) if ok is not None else Worded(tip, False, why)

    def close(self) -> None:
        with self._lock:
            b, self._backend = self._backend, None
        if b is not None:
            try:
                b.close()
            except Exception:
                pass


_default: TipWorder | None = None


def enabled() -> bool:
    from . import config
    return bool(getattr(config, "LLM_ENABLED", False))


def default_worder() -> TipWorder:
    """The process-wide worder: follows the LLM_ENABLED setting and starts the backend on first use."""
    global _default
    if _default is None:
        _default = TipWorder(backend_factory=create_backend, enabled=enabled)
    return _default


def is_ready() -> bool:
    """True when AI wording is switched on AND the model (and runtime) are installed: the only case that does any work."""
    return enabled() and status().ready


# ---- coach tips, reworded -----------------------------------------------------------------------------------------
HEADER_RULES = "Coach:"
HEADER_AI = "Coach (AI wording):"


def describe_with_ai(tips, worder: TipWorder | None) -> tuple[str, bool]:
    """coach.describe(tips), with each tip's sentence replaced by the model's wording when it passed the guard.
    Returns (text, any tip reworded). With no worder, or nothing passing, it is exactly coach.describe(tips)."""
    from . import coach
    plain = coach.describe(tips)
    if worder is None or not tips:
        return plain, False
    out, used = [], False
    for t in tips:
        w = worder.reword(t.text)
        used = used or w.ai
        out.append(dataclasses.replace(t, text=w.text))
    if not used:
        return plain, False
    return coach.describe(out).replace(HEADER_RULES, HEADER_AI, 1), True


def reword_async(tips, log: Callable[[str], None], worder: TipWorder | None = None) -> threading.Thread | None:
    """After a lap: the rules text has already been logged; in the background, log the AI wording too, but only if some
    tip passed the guard. Does nothing unless AI wording is on and installed."""
    w = worder or default_worder()
    if not tips or not w.ready() or (worder is None and not is_ready()):
        return None

    def work():
        try:
            text, used = describe_with_ai(tips, w)
            if used:
                log(text)
        except Exception:
            pass

    t = threading.Thread(target=work, daemon=True, name="trace-ai-wording")
    t.start()
    return t


# ---- turning it on: warning first, download only after a yes -----------------------------------------------------
@dataclass(frozen=True)
class Warning:
    title: str
    body: str
    rating: Rating
    can_enable: bool
    download_mb: float
    free_gb: float | None


def build_warning(st: Status, cores: int, ram_gb: float | None, rating: Rating, free_gb: float | None) -> Warning:
    mb = st.download_bytes / 1_000_000
    lines = [
        "Local AI wording is optional and OFF by default. When it is on, a small language model on this PC rewords the "
        "coach's tips after each lap in friendlier language. The rules still decide what to say: a check discards the "
        "AI's sentence unless every number, corner (T4) and direction word is unchanged, and then you simply get the rules "
        "text. It never runs while you drive and it never invents advice.",
        "",
        "Before you turn it on:",
        "  - It needs a modest PC. On very old hardware it can make TRACE slow or the PC hot.",
        f"  - Your PC: {cores} CPU cores, " + (f"{ram_gb:.1f} GB RAM" if ram_gb is not None else "RAM unknown") + f"  ->  {rating.level.upper()}",
        f"    {rating.text}",
        f"  - One-time download: about {mb:.0f} MB" + ("" if st.download_bytes else " (already downloaded)")
        + f". Free disk space needed: about {NEED_FREE_GB:.1f} GB"
        + (f" (you have {free_gb:.1f} GB free)." if free_gb is not None else "."),
        "",
        "Recommended PC:",
        *("  " + ln for ln in RECOMMENDED_SPECS.splitlines()),
        "",
        f"{LLAMA_NOTICE}. Llama 3.2 is licensed under the {LLAMA_LICENSE_NAME}: {LLAMA_LICENSE_URL}",
    ]
    can = st.available and (free_gb is None or free_gb >= NEED_FREE_GB or st.download_bytes == 0)
    if not st.available:
        lines.insert(2, st.backend.reason)
        lines.insert(3, "")
    elif not can:
        lines.append("\nNot enough free disk space for the download.")
    return Warning("Turn on local AI wording?", "\n".join(lines), rating, can, mb, free_gb)


class EnableFlow:
    """The steps behind the LLM_ENABLED switch. ``request()`` only looks (no network, nothing written); ``confirm()``
    downloads and verifies; ``cancel()`` leaves everything as it was (Rules mode)."""

    def __init__(self, base: Path | None = None, backend: Backend | None = None, specs: Callable | None = None,
                 downloader: Callable = download_verified, opener: Callable | None = None, allow_local: bool = False):
        self.base, self._backend, self._specs = base, backend, specs
        self._dl, self._opener, self._allow_local = downloader, opener, allow_local
        self.downloaded: list[str] = []

    def request(self) -> Warning:
        st = status(self.base, self._backend)
        cores, ram, rating = (self._specs or check_specs)()
        return build_warning(st, cores, ram, rating, free_disk_gb(self.base or llm_dir()))

    def confirm(self, progress: Callable[[str, int, int], None] | None = None, cancel: Callable[[], bool] | None = None) -> Status:
        st = status(self.base, self._backend)
        if not st.available:
            raise DownloadError(st.backend.reason)
        base = self.base or llm_dir()
        kw = {"opener": self._opener, "allow_local": self._allow_local, "cancel": cancel}
        if not st.model_ready:
            self._dl(MODEL_URL, model_path(base), MODEL_SHA256,
                     progress=(lambda d, t: progress("model", d, t)) if progress else None, **kw)
            self.downloaded.append("model")
        if st.backend.kind == "server" and not st.runtime_ready:
            zp = base / "runtime" / SERVER_ZIP
            self._dl(SERVER_URL, zp, SERVER_SHA256,
                     progress=(lambda d, t: progress("runtime", d, t)) if progress else None, **kw)
            try:
                extract_zip_safely(zp, server_dir(base))
            finally:
                zp.unlink(missing_ok=True)
            if find_server_exe(base) is None:
                shutil.rmtree(server_dir(base), ignore_errors=True)
                raise DownloadError("the downloaded runtime did not contain llama-server")
            self.downloaded.append("runtime")
        return status(self.base, self._backend)


# ---- the Android rule (TRACE AI) -----------------------------------------------------------------------------------
# Phones report less memory than their box says, so the bands are lower than the PC's: red under 5 GB, amber 5 to
# under 7 GB, green 7 GB and up. android/telemetry/.../LlmGuard.kt ports this and is tested against it.
RECOMMENDED_PHONES = (
    "Minimum: a mid-range phone from about 2022 (Snapdragon 7-series class) with 6 GB of RAM. It works; replies take "
    "several seconds and the phone may warm up.\n"
    "Recommended: a Snapdragon 8-series class phone with 8 GB of RAM or more. Replies take 1-3 seconds."
)


def rate_phone(ram_gb: float) -> Rating:
    if ram_gb < 5:
        return Rating("red", f"This phone reports {ram_gb:.1f} GB of RAM and TRACE AI needs about 5 GB or more. "
                             "Use TRACE Lite instead: the same app without the AI.")
    if ram_gb < 7:
        return Rating("amber", f"This phone reports {ram_gb:.1f} GB of RAM. TRACE AI should work, but replies will take "
                               "several seconds and the phone may warm up.")
    return Rating("green", f"This phone reports {ram_gb:.1f} GB of RAM. Good to go.")
