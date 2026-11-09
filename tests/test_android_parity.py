"""The Android app must say exactly what the desktop app says.

``src/gt7telem/labels.py`` is the one place the shared UI wording lives. These tests fail -- and so does CI --
when a desktop label is added or changed and Android's ``strings.xml`` (generated from it) or Kotlin code does not
follow, when the chart tabs differ, when old wording creeps back in, or when Android asks for more than INTERNET
permission-wise than it should. To fix a failure:  edit labels.py, run ``python tools/gen_android_strings.py``,
and use the new ``R.string.<name>`` in the Kotlin screen.
"""
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gt7telem import labels

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "app" / "src" / "main"
STRINGS = ANDROID / "res" / "values" / "strings.xml"
KOTLIN = sorted((ANDROID / "kotlin").rglob("*.kt"))
DESKTOP = [p for p in (ROOT / "src" / "gt7telem").glob("*.py") if p.name != "labels.py"]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_android_strings", ROOT / "tools" / "gen_android_strings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _android_value(raw: str) -> str:
    """strings.xml text -> the plain text Android shows (quotes and escapes removed), with %n$s -> {n}."""
    t = raw
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        t = t[1:-1]
    t = t.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return re.sub(r"%\d\$s", "{}", t)


def _desktop_value(text: str) -> str:
    return re.sub(r"\{[a-z_]+\}", "{}", text)


@pytest.fixture(scope="module")
def android_strings():
    root = ET.parse(STRINGS).getroot()
    return {e.get("name"): _android_value(e.text or "") for e in root.findall("string")}


def test_strings_xml_is_up_to_date_with_labels():
    assert STRINGS.read_text(encoding="utf-8") == _gen().render(), \
        "strings.xml is stale: run  python tools/gen_android_strings.py"


def test_every_desktop_label_is_in_android_strings_verbatim(android_strings):
    missing, wrong = [], []
    for name, text in labels.shared().items():
        key = name.lower()
        if key not in android_strings:
            missing.append(name)
        elif android_strings[key] != _desktop_value(text):
            wrong.append((name, text, android_strings[key]))
    assert not missing, f"labels missing from Android strings.xml: {missing}"
    assert not wrong, f"Android text differs from the desktop (name, desktop, android): {wrong}"


def test_android_code_actually_uses_every_label():
    src = "\n".join(p.read_text(encoding="utf-8") for p in KOTLIN)
    unused = [n for n in labels.shared() if f"R.string.{n.lower()}" not in src]
    assert not unused, f"labels not used by any Android screen (R.string.x): {unused}"


def test_desktop_uses_or_contains_every_label():
    """labels.py must not drift from the desktop windows either: each label is used as L.NAME or still appears
    verbatim as a literal in the desktop code."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in DESKTOP)
    lost = []
    for name, text in labels.shared().items():
        if re.search(rf"\b(?:L|labels)\.{name}\b", src):
            continue
        if name == "WINDOW_TITLE_FMT" and "window_title(" in src:   # used through labels.window_title()
            continue
        if "{" in text or text not in src:
            lost.append(name)
    assert not lost, f"labels neither used nor present in the desktop code: {lost}"


def _kotlin_list(path, marker):
    txt = path.read_text(encoding="utf-8")
    block = txt[txt.index(marker):]
    block = block[:block.index(")\n")]
    return re.findall(r'"([^"]+)"', block)


def test_lap_chart_tabs_match_desktop_and_android():
    from gt7telem import lap_analyst
    desktop = [g[0] for g in lap_analyst.GROUPS]
    assert desktop == labels.LAP_CHART_TABS
    assert _kotlin_list(ANDROID / "kotlin" / "app" / "trace" / "charts" / "LapCharts.kt", "val GROUPS") == desktop


def test_race_chart_tabs_match_desktop_and_android():
    from gt7telem import race_analyst
    desktop = [g[0] for g in race_analyst.GROUPS]
    assert desktop == labels.RACE_CHART_TABS
    assert _kotlin_list(ANDROID / "kotlin" / "app" / "trace" / "charts" / "RaceCharts.kt", "val GROUPS") == desktop


def test_tool_titles_follow_the_desktop_pattern():
    assert labels.window_title(labels.TOOL_LIVE, "1.0.3") == "TRACE 1.0.3 - Live Dashboard"
    assert labels.window_title(labels.TOOL_LAP, "1.0.3") == "TRACE 1.0.3 - Lap Analyst"
    assert labels.window_title(labels.TOOL_RACE, "1.0.3") == "TRACE 1.0.3 - Race Analyst"
    assert (labels.TITLE_LIVE, labels.TITLE_LAP, labels.TITLE_RACE) == ("LIVE DASHBOARD", "LAP ANALYST", "RACE ANALYST")
    assert labels.HEADER_BRAND == "GT7 TRACE"


def test_no_old_wording_in_android():
    banned = ("GT7 Telemetry Tool", "Telemetry Tool", "TRACE Dashboard", "Full-race lap splits")
    src = "\n".join(p.read_text(encoding="utf-8") for p in KOTLIN) + STRINGS.read_text(encoding="utf-8")
    assert not [b for b in banned if b in src]


def test_only_declared_permissions_and_internet_present():
    manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
    perms = set(re.findall(r'uses-permission android:name="android.permission.([A-Z_]+)"', manifest))
    assert "INTERNET" in perms
    # the permissions the app already had; a new one needs a deliberate decision, not a silent addition
    assert perms <= {"INTERNET", "ACCESS_NETWORK_STATE", "ACCESS_WIFI_STATE", "CHANGE_WIFI_MULTICAST_STATE",
                     "POST_NOTIFICATIONS", "WAKE_LOCK"}, perms


def test_webui_asset_matches_the_desktop_page():
    spec = importlib.util.spec_from_file_location("gen_android_webui", ROOT / "tools" / "gen_android_webui.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    asset = ANDROID / "assets" / "webui.html"
    assert asset.read_text(encoding="utf-8") == mod.render(), "run  python tools/gen_android_webui.py"
