"""release_notes.py -- print the CHANGELOG.md section for a release tag, for use as the GitHub Release text.

    python tools/release_notes.py v1.0.1      # -> the "## [1.0.1] - ..." section, without its heading

The release workflow (build-source.yml) runs this on every version tag so the GitHub release, the changelog
and the website can't disagree. If the changelog has no section for the tag it prints a short fallback and
exits 0 (a missing note must never block a release); the workflow log shows a warning.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section_for(tag: str, text: str) -> str | None:
    """The body of the `## [x.y.z]` section for `tag` ("v1.0.1" or "1.0.1"), or None when there is none."""
    version = tag.lstrip("vV")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, flags=re.M | re.S)
    if not m:
        return None
    body = m.group(1).strip()
    return body or None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py <tag>", file=sys.stderr)
        return 2
    tag = argv[1]
    body = section_for(tag, CHANGELOG.read_text(encoding="utf-8"))
    if body is None:
        print(f"::warning::CHANGELOG.md has no section for {tag}", file=sys.stderr)
        body = f"TRACE {tag}. See [CHANGELOG.md](https://github.com/ransh2014/gt7telemtrace/blob/main/CHANGELOG.md)."
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
