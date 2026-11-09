"""The desktop windows, the Android app, the website and the release must all agree: same palette, same
fonts, same version. Each of these drifted before; these tests keep them in step.

The website lives in a separate repository (normally ../gt7telemwebsite); its checks are skipped when it is absent."""
import re
from pathlib import Path

import pytest

from gt7telem import theme

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT.parent / "gt7telemwebsite" / "assets" / "styles.css"
ANDROID_THEME = ROOT / "android" / "app" / "src" / "main" / "kotlin" / "app" / "trace" / "Theme.kt"
ANDROID_GRADLE = ROOT / "android" / "app" / "build.gradle.kts"


def _css_tokens():
    root_block = re.search(r":root\s*\{(.*?)\n\}", CSS.read_text(encoding="utf-8"), re.S).group(1)
    return {k: v.lower() for k, v in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", root_block)}


@pytest.mark.skipif(not CSS.is_file(), reason="website repo not checked out next to this one")
def test_theme_py_matches_the_websites_css_tokens():
    css = _css_tokens()
    expected = {
        "bg": theme.BG, "bg-1": theme.BG1, "bg-2": theme.BG2, "bg-3": theme.BG3, "cbg": theme.CBG,
        "orange": theme.RED, "orange-dim": theme.RED_DIM, "orange-2": theme.RED_2,
        "blue": theme.TEAL, "blue-2": theme.TEAL_2, "green": theme.GREEN, "gold": theme.GOLD,
        "txt": theme.TXT, "mid": theme.MID, "dim": theme.DIM,
    }
    wrong = {k: (css.get(k), v) for k, v in expected.items() if css.get(k) != v.lower()}
    assert not wrong, f"theme.py differs from assets/styles.css (website, desktop): {wrong}"


@pytest.mark.skipif(not CSS.is_file(), reason="website repo not checked out next to this one")
def test_website_fonts_are_the_bundled_ones():
    css = CSS.read_text(encoding="utf-8")
    for family in ("Anton", "Sora", "Manrope", "JetBrains Mono"):
        assert f"'{family}'" in css, f"{family} missing from the website CSS"
        assert theme._FONT_FILES.get(family), f"{family} has no bundled font files in theme.py"
        for name in theme._FONT_FILES[family]:
            assert (theme.FONT_DIR / name).is_file(), f"bundled font file missing: {name}"


def test_android_dashboard_palette_matches_theme_py():
    kt = ANDROID_THEME.read_text(encoding="utf-8")
    block = kt[kt.index("object D"):kt.index("/** Lap / Race")]
    vals = {k: "#" + v.lower() for k, v in re.findall(r"val (\w+) = c\(0x([0-9a-fA-F]{6})\)", block)}
    expected = {
        "bg": theme.BG, "pnl": theme.BG1, "hdr": theme.BG2, "tile": theme.BG3, "trough": theme.BG3, "map": theme.CBG,
        "frame": theme.LINE2, "acc": theme.RED_2, "red": theme.RED_2, "hi": theme.TEAL_2, "log": theme.TEAL_2,
        "cold": theme.TEAL, "good": theme.GREEN, "amb": theme.GOLD, "fg": theme.TXT, "dim": theme.DIM,
    }
    wrong = {k: (vals.get(k), v) for k, v in expected.items() if vals.get(k) != v.lower()}
    assert not wrong, f"Android Theme.kt dashboard palette differs from theme.py: {wrong}"


def test_android_dark_analyst_palette_matches_theme_py():
    kt = ANDROID_THEME.read_text(encoding="utf-8")
    dark = re.search(r"val Dark = AP\((.*?)\)\n", kt, re.S).group(1)
    got = ["#" + h.lower() for h in re.findall(r"c\(0x([0-9a-fA-F]{6})\)", dark)]
    # AP(bg, pnl, pnl2, dim2, acc, cyn, grn, yel, org, prp, fg, dim, blue)
    want = [theme.BG, theme.BG1, theme.BG2, theme.BG3, theme.RED_2, theme.TEAL_2, theme.GREEN, theme.GOLD, theme.RED,
            theme.MID, theme.TXT, theme.DIM, theme.BLUE_LINK]
    assert got == [w.lower() for w in want]


def test_android_version_matches_the_package():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    gradle = ANDROID_GRADLE.read_text(encoding="utf-8")
    assert re.search(r'versionName = "([^"]+)"', gradle).group(1) == version, "Android versionName != package version"
    major, minor, patch = (int(x) for x in version.split("."))
    code = int(re.search(r"versionCode = (\d+)", gradle).group(1))
    assert code == major * 1000 + minor * 100 + patch, f"versionCode {code} is not derived from {version}"
