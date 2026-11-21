"""fetch_ai_assets.py -- download the pinned AI model into the TRACE AI build's asset folder.

CI runs this before building the AI flavor of the Android app:

    python tools/fetch_ai_assets.py android/app/src/ai/assets

The model URL and SHA-256 are the ones the desktop app uses (src/gt7telem/llm.py), so both platforms carry the very
same file. The file is verified against the pinned SHA-256 every time; an existing file with the right checksum is kept
(that is what makes the CI cache work), anything else is replaced, and a download that does not match fails the build.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(dest_dir, url: str | None = None, sha256: str | None = None, name: str | None = None, allow_local: bool = False,
          say=print) -> Path:
    """The verified model file in `dest_dir`. Raises llm.DownloadError when the download does not match its checksum."""
    from gt7telem import llm
    url, sha256, name = url or llm.MODEL_URL, sha256 or llm.MODEL_SHA256, name or llm.MODEL_FILE
    dest = Path(dest_dir) / name
    if dest.is_file() and sha256_of(dest) == sha256.lower():
        say(f"{dest.name}: already present and verified")
        return dest
    dest.unlink(missing_ok=True)
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = done * 100 // total if total else 0
        if pct // 10 != last[0]:
            last[0] = pct // 10
            say(f"{dest.name}: {pct}%")
    llm.download_verified(url, dest, sha256, progress=progress, allow_local=allow_local)
    say(f"{dest.name}: downloaded and verified ({dest.stat().st_size} bytes)")
    return dest


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python tools/fetch_ai_assets.py <asset folder>", file=sys.stderr)
        return 2
    try:
        fetch(argv[0])
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
