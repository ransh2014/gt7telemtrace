"""Golden snapshots of every Lap and Race chart group, taken through the same headless path the terminal UI uses.

If a chart's data legitimately changes, regenerate with:  UPDATE_GOLDEN=1 python -m pytest tests/test_core_charts.py
and review the diff of golden_charts.json. An unexpected change means a refactor altered what a chart shows."""
import json
import os
import warnings
from pathlib import Path

import pytest
from _fixtures import full_lap_data, full_race_data, slower_lap_data

from gt7telem import lap_charts, race_charts
from gt7telem.core import charts, figspec, registry

warnings.filterwarnings("ignore")
GOLDEN = Path(__file__).with_name("golden_charts.json")


@pytest.fixture(scope="module")
def frames():
    return {
        "lap": lap_charts.load_lap_data(full_lap_data())[1],
        "lap_b": lap_charts.load_lap_data(slower_lap_data())[1],
        "race": race_charts.load_race_data(full_race_data())[1],
    }


def _summary(spec):
    return {
        "axes": len(spec["axes"]),
        "lines": sum(len(a["lines"]) for a in spec["axes"]),
        "titles": [a["title"] for a in spec["axes"]],
        "fp": figspec.fingerprint(spec),
    }


def _all(frames):
    out = {}
    for n in registry.lap_groups():
        out[f"lap/{n}"] = _summary(charts.lap_chart(n, frames["lap"], frames["lap_b"], consensus=[], pool=None))
    for n in registry.race_groups():
        out[f"race/{n}"] = _summary(charts.race_chart(n, frames["race"]))
    return out


def test_every_group_renders_headlessly_and_matches_golden(frames):
    got = _all(frames)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True), encoding="utf-8")
        pytest.skip("golden updated")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(got) == set(want), "chart groups were added or removed: regenerate the golden file on purpose"
    for k in sorted(got):
        assert got[k]["axes"] == want[k]["axes"] and got[k]["titles"] == want[k]["titles"], f"{k}: layout changed"
        assert got[k]["lines"] == want[k]["lines"], f"{k}: number of lines changed"
        assert got[k]["fp"] == want[k]["fp"], f"{k}: chart data changed"


def test_rendering_is_deterministic(frames):
    a = figspec.fingerprint(charts.lap_chart("Inputs", frames["lap"], frames["lap_b"]))
    b = figspec.fingerprint(charts.lap_chart("Inputs", frames["lap"], frames["lap_b"]))
    assert a == b


def test_spec_is_plain_json(frames):
    spec = charts.lap_chart("Tyres", frames["lap"])
    json.dumps(spec)                               # no numpy types or artists leak out
    assert spec["axes"] and all("title" in a for a in spec["axes"])


def test_charts_modules_do_not_import_tkinter():
    for mod in (lap_charts, race_charts):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import tkinter" not in src and "from tkinter" not in src, mod.__name__
