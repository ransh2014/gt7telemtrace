import numpy as np
import pandas as pd
from test_corners import _lap

from gt7telem import driver_dna as dna


def _with_steer(df, smooth=True):
    df = df.copy()
    base = np.where(df["lat_g"] > 0, 0.5, 0.0)
    df["steering"] = base if smooth else base + np.random.default_rng(1).normal(0, 0.4, len(df))
    return df


def test_profile_has_all_traits_in_range():
    p = dna.profile(_with_steer(_lap()))
    assert p is not None and set(p) == set(dna.TRAITS)
    for k in dna.TRAITS[:4]:
        assert 0 <= p[k] <= 100
    assert p["precision"] is None          # one lap: no spread yet


def test_jerky_steering_scores_lower_smoothness():
    smooth = dna.profile(_with_steer(_lap()))["smoothness"]
    jerky = dna.profile(_with_steer(_lap(), smooth=False))["smoothness"]
    assert jerky < smooth


def test_precision_needs_three_laps_and_rewards_repeatability():
    a = _with_steer(_lap())
    same = dna.profile(a, [_with_steer(_lap()), _with_steer(_lap())])
    wild = dna.profile(a, [_with_steer(_lap(brake_shift=-40)), _with_steer(_lap(brake_shift=40))])
    assert same["precision"] > wild["precision"]


def test_unreadable_lap():
    assert dna.profile(pd.DataFrame()) is None


def test_compare_and_archetype():
    a = {"brake_aggression": 80, "trail_braking": 70, "smoothness": 40, "throttle_modulation": 50, "precision": None}
    b = {"brake_aggression": 40, "trail_braking": 70, "smoothness": 40, "throttle_modulation": 50, "precision": 60}
    lines = dna.compare(a, b)
    assert len(lines) == 1 and "Brake aggression" in lines[0]
    assert dna.compare(a, a) == ["Your styles are very close."]
    assert "trail" in dna.archetype(a).lower()


def test_code_roundtrip_and_rejects_bad():
    t = {"brake_aggression": 80.4, "trail_braking": 10, "smoothness": 99, "throttle_modulation": 0, "precision": None}
    name, back = dna.import_code(dna.export_code(t, "Ra<n>sh"))
    assert name == "Ransh" and back["brake_aggression"] == 80 and back["precision"] is None
    assert dna.import_code("DNA1:x:1,2,3") is None
    assert dna.import_code("DNA1:x:1,2,3,4,500") is None
    assert dna.import_code("nonsense") is None


def test_history_roundtrip_and_trend(tmp_path):
    p = tmp_path / "h.json"
    for i, v in enumerate((40, 50, 60, 70)):
        assert dna.record({"smoothness": v}, f"2026-11-0{i + 1}", p)
    dna.record({"smoothness": 71}, "2026-11-04", p)          # same day replaces
    h = dna.load_history(p)
    assert len(h) == 4 and h[-1]["traits"]["smoothness"] == 71
    assert dna.trend(h, "smoothness") > 0
    assert dna.trend(h[:2], "smoothness") is None
    assert dna.load_history(tmp_path / "missing.json") == []
