"""The Tk windows and the terminal UI must show the same things. These tests fail the build when one front end
gets a feature, chart group, tool or label the other doesn't, or when analysis creeps into a renderer.

Model: tests/test_android_parity.py does the same job for the Android app."""
import re
from pathlib import Path

from gt7telem import analysis_tools, labels, lap_analyst, lap_charts, race_analyst, race_charts
from gt7telem.core import registry

SRC = Path(__file__).resolve().parents[1] / "src" / "gt7telem"


def _read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


def test_both_analysts_draw_the_very_same_chart_groups_as_the_registry():
    # Tk uses the lists from the shared modules directly (not copies), and the registry reads the same lists
    assert lap_analyst.GROUPS is lap_charts.GROUPS and race_analyst.GROUPS is race_charts.GROUPS
    assert registry.lap_groups() == [g[0] for g in lap_charts.GROUPS]
    assert registry.race_groups() == [g[0] for g in race_charts.GROUPS]


def test_chart_tab_names_in_labels_match_the_registry():
    assert labels.LAP_CHART_TABS == registry.lap_groups()
    assert labels.RACE_CHART_TABS == registry.race_groups()


def test_tk_sidebars_build_their_tool_buttons_from_the_registry():
    assert "registry.LAP_TOOLS" in _read("lap_analyst.py")
    assert "registry.RACE_TOOLS" in _read("race_analyst.py")
    assert "registry.REPLAY_EXPORT_LABEL" in _read("lap_analyst.py")


def test_terminal_ui_builds_from_the_registry():
    text = _read("tui/app.py")
    for needle in ("registry.lap_groups()", "registry.race_groups()", "registry.LAP_TOOLS", "registry.RACE_TOOLS",
                   "registry.SETTINGS"):
        assert needle in text, f"the terminal UI no longer reads {needle}"


def test_every_tool_kind_in_the_registry_has_an_implementation():
    for kind, _label, _c in registry.LAP_TOOLS:
        assert kind in ("coach", "setup", "dna", "plan", "validate", "dyno")
    import inspect
    lap_src = inspect.getsource(analysis_tools.lap_tool)
    for kind, _label, _c in registry.LAP_TOOLS:
        assert f'"{kind}"' in lap_src, f"analysis_tools.lap_tool has no branch for {kind}"
    race_src = inspect.getsource(analysis_tools.race_report)
    for kind, _label, _c in registry.RACE_TOOLS:
        assert f'"{kind}"' in race_src


def test_settings_wording_comes_from_labels_py():
    shared = set(labels.shared().values())
    for _key, label in registry.SETTINGS:
        assert label in shared, f"{label!r} is not a labels.py string"


def test_renderers_hold_no_analysis():
    """pandas / numpy / matplotlib / the analysis modules belong in the core, not in a renderer."""
    forbidden = re.compile(r"^\s*(import|from)\s+(pandas|numpy|matplotlib|scipy)\b", re.M)
    for f in (SRC / "tui").glob("*.py"):
        assert not forbidden.search(f.read_text(encoding="utf-8")), f"{f.name} imports a numeric library"
    tui = _read("tui/app.py") + _read("tui/chartview.py")
    for mod in ("corners", "coach", "setup_advisor", "driver_dna", "planner", "validate", "dyno"):
        assert not re.search(rf"^\s*from\s+\.\.\s+import\s+.*\b{mod}\b", tui, re.M), f"tui imports {mod} directly"


def test_chart_modules_and_the_core_have_no_tkinter():
    for f in [SRC / "lap_charts.py", SRC / "race_charts.py", SRC / "analysis_tools.py", *(SRC / "core").glob("*.py"),
              *(SRC / "tui").glob("*.py")]:
        src = f.read_text(encoding="utf-8")
        assert "import tkinter" not in src and "from tkinter" not in src, f.name


def test_source_readme_lists_the_terminal_ui():
    text = (Path(__file__).resolve().parents[1] / "tools" / "source_readme.txt").read_text(encoding="utf-8")
    assert "tui/" in text
