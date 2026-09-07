"""Tests for the optional Prometheus exporter.

Worth covering because start() unpacks start_http_server()'s (server, thread)
return, which only exists in prometheus_client >= 0.20. On 0.19.x it returns
None and that unpack raised a TypeError the surrounding `except OSError`
never caught -- which took the whole Dashboard down when metrics were
enabled. These tests fail loudly if the floor pin ever slips back.
"""
import socket
import urllib.request

import pytest

from gt7telem import metrics_server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _always_stop():
    yield
    metrics_server.stop()


def test_start_returns_true_and_reports_running():
    assert metrics_server.start(_free_port()) is True
    assert metrics_server.is_running() is True


def test_start_is_idempotent():
    port = _free_port()
    assert metrics_server.start(port) is True
    assert metrics_server.start(port) is True


def test_stop_frees_the_port_and_clears_state():
    port = _free_port()
    metrics_server.start(port)
    metrics_server.stop()
    assert metrics_server.is_running() is False
    # binding again proves the socket was really closed, not just flagged
    assert metrics_server.start(port) is True


def test_start_returns_false_when_the_port_is_taken():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert metrics_server.start(port) is False
        assert metrics_server.is_running() is False


def test_update_is_a_noop_before_start():
    """Call sites rely on this so they don't have to check is_running()."""
    metrics_server.update({"speed_kmh": 100})


def test_gauges_are_served_over_http():
    port = _free_port()
    assert metrics_server.start(port, addr="127.0.0.1")
    metrics_server.update({
        "speed_kmh": 188.4, "rpm": 6240, "throttle": 0.83,
        "brake": 0.0, "fuel_remaining": 42.0, "current_lap_ms": 91234,
    })
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    assert "gt7_speed_kmh 188.4" in body
    assert "gt7_rpm 6240.0" in body
    assert "gt7_throttle 0.83" in body


def test_update_tolerates_junk_values():
    metrics_server.start(_free_port())
    metrics_server.update({"speed_kmh": None, "rpm": "not a number", "brake": 0.5})
    metrics_server.update({})


def test_stop_resets_gauges_to_zero():
    port = _free_port()
    metrics_server.start(port)
    metrics_server.update({"speed_kmh": 200.0})
    metrics_server.stop()
    metrics_server.start(port)
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    assert "gt7_speed_kmh 0.0" in body
