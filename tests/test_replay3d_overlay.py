import json
import re

import numpy as np

from gt7telem import replay3d
from gt7telem.overlay_page import OVERLAY


def _samples(n=300):
    th = np.linspace(0, 2 * np.pi, n)
    return [{"world_x": 100 * np.cos(a), "world_y": 0.0, "world_z": 60 * np.sin(a), "speed_kmh": 100 + 50 * np.sin(a),
             "throttle": 0.5, "brake": 0.0, "t": i * 0.1} for i, a in enumerate(th)]


def test_usable():
    assert replay3d.usable(_samples())
    assert not replay3d.usable(_samples(5))
    assert not replay3d.usable([{"speed_kmh": 1}] * 100)
    assert not replay3d.usable(None)


def test_html_is_self_contained_and_escaped():
    s = _samples()
    s[0]["world_x"] = float("nan")
    html = replay3d.build_html(s, title="<script>alert(1)</script>", ghost_samples=_samples(100))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert not re.search(r"https?://", html)               # no CDN, works offline
    blob = re.search(r"const D=(\{.*?\});\nconst L", html, re.S).group(1)
    data = json.loads(blob.replace("<\\/", "</"))
    assert len(data["lap"]) == 300 and len(data["ghost"]) == 100
    assert data["lap"][0][6] == 0.0 and data["lap"][0][0] == 0.0     # NaN became 0, time starts at 0


def test_thins_long_laps():
    assert len(replay3d._compact(_samples(5000))) == replay3d.MAX_POINTS


def test_write_and_gif(tmp_path):
    p = replay3d.write_html(tmp_path / "r.html", _samples())
    assert p.read_text(encoding="utf-8").startswith("<!doctype html>")
    gif = replay3d.export_gif(tmp_path / "r.gif", _samples(), size=120, frames=6)
    assert gif is not None and gif.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    assert replay3d.export_gif(tmp_path / "x.gif", _samples(3)) is None


def test_overlay_is_transparent_selfcontained_and_safe():
    assert "background:transparent" in OVERLAY
    assert "/ws" in OVERLAY and "innerHTML" not in OVERLAY
    assert not re.search(r"https?://", OVERLAY)
