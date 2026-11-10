# PyInstaller spec for the Windows TRACE.exe (used by .github/workflows/build-windows.yml).
#
#   pyinstaller --noconfirm packaging/TRACE-windows.spec      (run from the repo root, after `pip install .`)
#
# Same output as the old command line: ONE windowed TRACE.exe at dist/TRACE.exe (the MSIX workflow packs that exact
# file as-is). What this spec adds is size trimming, and every trim below was checked against the code first:
#   * modules no TRACE code (or the libraries it uses) imports -- see the grep notes next to each group;
#   * data files TRACE never loads (matplotlib sample data, PDF/PS fonts, math fonts, Tcl timezone and locale data);
#   * optional Pillow codecs (AVIF, WebP, colour management) that Pillow loads only on demand, inside try/except.
# Not used, on purpose: UPX or any other exe packer (antivirus false positives, Microsoft Store certification).
# TRACE's own fonts (gt7telem/fonts) are always kept.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

import gt7telem

PKG_DIR = os.path.dirname(gt7telem.__file__)

# ── modules we never import ──────────────────────────────────────────────────
EXCLUDES = [
    # GUI toolkits other than Tk (TRACE is tkinter + matplotlib TkAgg only)
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "sip", "wx", "gi", "cairo", "cairocffi",
    "IPython", "ipykernel", "jupyter", "notebook", "nbformat",
    # matplotlib backends other than Agg / TkAgg (lap_analyst + race_analyst call matplotlib.use("TkAgg"),
    # share_card renders with backend_agg; every savefig is format="png")
    "matplotlib.backends.backend_qt", "matplotlib.backends.backend_qt5", "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg", "matplotlib.backends.backend_qtcairo", "matplotlib.backends.backend_qt5cairo",
    "matplotlib.backends.qt_compat", "matplotlib.backends.qt_editor",
    "matplotlib.backends.backend_gtk3", "matplotlib.backends.backend_gtk3agg", "matplotlib.backends.backend_gtk3cairo",
    "matplotlib.backends.backend_gtk4", "matplotlib.backends.backend_gtk4agg", "matplotlib.backends.backend_gtk4cairo",
    "matplotlib.backends.backend_wx", "matplotlib.backends.backend_wxagg", "matplotlib.backends.backend_wxcairo",
    "matplotlib.backends.backend_macosx", "matplotlib.backends.backend_webagg", "matplotlib.backends.backend_webagg_core",
    "matplotlib.backends.backend_nbagg", "matplotlib.backends.backend_cairo", "matplotlib.backends.backend_template",
    "matplotlib.backends.backend_pdf", "matplotlib.backends.backend_pgf", "matplotlib.backends.backend_ps",
    "matplotlib.backends.backend_svg", "matplotlib.testing", "matplotlib.tests",
    # Tk / stdlib test suites and dev tools
    "test", "tkinter.test", "tkinter.tix", "idlelib", "turtle", "turtledemo", "lib2to3", "pydoc", "pydoc_data",
    "doctest", "pdb", "ensurepip",   # (unittest stays: pyparsing.testing, used by matplotlib, imports it)
    "venv", "distutils", "setuptools", "pkg_resources", "pip",
    # numpy / pandas extras (TRACE uses plain arrays and DataFrames; no Fortran, no testing helpers, no SQL/Excel)
    "numpy.f2py", "numpy.distutils", "numpy.tests", "numpy._pyinstaller",
    "pandas.tests", "pandas.conftest",   # (pandas._testing / numpy.testing stay: `import pandas` loads them)
    "scipy", "sqlalchemy", "sqlite3", "pyarrow", "tables", "openpyxl", "xlrd", "xlsxwriter", "odf", "bs4", "lxml",
    "html5lib", "jinja2", "fsspec", "gcsfs", "s3fs", "numexpr", "bottleneck", "numba", "xarray", "zstandard",
    # tzdata: TRACE only uses datetime.timezone.utc (analytics.py, leaderboard.py), never named zones
    "tzdata",
    # Pillow: only what matplotlib needs for PNG (Image, PngImagePlugin, ImageTk, features). AVIF / WebP / CMS
    # plugins import their C module inside try/except, so leaving them out just marks those formats unsupported.
    "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL.ImageCms", "PIL._imagingcms",
    "PIL.ImageQt", "PIL.ImageShow", "PIL.ImageGrab",
    # pycryptodome: TRACE uses only Crypto.Cipher.AES (GCM, config.py) and Crypto.Cipher.Salsa20 (udp.py)
    "Crypto.PublicKey", "Crypto.Signature", "Crypto.Protocol", "Crypto.Math", "Crypto.IO", "Crypto.SelfTest",
]

# ── data files we never load (matched on the destination path inside the exe) ──
_MPL_FONTS = "matplotlib/mpl-data/fonts/"
_KEEP_MPL_FONTS = {   # DejaVu Sans (+ Mono, used by family="monospace" text) is matplotlib's fallback font
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf",
    "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf",
    "DejaVuSansDisplay.ttf", "LastResortHE-Regular.ttf", "LICENSE_DEJAVU",
}
_DROP_PREFIXES = (
    "matplotlib/mpl-data/sample_data/",    # example datasets for the matplotlib docs
    "matplotlib/mpl-data/plot_directive/",  # Sphinx extension
    "matplotlib/mpl-data/kpsewhich.lua",   # LaTeX (usetex) helper; TRACE never sets usetex
    "_tcl_data/tzdata/",                   # Tcl `clock` time zones; TRACE never calls Tcl's clock
    "_tcl_data/msgs/", "_tk_data/msgs/",   # Tcl/Tk translations for Tk's own dialogs (Windows uses native ones)
    "_tk_data/images/",                    # Tk demo logos, not used by Tk itself
    "tzdata/",
)
_DROP_BINARIES = ("PIL/_avif", "PIL/_webp", "PIL/_imagingcms", "Crypto/PublicKey/", "Crypto/Math/",
                  "Crypto/Signature/", "Crypto/Protocol/", "Crypto/IO/", "sqlite3.dll", "_sqlite3")


def _dropped(dest: str) -> bool:
    d = dest.replace("\\", "/")
    if d.startswith(_MPL_FONTS):            # matplotlib's PDF/PS core fonts, STIX and Computer Modern math fonts
        return os.path.basename(d) not in _KEEP_MPL_FONTS
    return d.startswith(_DROP_PREFIXES) or d.startswith(_DROP_BINARIES)


a = Analysis(
    [os.path.abspath(os.environ.get("TRACE_ENTRYPOINT", "entrypoint.py"))],   # relative to the working directory
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(PKG_DIR, "car_ids.csv"), "gt7telem"),
        (os.path.join(PKG_DIR, "course_ids.csv"), "gt7telem"),
        (os.path.join(PKG_DIR, "fonts"), "gt7telem/fonts"),   # every TRACE font + OFL.txt, untouched
    ] + collect_data_files("textual") + collect_data_files("textual_plotext") + collect_data_files("plotext"),
    # the terminal edition (gt7telem/tui): Textual loads its .tcss styles and widgets by name, so keep all of it
    hiddenimports=(collect_submodules("websockets") + collect_submodules("textual")
                   + collect_submodules("textual_plotext") + collect_submodules("plotext")
                   + collect_submodules("rich") + collect_submodules("gt7telem")),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=int(os.environ.get("TRACE_OPTIMIZE", "2")),   # -OO: docstrings and asserts stripped (tests pass with -OO)
)
a.datas = [d for d in a.datas if not _dropped(d[0])]
a.binaries = [b for b in a.binaries if not _dropped(b[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=os.environ.get("TRACE_EXE_NAME", "TRACE"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,      # Windows wheels ship release PE files without debug symbols; GNU strip would corrupt them
    upx=False,        # never: antivirus false positives and Microsoft Store certification flags
    upx_exclude=[],
    runtime_tmpdir=None,
    console=os.environ.get("TRACE_CONSOLE") == "1",   # TRACE.exe is windowed; the console switch is for build checks
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
