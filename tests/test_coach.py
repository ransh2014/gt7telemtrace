import numpy as np
import pandas as pd
from test_corners import APEXES, _lap

from gt7telem import coach


def _costly(df, lo, hi, k=0.8):
    """Slow the lap by `k` between apex+lo and apex+hi at every corner and redo the clock, so the habit costs time."""
    d = df.copy()
    pos = d["track_position"].to_numpy()
    for a in APEXES:
        m = (pos >= a + lo) & (pos < a + hi)
        d.loc[m, "speed_kmh"] = d.loc[m, "speed_kmh"] * k
    spd = d["speed_kmh"].to_numpy() / 3.6
    d["t"] = np.concatenate([[0.0], np.cumsum(np.diff(pos) / spd[1:])])
    return d


def test_no_tips_for_identical_laps():
    assert coach.analyse(_lap(), _lap()) == []
    assert "no corner" in coach.describe([]).lower()


def test_early_braking_gets_a_brake_later_tip():
    # ours brakes 30 m earlier than the reference at every corner
    tips = coach.analyse(_costly(_lap(brake_shift=-30), -140, -30), _lap())
    assert tips
    t = tips[0]
    assert "later" in t.text and f"T{t.corner}" in t.text
    assert len(tips) <= 3


def test_early_lift_gets_a_throttle_tip():
    tips = coach.analyse(_costly(_lap(throttle_shift=40), 20, 60), _lap())
    assert any(t.kind == "throttle" for t in tips)
    assert any("throttle" in t.cue.lower() for t in tips)


def test_slow_apex_gets_a_speed_tip():
    tips = coach.analyse(_costly(_lap(speed_drop=15), -10, 10), _lap())
    assert any(t.kind == "speed" for t in tips)


def test_tips_sorted_by_time_loss():
    tips = coach.analyse(_costly(_lap(throttle_shift=40, speed_drop=10), 0, 60), _lap(), max_tips=5)
    assert [t.time_s for t in tips] == sorted((t.time_s for t in tips), reverse=True)


def test_live_coach_speaks_once_per_corner_and_rearms_on_new_lap():
    tips = coach.analyse(_costly(_lap(throttle_shift=40), 20, 60), _lap())
    assert tips
    live = coach.LiveCoach(tips)
    said = []
    for pos in range(0, 3000, 10):
        c = live.update(float(pos), 150.0)
        if c:
            said.append((pos, c))
    assert len(said) == len(tips)
    by_cue = {t.cue: t for t in tips}
    for pos, cue in said:
        t = by_cue[cue]
        assert pos < (t.brake_pos if t.brake_pos is not None else t.apex)   # before the corner, not after
    again = [live.update(float(p), 150.0) for p in range(0, 3000, 10)]      # position wrapped: armed again
    assert sum(1 for c in again if c) == len(tips)


def test_unreadable_laps_give_no_tips():
    assert coach.analyse(pd.DataFrame(), _lap()) == []
    assert coach.analyse(None, _lap()) == []
