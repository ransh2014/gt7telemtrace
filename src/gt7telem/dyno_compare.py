"""Car-vs-car dyno overlay and final-drive advice.

Pure numpy; builds on dyno.analyse(). ``overlay`` puts two analysed cars (or two tunes of one car) on common rpm
axes; ``final_drive_advice`` looks at a lap's speed trace and says whether the top gear is geared too short (hits the
limiter on the straight) or too long (never reaches the limiter, so acceleration is wasted).
"""

from __future__ import annotations

import numpy as np

from . import dyno


def _curves(res: dict):
    c = res.get("curves") or {}
    return c


def overlay(a: dict, b: dict, name_a: str = "A", name_b: str = "B", n: int = 60) -> dict:
    """Both cars' power curves resampled onto shared rpm points. `a`/`b` are dyno.analyse() results.

    Returns {"ok","msg","rpm":[...],"a":[W/kg...],"b":[...],"peak":{...},"text"}."""
    out = {"ok": False, "msg": "", "rpm": [], "a": [], "b": [], "peak": {}, "text": ""}
    if not (a.get("ok") and b.get("ok")):
        out["msg"] = "Both cars need a dyno result (a full lap with engine data)."
        out["text"] = out["msg"]
        return out
    fa, fb = a.get("P_at"), b.get("P_at")
    lo = max(a["cover"][0], b["cover"][0])
    hi = min(a["rev_lim"], b["rev_lim"])
    if not callable(fa) or not callable(fb) or hi <= lo:
        out["msg"] = "The two cars' rev ranges don't overlap enough to compare."
        out["text"] = out["msg"]
        return out
    rpm = np.linspace(lo, hi, n)
    pa, pb = np.nan_to_num(fa(rpm)), np.nan_to_num(fb(rpm))
    out.update(ok=True, rpm=rpm.tolist(), a=pa.tolist(), b=pb.tolist(),
               peak={name_a: {"wkg": float(a["peak_wkg"]), "rpm": float(a["peak_rpm"])},
                     name_b: {"wkg": float(b["peak_wkg"]), "rpm": float(b["peak_rpm"])}})
    stronger = name_a if a["peak_wkg"] >= b["peak_wkg"] else name_b
    diff = abs(a["peak_wkg"] - b["peak_wkg"]) / max(min(a["peak_wkg"], b["peak_wkg"]), 1e-6) * 100
    diff_curve = pa - pb
    area = float(np.sum((diff_curve[1:] + diff_curve[:-1]) / 2 * np.diff(rpm)))   # trapezoid; np.trapz is gone in NumPy 2
    out["text"] = (f"{stronger} makes {diff:.0f}% more peak power per kg. "
                   f"Across the shared rev range {name_a if area >= 0 else name_b} pulls harder on average.")
    return out


def final_drive_advice(res: dict, speed_kmh, top_gear_frac: float = 0.02) -> dict:
    """Suggest a final-drive change from where the lap's top speed lands against the limiter.

    `res` is dyno.analyse(); `speed_kmh` the lap's speed samples. If the car tops out well below the limiter in its
    top gear the gearing is too long (shorter final drive helps acceleration); if it sits on the limiter for a share of
    the lap the gearing is too short."""
    out = {"ok": False, "action": None, "text": ""}
    if not res.get("ok") or not res.get("gears"):
        out["text"] = "Needs a dyno result."
        return out
    top = max(res["gears"], key=lambda g: g["g"])
    v = np.asarray(speed_kmh, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 20:
        out["text"] = "Needs the lap's speed trace."
        return out
    vmax = float(np.percentile(v, 99.5))
    gear_top = top["top_kmh"]
    share = float(np.mean(v >= 0.985 * gear_top))
    out["ok"] = True
    out["top_gear_kmh"], out["lap_max_kmh"], out["limiter_share"] = gear_top, vmax, share
    if share >= top_gear_frac:
        pct = max(1.0, round((share / 0.05) * 1.0, 1))
        out["action"] = "longer"
        out["text"] = (f"You hit the limiter in top gear for {share * 100:.1f}% of the lap (top {gear_top:.0f} km/h). "
                       f"Lengthen the final drive about {min(pct, 5.0):.0f}% (lower the final ratio number).")
    elif vmax < 0.93 * gear_top:
        gap = (gear_top - vmax) / gear_top * 100
        out["action"] = "shorter"
        out["text"] = (f"Top gear has {gap:.0f}% to spare (lap top speed {vmax:.0f} km/h of {gear_top:.0f}). "
                       f"Shorten the final drive about {min(round(gap * 0.7), 8):.0f}% for stronger acceleration.")
    else:
        out["action"] = "keep"
        out["text"] = "Top-gear length is about right for this lap."
    return out


def analyse_pair(df_a, df_b, **kw):
    """Convenience: analyse two lap frames and overlay them."""
    return overlay(dyno.analyse(df_a, **kw), dyno.analyse(df_b, **kw))
