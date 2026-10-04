"""Docs must agree with the code about how many chart groups there are --
"17 chart groups" survived in four places after Corners was added."""
import re
from pathlib import Path

from gt7telem import lap_analyst, race_analyst

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_lap_analyst_chart_group_count_in_docs():
    n = len(lap_analyst.GROUPS)
    for rel in ("README.md", "tools/source_readme.txt", "choco/gt7telem.nuspec"):
        lines = [ln for ln in _read(rel).splitlines() if "Race analysis" not in ln]
        counts = set(re.findall(r"(\d+) chart groups", "\n".join(lines)))
        assert counts <= {str(n)} and counts, f"{rel} says {counts}, code has {n}"


def test_race_analyst_chart_group_count_in_docs():
    n = len(race_analyst.GROUPS)
    m = re.search(r"race_analyst\.py\s+Race analysis — (\d+) chart groups", _read("tools/source_readme.txt"))
    assert m and int(m.group(1)) == n


def test_source_readme_lists_every_module():
    text = _read("tools/source_readme.txt")
    for f in (ROOT / "src" / "gt7telem").glob("*.py"):
        if f.name in ("__init__.py", "add_car.py", "add_track.py", "cars.py", "tracks.py"):
            continue
        assert f.name in text, f"{f.name} missing from tools/source_readme.txt"
