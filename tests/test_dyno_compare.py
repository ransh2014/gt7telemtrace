import numpy as np

from gt7telem import dyno_compare as dc


def _res(peak=10.0, lim=7000.0, top_kmh=250.0, lo=2500.0):
    return {
        "ok": True, "peak_wkg": peak, "peak_rpm": 5500.0, "rev_lim": lim, "cover": (lo, lim),
        "P_at": lambda rpm: peak * np.clip(np.asarray(rpm, dtype=float) / 5500.0, 0, 1),
        "gears": [{"g": 1, "top_kmh": 80.0}, {"g": 2, "top_kmh": 140.0}, {"g": 3, "top_kmh": top_kmh}],
    }


def test_overlay_names_the_stronger_car():
    r = dc.overlay(_res(12.0), _res(9.0), "RS", "Civic")
    assert r["ok"] and len(r["rpm"]) == len(r["a"]) == len(r["b"]) == 60
    assert "RS makes" in r["text"] and r["peak"]["RS"]["wkg"] == 12.0


def test_overlay_needs_both_and_overlap():
    assert not dc.overlay({"ok": False}, _res())["ok"]
    assert not dc.overlay(_res(lim=3000.0, lo=2500.0), _res(lo=6500.0))["ok"]


def test_final_drive_too_short_too_long_and_ok():
    res = _res()
    on_limiter = np.concatenate([np.full(80, 249.0), np.full(20, 120.0)])
    assert dc.final_drive_advice(res, on_limiter)["action"] == "longer"
    short_trace = np.linspace(50, 200, 200)
    r = dc.final_drive_advice(res, short_trace)
    assert r["action"] == "shorter" and "Shorten" in r["text"]
    ok = np.linspace(50, 242, 400)
    assert dc.final_drive_advice(res, ok)["action"] == "keep"


def test_final_drive_bad_input():
    assert not dc.final_drive_advice({"ok": False}, [1, 2, 3])["ok"]
    assert not dc.final_drive_advice(_res(), [1, 2, 3])["ok"]
