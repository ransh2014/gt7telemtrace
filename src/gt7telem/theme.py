"""theme.py -- the TRACE website's design tokens for the desktop windows.

Colours and font families are taken verbatim from the website's ``assets/styles.css`` (:root block), so the
desktop dashboard, launcher and analysts match trace's site: obsidian surfaces, one signature red, quiet teal,
muted racing green and gold.

Fonts: Anton (impact), Sora (display), Manrope (body) and JetBrains Mono (data) -- the same four families the
site loads from Google Fonts. Tk can't read web fonts, so TTF copies placed in ``gt7telem/fonts/`` are
registered privately for this process (Windows; no install needed). Any family that isn't available falls back
to the platform's own font, so a missing file never breaks the UI. Never imports tkinter (headless-safe).
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── Surfaces ──────────────────────────────────────────────────────────────────
BG    = "#08080a"   # --bg
BG1   = "#0e0e11"   # --bg-1   (panels)
BG2   = "#17171b"   # --bg-2   (raised panels / header bars)
BG3   = "#202024"   # --bg-3   (inputs, buttons, grid lines)
CBG   = "#050506"   # --cbg    (canvas / code wells)
LINE  = "#1c1c1e"   # --line   (rgba(255,255,255,.08) over --bg)
LINE2 = "#2f2f31"   # --line-2 (rgba(255,255,255,.16) over --bg)

# ── Accents ───────────────────────────────────────────────────────────────────
RED      = "#c8102e"   # --orange (the signature team red)
RED_DIM  = "#8c0c22"   # --orange-dim
RED_2    = "#e2455a"   # --orange-2 (lighter red, legible as text)
TEAL     = "#1c5c58"   # --blue
TEAL_2   = "#5fb3a8"   # --blue-2 (links / data)
GREEN    = "#3c9a5f"   # --green  (live / throttle)
GOLD     = "#c9a24b"   # --gold

# ── Text ──────────────────────────────────────────────────────────────────────
TXT = "#f2f0ec"   # --txt
MID = "#96949c"   # --mid
DIM = "#605e68"   # --dim

# ── Font files (TTF) bundled next to this module ──────────────────────────────
FONT_DIR = Path(__file__).with_name("fonts")
_FONT_FILES = {
    "Anton": ["Anton-Regular.ttf"],
    "Sora": ["Sora-Regular.ttf", "Sora-Bold.ttf"],
    "Manrope": ["Manrope-Regular.ttf", "Manrope-Bold.ttf"],
    "JetBrains Mono": ["JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"],
}


def _platform_mono(platform: str = sys.platform) -> str:
    if platform.startswith("win"):
        return "Consolas"
    if platform == "darwin":
        return "Menlo"
    return "monospace"


def _platform_sans(platform: str = sys.platform) -> str:
    if platform.startswith("win"):
        return "Segoe UI"
    if platform == "darwin":
        return "Helvetica Neue"
    return "sans-serif"


def _register(path: Path) -> bool:
    """Make one font file usable by this process only (nothing is installed system-wide).
    Windows: GDI private font. macOS: CoreText process-scope registration. Linux: fontconfig app font."""
    try:
        import ctypes

        if sys.platform.startswith("win"):
            FR_PRIVATE = 0x10
            return bool(ctypes.windll.gdi32.AddFontResourceExW(str(path), FR_PRIVATE, 0))
        if sys.platform == "darwin":
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
            ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
            ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            raw = str(path).encode()
            url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            # kCTFontManagerScopeProcess = 1; True also when it is already registered
            return bool(url) and bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.restype = ctypes.c_int
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        return bool(fc.FcConfigAppFontAddFile(None, str(path).encode()))
    except Exception:
        return False


def load_fonts() -> dict[str, bool]:
    """Register every bundled TTF; returns {family: usable}. Safe to call repeatedly or with no files."""
    got: dict[str, bool] = {}
    for family, files in _FONT_FILES.items():
        ok = False
        for name in files:
            p = FONT_DIR / name
            if p.is_file():
                ok = _register(p) or ok
        got[family] = ok
    return got


_LOADED = load_fonts()


def _pick(family: str, fallback: str) -> str:
    return family if _LOADED.get(family) else fallback


IMPACT  = _pick("Anton", _platform_mono())            # big numerals (gear, speed)
DISPLAY = _pick("Sora", _platform_sans())             # titles / headings
BODY    = _pick("Manrope", _platform_sans())          # prose
MONO    = _pick("JetBrains Mono", _platform_mono())   # data, labels, eyebrows


def mpl_font_family() -> str:
    """Family for matplotlib's rcParams (registers the bundled TTFs with matplotlib too)."""
    if _LOADED.get("JetBrains Mono"):
        try:
            from matplotlib import font_manager

            for name in _FONT_FILES["JetBrains Mono"]:
                p = FONT_DIR / name
                if p.is_file():
                    font_manager.fontManager.addfont(str(p))
            return "JetBrains Mono"
        except Exception:
            pass
    return "monospace"


# ── DPI ────────────────────────────────────────────────────────────────────────
SCALE = 1.0   # display scale factor once the process is DPI-aware (1.0 otherwise); see enable_dpi_awareness()


def enable_dpi_awareness() -> float:
    """Windows only: draw at the screen's real resolution instead of letting Windows bitmap-stretch (blur) the
    window at 125/150/175 % scaling. Must run before the first Tk window. Sets and returns SCALE; pixel sizes in
    the UI go through px() so layouts keep their proportions. No-op elsewhere (macOS/Linux scale themselves)."""
    global SCALE
    if not sys.platform.startswith("win"):
        return SCALE
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system-DPI aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
        SCALE = max(1.0, dpi / 96.0)
    except Exception:
        SCALE = 1.0
    return SCALE


def px(n):
    """A pixel size designed at 100% scaling, converted for this display (see enable_dpi_awareness)."""
    return int(round(n * SCALE))


# ── Window helpers (tkinter objects are passed in; this module never imports tkinter at load) ──────────────────
def fit_window(win, width: int, height: int, min_w: int = 0, min_h: int = 0) -> None:
    """Size `win` to the wanted size but never larger than the screen (leaving room for the taskbar and title
    bar), centre it, and clamp its minimum size the same way. On a scaled display (e.g. 150% -> a 1280x720 logical
    screen) a fixed 1280x920 window used to hang off the bottom and hide controls."""
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    width, height, min_w, min_h = px(width), px(height), px(min_w), px(min_h)
    w, h = min(width, sw - px(16)), min(height, sh - px(90))
    win.minsize(min(min_w or w, w), min(min_h or h, h))
    win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h - px(60)) // 2)}")


def style_ttk(root) -> None:
    """Dark TRACE look for ttk widgets (progress bars, scrollbars, combo boxes, tables). The native Windows/macOS
    ttk themes ignore colours, which is why bars and tables showed up white; 'clam' honours them."""
    from tkinter import ttk

    s = ttk.Style(root)
    s.theme_use("clam")
    for orient in ("Vertical", "Horizontal"):
        s.configure(f"{orient}.TScrollbar", troughcolor=BG1, background=BG3, bordercolor=BG1,
                    lightcolor=BG3, darkcolor=BG3, arrowcolor=MID, relief="flat")
        s.map(f"{orient}.TScrollbar", background=[("active", DIM)])
    s.configure("Horizontal.TProgressbar", troughcolor=BG3, background=RED, bordercolor=BG3,
                lightcolor=RED, darkcolor=RED)
    s.configure("TCombobox", fieldbackground=BG3, background=BG3, foreground=TXT, arrowcolor=MID,
                bordercolor=LINE2, lightcolor=BG3, darkcolor=BG3, selectbackground=BG3, selectforeground=TXT)
    s.map("TCombobox", fieldbackground=[("readonly", BG3)], foreground=[("readonly", TXT)],
          selectbackground=[("readonly", BG3)], selectforeground=[("readonly", TXT)])
    s.configure("Treeview", background=CBG, fieldbackground=CBG, foreground=TXT, bordercolor=LINE,
                lightcolor=CBG, darkcolor=CBG)
    s.configure("Treeview.Heading", background=BG1, foreground=RED_2, relief="flat", bordercolor=LINE)
    root.option_add("*TCombobox*Listbox.background", BG3)
    root.option_add("*TCombobox*Listbox.foreground", TXT)
    root.option_add("*TCombobox*Listbox.selectBackground", RED)
    root.option_add("*TCombobox*Listbox.selectForeground", TXT)


def bar_style(root, name: str, colour: str) -> str:
    """A progress-bar style filled with `colour`; returns the style name to pass as style=."""
    from tkinter import ttk

    style = f"{name}.Horizontal.TProgressbar"
    ttk.Style(root).configure(style, troughcolor=BG3, background=colour, bordercolor=BG3,
                              lightcolor=colour, darkcolor=colour)
    return style


def reflow(frame, pad: int = 6) -> None:
    """Turn a frame whose children were pack()ed in a row into one that wraps onto extra rows when the window is
    narrower than the row. Visual order is kept (left-packed first, then right-packed, last-created leftmost)."""
    kids = list(frame.winfo_children())
    lefts, rights = [], []
    for w in kids:
        try:
            side = w.pack_info().get("side", "left")
        except Exception:
            continue
        (rights if side == "right" else lefts).append(w)
    order = lefts + rights[::-1]
    for w in order:
        w.pack_forget()
    sig = {"v": None}

    def layout(_e=None):
        try:
            width = frame.winfo_width()
            key = (width, tuple(w.winfo_reqwidth() for w in order))
            if width <= 1 or key == sig["v"]:
                return
            sig["v"] = key
            x = row = 0
            rowh = max(w.winfo_reqheight() for w in order) + 2
            for w in order:
                need = w.winfo_reqwidth() + pad
                if x and x + need > width:
                    row, x = row + 1, 0
                w.place(x=x, y=row * rowh + 2)   # place (not grid): rows must not share column widths
                x += need
            frame.configure(height=(row + 1) * rowh + 4)
        except Exception:
            pass

    def later(_e=None):
        frame.after_idle(layout)

    frame.bind("<Configure>", later)
    for w in order:
        w.bind("<Configure>", later, add="+")
    later()
