"""Tests for the optional LAN web dashboard (webui_server).

Uses the real server on an ephemeral port plus a real WebSocket client, so the
HTTP page, the /ws upgrade, the frame schema, the origin check and the
port-in-use path are all exercised end to end (no console needed).
"""
import json
import socket
import urllib.error
import urllib.request

import pytest

pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402

from gt7telem import config, webui_server  # noqa: E402


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _always_stop():
    yield
    webui_server.stop()


SNAP = {"speed_kmh": 123.4, "rpm": 5000, "max_rpm": 8000, "gear": 3, "suggested_gear": 4, "throttle": 0.5,
        "brake": 0.0, "steering": -0.2, "tyre_temp_fl": 70, "tyre_temp_fr": 71, "tyre_temp_rl": 90, "tyre_temp_rr": 101,
        "tyre_slip_fl": 1.0, "tyre_slip_fr": 1.0, "tyre_slip_rl": 1.0, "tyre_slip_rr": 1.0, "fuel_remaining": 50,
        "fuel_capacity": 100, "lap_number": 2, "total_laps": 5, "world_x": 10.0, "world_z": 20.0, "speed": 1}


def test_start_stop_and_idempotent():
    port = _free_port()
    assert webui_server.start(port) is True
    assert webui_server.is_running()
    assert webui_server.start(port) is True
    webui_server.stop()
    assert not webui_server.is_running()
    # the port is free again
    assert webui_server.start(port) is True


def test_serves_page_and_404():
    port = _free_port()
    assert webui_server.start(port)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        body = r.read().decode()
        assert r.status == 200
        assert "text/html" in r.headers["Content-Type"]
        assert "Content-Security-Policy" in r.headers
    assert "TRACE" in body
    # self-contained: no external scripts, styles or fonts
    assert "//fonts.googleapis" not in body and "src=\"http" not in body and "href=\"http" not in body
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    assert e.value.code == 404


def test_frame_schema_and_escaping():
    f = webui_server.build_frame(SNAP, {"connected": True, "cur_ms": 1234, "delta": 0.25, "vs_top": None,
                                        "car": "Mazda\x00<b>MX-5</b>", "track": "T" * 500,
                                        "strategy": {"laps_of_fuel": 6.25, "pit_by_lap": 4}})
    for key in ("type", "ok", "spd", "rpm", "maxrpm", "gear", "sug", "thr", "brk", "str", "latg", "long", "tt", "ts",
                "fuel", "cap", "lap", "laps", "cur", "last", "best", "delta", "top", "fl", "pit", "car", "track",
                "pos", "x", "z", "tcs", "asm", "lim", "hist"):
        assert key in f
    assert f["spd"] == 123.4 and len(f["tt"]) == 4 and len(f["ts"]) == 4
    assert "\x00" not in f["car"] and len(f["track"]) <= 80
    assert f["delta"] == 0.25 and f["top"] is None
    json.dumps(f)   # serialisable


def test_gt7_no_suggestion_sentinel_and_bad_values():
    f = webui_server.build_frame({"suggested_gear": 15, "speed_kmh": float("nan"), "rpm": "x"})
    assert f["sug"] == 0 and f["spd"] == 0 and f["rpm"] == 0


def test_websocket_receives_frames_and_is_read_only():
    port = _free_port()
    assert webui_server.start(port, hz=60)
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert hello["type"] == "hello"
        webui_server.update(SNAP, {"connected": True, "car": "Car", "track": "Track"})
        got = None
        for _ in range(20):
            m = json.loads(ws.recv(timeout=5))
            if m["type"] == "f":
                got = m
                break
        assert got and got["spd"] == 123.4 and got["car"] == "Car"
        ws.send("anything the client says is ignored")   # read-only: must not break the feed
        webui_server.update(dict(SNAP, speed_kmh=200), {"connected": True})
        for _ in range(20):
            m = json.loads(ws.recv(timeout=5))
            if m["type"] == "f" and m["spd"] == 200:
                break
        else:
            pytest.fail("no updated frame after client message")


def test_cross_site_origin_rejected():
    port = _free_port()
    assert webui_server.start(port)
    with pytest.raises(Exception):
        with connect(f"ws://127.0.0.1:{port}/ws", origin="http://evil.example", open_timeout=5):
            pass
    with connect(f"ws://127.0.0.1:{port}/ws", origin=f"http://127.0.0.1:{port}", open_timeout=5) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"


def test_port_in_use_returns_false_without_raising():
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        assert webui_server.start(port) is False
        assert not webui_server.is_running()
        assert webui_server.last_error[0]


def test_binding_localhost_vs_remote():
    lan = webui_server.lan_ip()
    port = _free_port()
    assert webui_server.start(port, addr="127.0.0.1")
    if lan:
        with pytest.raises(OSError):
            socket.create_connection((lan, port), timeout=1).close()
    webui_server.stop()
    assert webui_server.start(port, addr="0.0.0.0")
    with socket.create_connection((lan or "127.0.0.1", port), timeout=2):
        pass


def test_update_is_noop_when_stopped():
    webui_server.update(SNAP, {})   # must not raise
    assert not webui_server.is_running()


def test_hz_is_clamped():
    assert webui_server._clamp_hz(1000) == webui_server.MAX_HZ
    assert webui_server._clamp_hz(0) == webui_server.MIN_HZ
    assert webui_server._clamp_hz("junk") == 30


def test_settings_defaults_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    d = config.load()
    assert d["WEBUI_ENABLED"] is False and d["WEBUI_PORT"] == 8080
    assert d["WEBUI_BIND_ALL"] is False and d["WEBUI_HZ"] == 30
    config.save(WEBUI_ENABLED=True, WEBUI_BIND_ALL=True, WEBUI_PORT=8123)
    d = config.load()
    assert d["WEBUI_ENABLED"] is True and d["WEBUI_BIND_ALL"] is True and d["WEBUI_PORT"] == 8123
