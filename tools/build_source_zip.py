"""
build_source_zip.py -- produce gt7telem-source.zip, the "Python Source"
download offered on gt7trace.netlify.app/setup.html.

The three binary downloads are built by GitHub Actions on every version
tag; this one used to be assembled by hand, which is exactly why it drifted
a release behind (the site was still serving a 0.3.2 source zip after 0.3.3
shipped). Run this instead -- it always reads the live package source, so
the zip can't disagree with pyproject.toml.

Usage (from anywhere):

    python tools/build_source_zip.py                 # -> dist/gt7telem-source.zip
    python tools/build_source_zip.py -o <dir-or-zip> # write somewhere else

To refresh the website copy after cutting a release:

    python tools/build_source_zip.py -o ../gt7telemwebsite/gt7telem-source.zip

Layout produced (matches what setup.html and totalfiles.html describe):

    launcher.py        thin top-level wrapper -> gt7telem.launcher:main
    README.txt         setup instructions (tools/source_readme.txt)
    requirements.txt   generated from pyproject.toml's [project] dependencies
    gt7telem/...       the package itself, minus caches and local settings
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "gt7telem"
TOOLS = Path(__file__).resolve().parent

# Local-only files that must never end up in a public download: the user's
# own saved settings, and the key that encrypts their Supabase session.
EXCLUDE_NAMES = {"settings.json", ".settings.key", "track_boundaries.json"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_DIRS = {"__pycache__"}


def package_version() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def requirements_text() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    return "\n".join(deps) + "\n"


def package_files() -> list[Path]:
    out = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in p.relative_to(PKG).parts):
            continue
        if p.name in EXCLUDE_NAMES or p.suffix in EXCLUDE_SUFFIXES:
            continue
        out.append(p)
    return out


def check_version_sync() -> str:
    """The zip is worthless if __init__ and pyproject disagree -- that exact
    drift has bitten this project twice (see CHANGELOG 0.1.3 and 0.2.2)."""
    version = package_version()
    init = (PKG / "__init__.py").read_text(encoding="utf-8")
    marker = f'__version__ = "{version}"'
    if marker not in init:
        sys.exit(
            f"ERROR: pyproject.toml says {version} but src/gt7telem/__init__.py "
            f"does not contain {marker!r}. Sync them before building."
        )
    return version


def build(out_path: Path) -> Path:
    version = check_version_sync()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("launcher.py", (TOOLS / "source_launcher.py").read_text(encoding="utf-8"))
        z.writestr("README.txt", (TOOLS / "source_readme.txt").read_text(encoding="utf-8"))
        z.writestr("requirements.txt", requirements_text())
        for f in package_files():
            z.write(f, f"gt7telem/{f.relative_to(PKG).as_posix()}")

    with zipfile.ZipFile(out_path) as z:
        names = z.namelist()
    print(f"Built {out_path}  (v{version}, {len(names)} files, {out_path.stat().st_size:,} bytes)")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-o", "--output", default=None,
        help="output .zip path, or a directory to write gt7telem-source.zip into "
             "(default: dist/gt7telem-source.zip)",
    )
    args = ap.parse_args()

    if args.output is None:
        out = REPO / "dist" / "gt7telem-source.zip"
    else:
        out = Path(args.output).resolve()
        if out.is_dir() or not out.suffix:
            out = out / "gt7telem-source.zip"
    build(out)


if __name__ == "__main__":
    main()
