"""dyno.py -- power curve, gearbox and tuning hints worked out from one lap.

GT7 never shows the engine's real output curve or the gear spacing, but the
telemetry holds everything needed to rebuild them: how hard the car pulls at
full throttle at every rpm, in every gear. Specific wheel power is

    P/m = v * (a + g*sin(slope) + c0 + c1*v^2)

(acceleration, hill, rolling resistance c0, aero drag c1), taken only from
full-throttle, no-wheelspin, mostly-straight samples and binned by rpm. The
drag terms are fitted from the lap's own coast-down; the fit can read a little
high because engine braking is in it, so treat absolute numbers as an estimate
(roughly +/-10%). The curve SHAPE, the peak rpm and the shift points are much
more reliable than the absolute hp.

Pure numpy/pandas -- no Tk -- so it is testable on synthetic data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

G = 9.81
HP_W = 745.7
_REQ = ("t", "speed_kmh", "rpm", "gear", "throttle", "brake")
_C0_DEFAULT, _C1_DEFAULT = 0.12, 2.8e-4      # used when the lap has no usable coast-down


# ── small helpers ─────────────────────────────────────────────────────────────
def _col(df, name, default=0.0):
    if name in df.columns:
        a = np.asarray(df[name], dtype=float)
        return np.where(np.isfinite(a), a, default)
    return np.full(len(df), default, dtype=float)


def _smooth(x, n):
    n = int(n)
    if n < 3 or len(x) < n:
        return np.asarray(x, dtype=float).copy()
    pad = n // 2
    xp = np.pad(np.asarray(x, dtype=float), (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, np.ones(n) / n, mode="valid")


def _near_shift(gear, pad):
    chg = np.zeros(len(gear), dtype=float)
    chg[1:] = gear[1:] != gear[:-1]
    return np.convolve(chg, np.ones(2 * pad + 1), mode="same") > 0


# ── remembered car weights (asked once per car) ───────────────────────────────
def _weights_path():
    return Path.home() / ".gt7telem" / "car_weights.json"


def load_weight(car):
    """Saved weight in kg for this car name, or None."""
    try:
        with open(_weights_path(), encoding="utf-8") as f:
            kg = float(json.load(f).get(str(car).strip().lower()))
        return kg if 300 <= kg <= 5000 else None
    except Exception:
        return None


def save_weight(car, kg):
    try:
        kg = float(kg)
        if not 300 <= kg <= 5000:
            return False
        p = _weights_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        d[str(car).strip().lower()] = kg
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        tmp.replace(p)
        return True
    except Exception:
        return False


# ── the dyno ──────────────────────────────────────────────────────────────────
def analyse(df, gear_ratios=None, mass_kg=None, tyre_radius_m=None):
    """Rebuild the car's power curve and gearbox from a lap. Always returns a
    dict; res["ok"] is False (with res["msg"] saying why) when the lap can't
    support it."""
    res = {"ok": False, "msg": ""}
    if df is None or len(df) < 120 or any(c not in df.columns for c in _REQ):
        res["msg"] = "Needs a full lap with engine data (not a leaderboard ghost)."
        return res
    n = len(df)
    t = np.maximum.accumulate(_col(df, "t")) + np.arange(n) * 1e-6
    d = np.diff(t)
    d = d[d > 1e-4]
    if len(d) < 20:
        res["msg"] = "This lap has no usable timestamps."
        return res
    dt = float(np.median(d))
    w = max(3, int(round(0.2 / dt)))
    if w % 2 == 0:
        w += 1

    v = _smooth(_col(df, "speed_kmh") / 3.6, w)
    a = np.gradient(v, t)
    rpm = _smooth(_col(df, "rpm"), w)
    gear = np.rint(_col(df, "gear")).astype(int)
    thr, brk = _col(df, "throttle"), _col(df, "brake")
    ay = np.abs(_col(df, "ang_y")) * v
    vx, vy, vz = _col(df, "vel_x"), _col(df, "vel_y"), _col(df, "vel_z")
    sp = np.sqrt(vx * vx + vy * vy + vz * vz)
    sin_t = np.where(sp > 3.0, vy / np.maximum(sp, 1e-6), 0.0)
    sin_t = np.clip(_smooth(sin_t, w * 3 | 1), -0.3, 0.3)

    S = np.vstack([_col(df, f"tyre_slip_{x}") for x in ("fl", "fr", "rl", "rr")])
    moving = v > 5
    have_slip = bool(moving.any() and float(np.mean(S[:, moving] > 0.2)) > 0.5)
    slip_max = S.max(axis=0) if have_slip else np.ones(n)
    slip_min = S.min(axis=0) if have_slip else np.ones(n)

    pad = max(3, int(round(0.25 / dt)))
    near = _near_shift(gear, pad)

    # rolling resistance + aero drag from the coast-down
    coast = ((thr < 0.03) & (brk < 0.03) & (v > 12) & (ay < 1.5) & (gear >= 1) & ~near)
    c0, c1, src = _C0_DEFAULT, _C1_DEFAULT, "assumed"
    if coast.sum() >= 60 and (np.percentile(v[coast], 95) - np.percentile(v[coast], 5)) >= 10:
        y = -(a[coast] + G * sin_t[coast])
        X = np.column_stack([np.ones(int(coast.sum())), v[coast] ** 2])
        try:
            (f0, f1), *_ = np.linalg.lstsq(X, y, rcond=None)
            if 0.02 <= f0 <= 0.4 and 5e-5 <= f1 <= 8e-4:
                c0, c1, src = float(f0), float(f1), "fitted"
        except Exception:
            pass
    res["resist"] = {"c0": c0, "c1": c1, "src": src}

    # full-throttle, grip-limited-by-engine samples
    m = ((thr > 0.97) & (brk < 0.02) & (gear >= 1) & ~near & (v > 3.0) & (ay < 3.0)
         & (slip_max < 1.08) & (rpm > 1000))
    f_spec = a + G * sin_t + c0 + c1 * v * v
    p_spec = f_spec * v
    m &= (f_spec > 0) & (p_spec > 0) & (p_spec < 2500)
    res["n_samples"] = int(m.sum())
    if m.sum() < 80:
        res["msg"] = ("Not enough clean full-throttle driving in this lap "
                      f"({int(m.sum())} samples). Use a lap with a few long straights.")
        return res

    obs_max = float(np.percentile(rpm[rpm > 0], 99.8))
    cands = [obs_max]
    for cn in ("rpm_limiter", "max_rpm"):
        c = _col(df, cn)
        c = c[c > 1000]
        if len(c):
            cands.append(float(np.median(c)))
    rev_lim = min(max(cands), obs_max * 1.25)
    res["rev_lim"] = rev_lim

    bw = max(50.0, round(rev_lim / 70.0 / 50.0) * 50.0)
    lo = math.floor(float(np.percentile(rpm[m], 1)) / bw) * bw
    hi = float(np.percentile(rpm[m], 99.5)) + bw
    # Overlapping rpm windows (+/-1.5 bins), so the thinly covered launch region still gets a
    # value instead of a gap that a straight line would bridge.
    pm, ps_, boost = p_spec[m], rpm[m], _col(df, "boost")[m]
    centers, q1, q3, bo, bst = [], [], [], [], []
    for c in np.arange(lo, hi + bw, bw):
        sel = np.abs(ps_ - c) <= 1.5 * bw
        if sel.sum() < 6:
            continue
        centers.append(float(c))
        q1.append(float(np.percentile(pm[sel], 25)))
        q3.append(float(np.percentile(pm[sel], 75)))
        bo.append(float(np.median(pm[sel])))
        bst.append(float(np.median(boost[sel])))
    if len(centers) < 8:
        res["msg"] = "The lap doesn't sweep enough of the rev range to draw a curve."
        return res
    centers = np.array(centers)
    med = _smooth(np.array(bo), 3)
    q1, q3 = _smooth(np.array(q1), 3), _smooth(np.array(q3), 3)
    bst = np.array(bst)

    def P_at(r):                       # nan outside the rpm range the lap covered
        return np.interp(r, centers, med, left=np.nan, right=np.nan)

    def P_edge(r):                     # holds the end values (for extrapolating top speed)
        return np.interp(r, centers, med)

    ipk = int(np.argmax(med))
    torque = med / (2 * math.pi * centers / 60.0)             # Nm/kg
    itq = int(np.argmax(torque))
    res.update(rpm=centers, p_wkg=med, q1=q1, q3=q3, tq_nmkg=torque, boost=bst,
               peak_rpm=float(centers[ipk]), peak_wkg=float(med[ipk]),
               peak_tq_rpm=float(centers[itq]), peak_tq_nmkg=float(torque[itq]),
               cover=(float(centers[0]), float(centers[-1])), P_at=P_at, P_edge=P_edge,
               hp_per_tonne=float(med[ipk] * 1000.0 / HP_W))
    mass = float(mass_kg) if mass_kg and 300 <= float(mass_kg) <= 5000 else None
    res["mass"] = mass
    if mass:
        res.update(unit_p="hp", unit_t="Nm", p_disp=med * mass / HP_W, q1_disp=q1 * mass / HP_W,
                   q3_disp=q3 * mass / HP_W, t_disp=torque * mass,
                   peak_p_disp=float(med[ipk] * mass / HP_W), peak_t_disp=float(torque[itq] * mass))
    else:
        res.update(unit_p="hp/t", unit_t="Nm/t", p_disp=med * 1000.0 / HP_W, q1_disp=q1 * 1000.0 / HP_W,
                   q3_disp=q3 * 1000.0 / HP_W, t_disp=torque * 1000.0,
                   peak_p_disp=float(med[ipk] * 1000.0 / HP_W), peak_t_disp=float(torque[itq] * 1000.0))

    # gearbox: measured rpm per m/s in each gear
    ratios = [float(x) for x in (gear_ratios or []) if isinstance(x, (int, float)) and math.isfinite(x)]
    ks, kn = {}, {}
    ok_k = (~near) & (v > 3) & (slip_max < 1.05) & (slip_min > 0.95) & (rpm > 1000)
    for g in range(1, 9):
        s = ok_k & (gear == g)
        if s.sum() >= 15:
            ks[g] = float(np.median(rpm[s] / v[s]))
            kn[g] = int(s.sum())
    src_k = {g: "measured" for g in ks}
    if ks and ratios:
        gref = max(kn, key=kn.get)
        if gref <= len(ratios) and ratios[gref - 1] > 0:
            for g in range(1, min(len(ratios), 8) + 1):
                if g not in ks and ratios[g - 1] > 0:
                    ks[g] = ks[gref] * ratios[g - 1] / ratios[gref - 1]
                    src_k[g] = "from ratio"
    if not ks:
        res["msg"] = "Couldn't pin down any gear (too little steady driving in each)."
        return res

    n_gears = max(len(ratios) and sum(1 for r in ratios if r > 0), max(ks))
    res["n_gears"] = int(n_gears)
    share = {g: float(np.mean(gear == g)) for g in range(1, 9)}
    gs = sorted(ks)
    # optimal shift points: where the next gear starts pulling harder
    opt, land = {}, {}
    for g in gs:
        if g + 1 not in ks:
            continue
        rho = ks[g + 1] / ks[g]
        if not 0.4 < rho < 0.98:
            continue
        grid = np.arange(res["peak_rpm"], rev_lim, max(25.0, bw / 4.0))
        if len(grid) < 2:
            continue
        pa, pb = P_at(grid), P_at(grid * rho)
        valid = np.isfinite(pa) & np.isfinite(pb)
        if not valid.any():
            continue
        better = valid & (pb >= pa)
        r_opt = float(grid[int(np.argmax(better))]) if better.any() else float(rev_lim)
        opt[g], land[g] = r_opt, r_opt * rho
    # the shifts you actually made
    mine = {}
    for i in range(1, n):
        if gear[i] == gear[i - 1] + 1 and gear[i - 1] >= 1 and thr[max(0, i - 4)] > 0.9:
            mine.setdefault(int(gear[i - 1]), []).append(float(_col(df, "rpm")[i - 1]))
    gears = []
    for g in gs:
        gears.append({
            "g": g, "ratio": ratios[g - 1] if g <= len(ratios) and ratios[g - 1] > 0 else None,
            "k": ks[g], "src": src_k[g], "kmh_per_1000rpm": 3.6 * 1000.0 / ks[g],
            "top_kmh": rev_lim / ks[g] * 3.6, "share": share.get(g, 0.0),
            "opt_shift": opt.get(g), "land_rpm": land.get(g),
            "your_shift": float(np.median(mine[g])) if mine.get(g) else None,
        })
        if tyre_radius_m and gears[-1]["ratio"]:
            # overall = ratio * final drive; k = rpm per m/s
            gears[-1]["final_drive"] = ks[g] * 2 * math.pi * tyre_radius_m / 60.0 / gears[-1]["ratio"]
    res["gears"] = gears

    # thrust (in g) against speed for each gear, and the envelope
    curves = {}
    for g in gs:
        vv = np.linspace(max(2.0, centers[0] / ks[g]), rev_lim / ks[g], 80)
        curves[g] = (vv * 3.6, P_edge(ks[g] * vv) / vv / G)
    res["curves"] = curves

    # true top speed: rev limiter in the tallest gear, or where drag wins first
    gt = gs[-1]
    vv = np.linspace(2.0, rev_lim / ks[gt], 500)
    fnet = P_edge(ks[gt] * vv) / vv - (c0 + c1 * vv * vv)
    pos = np.where(fnet > 0)[0]
    if len(pos) and pos[-1] < len(vv) - 2:
        res["top"] = {"kmh": float(vv[pos[-1]] * 3.6), "by": "drag", "gear": gt}
    else:
        res["top"] = {"kmh": float(vv[-1] * 3.6), "by": "rev limiter", "gear": gt}
    res["ok"] = True
    return res


# ── tuning hints ──────────────────────────────────────────────────────────────
def _balance(df, v):
    """Understeer ratio per corner phase: the steering angle you used divided by
    the angle a neutral (Ackermann) car needs for the yaw rate you got. ~1 is
    neutral, well above 1 pushes wide, below 1 rotates (or you're catching it).
    Returns {phase: (median_ratio, n)} or None. Tyre slip angles make a healthy
    car read slightly above 1, so only clear departures are reported."""
    wb = _col(df, "wheel_base")
    wb = wb[wb > 0.5]
    if not len(wb) or "wheel_steering_angle_l" not in df.columns:
        return None
    L = float(np.median(wb))
    d = (_col(df, "wheel_steering_angle_l") + _col(df, "wheel_steering_angle_r")) / 2.0
    if float(np.percentile(np.abs(d), 99)) > 1.6:        # degrees, not radians
        d = np.radians(d)
    yaw = _col(df, "ang_y")
    ok = (v > 12) & (np.abs(yaw) * v > 2.0) & (np.abs(d) > 0.01)
    if ok.sum() < 60:
        return None
    dn = L * yaw / np.maximum(v, 1.0)
    if np.corrcoef(d[ok], dn[ok])[0, 1] < 0:
        d = -d
    ratio = np.clip(d / np.where(np.abs(dn) < 1e-6, 1e-6, dn), -3.0, 5.0)
    thr, brk = _col(df, "throttle"), _col(df, "brake")
    phases = {
        "entry": ok & (brk > 0.1),
        "mid": ok & (thr <= 0.5) & (brk <= 0.1),
        "exit": ok & (thr > 0.5) & (brk < 0.05),
        "fast": ok & (v > 50),
    }
    out = {}
    for k, msk in phases.items():
        if msk.sum() >= 25:
            out[k] = (float(np.median(ratio[msk])), int(msk.sum()))
    return out or None


def advise(df, res, gear_ratios=None):
    """Plain-English tuning hints from the lap, each tied to a measured signal.
    The fixes follow GT7 community tuning guides (GTPlanet, Coach Dave Academy,
    racinggames.gg): they give a DIRECTION to try, never a number -- the right
    amount depends on the car. Change one thing at a time and re-drive."""
    hints = []

    def add(sev, area, finding, fix):
        hints.append({"sev": sev, "area": area, "finding": finding, "fix": fix})

    if df is None or len(df) < 120:
        return hints
    v = _col(df, "speed_kmh") / 3.6
    thr, brk = _col(df, "throttle"), _col(df, "brake")
    rpm = _col(df, "rpm")
    gear = np.rint(_col(df, "gear")).astype(int)
    t = _col(df, "t")
    dt = float(np.median(np.diff(t)[np.diff(t) > 1e-4])) if (np.diff(t) > 1e-4).any() else 1 / 60

    # ── gearing (needs the dyno) ──
    if res and res.get("ok"):
        rl, pk, pk_rpm = res["rev_lim"], res["peak_wkg"], res["peak_rpm"]
        gears = res["gears"]
        top_g = res.get("n_gears") or gears[-1]["g"]
        cov_hi = res["cover"][1]
        if cov_hi < 0.93 * rl:
            add(1, "Dyno", f"The curve stops at {cov_hi:.0f} rpm: you never held full throttle higher "
                f"(limiter ~{rl:.0f}).",
                "Hold a gear to the limiter on one straight, or shift later, so the top of the power "
                "curve and the best shift points can be measured.")
        lim = (rpm >= 0.985 * rl) & (thr > 0.9) & (gear >= 1)
        lim_s = float(lim.sum()) * dt
        if lim_s >= 1.5:
            add(2, "Gearing", f"You sat on the rev limiter for {lim_s:.1f} s this lap.",
                "Lengthen the final gear (raise top speed). You gain top speed and lose some pull. "
                "A taller gear that still reaches the limiter is wasted.")
        i_top = int(np.argmax(v))
        if gear[i_top] >= 1 and thr[i_top] > 0.9 and rpm[i_top] < 0.9 * pk_rpm:
            add(2, "Gearing", f"At your top speed the engine was at {rpm[i_top]:.0f} rpm, well under its "
                f"{pk_rpm:.0f} rpm power peak.",
                "Shorten the final gear: the car is drag-limited there, so you lose almost no top speed "
                "and gain acceleration everywhere.")
        acc = ((thr > 0.97) & (brk < 0.02) & (gear >= 1) & (rpm > 1000))
        if acc.sum() > 100:
            low = float(np.mean(np.nan_to_num(res["P_at"](rpm[acc]), nan=pk) < 0.85 * pk))
            if low > 0.25:
                add(2, "Gearing", f"{low * 100:.0f}% of your full-throttle time was below 85% of peak power.",
                    "The gears are too long (or too widely spaced) for this track. Shorten the final gear "
                    "or close up the ratios you use on corner exit.")
        for gd in gears[:-1]:
            if gd["opt_shift"] and gd["land_rpm"]:
                p_land = res["P_at"](np.array([gd["land_rpm"]]))[0]
                if np.isfinite(p_land) and p_land < 0.82 * pk:
                    add(2, "Gearing", f"Upshift {gd['g']}>{gd['g'] + 1} drops you to {gd['land_rpm']:.0f} rpm, "
                        f"only {p_land / pk * 100:.0f}% of peak power.",
                        f"Close the gap: shorten gear {gd['g'] + 1} (or lengthen {gd['g']}) so the drop is smaller. "
                        "Close ratios suit a peaky engine.")
        diffs = [(gd["your_shift"] - gd["opt_shift"], gd) for gd in gears
                 if gd["your_shift"] and gd["opt_shift"]]
        if len(diffs) >= 2:
            avg = float(np.mean([d for d, _ in diffs]))
            if avg < -0.05 * rl:
                add(1, "Shifting", f"You upshift about {-avg:.0f} rpm earlier than the curve says is best.",
                    "Hold each gear closer to its shift point (see the table): the next gear only pulls "
                    "harder past that rpm.")
            elif avg > 0.04 * rl:
                add(1, "Shifting", f"You upshift about {avg:.0f} rpm later than the best crossover.",
                    "Shift a little earlier; past the crossover the next gear already accelerates harder.")
        for gd in gears:
            if 2 <= gd["g"] < top_g and gd["share"] < 0.02:
                add(1, "Gearing", f"Gear {gd['g']} is barely used here ({gd['share'] * 100:.1f}% of the lap).",
                    "Only the gears you actually use matter on a track: spread the ratios you do use "
                    "so they sit in the power band.")
        top_share = float(np.mean(gear == top_g))
        if top_share < 0.02 and top_g >= 4:
            add(2, "Gearing", f"Top gear ({top_g}) is almost never used on this track.",
                "Shorten the final gear for tracks with few straights: you lose a top speed you never reach "
                "and gain acceleration out of every corner.")

    # ── balance ──
    bal = _balance(df, v)
    if bal:
        u = {k: val for k, (val, cnt) in bal.items()}
        if u.get("entry", 1) > 1.45:
            add(3, "Balance", f"Understeer on corner entry (steering {u['entry']:.1f}x what the turn needs).",
                "Lower the differential braking sensitivity; check front toe-out and rake (a little front "
                "toe-out or positive rake helps turn-in).")
        elif u.get("entry", 1) < 0.75:
            add(2, "Balance", "The rear rotates or steps out on corner entry.",
                "Move brake balance toward the front, and raise differential braking sensitivity a little.")
        if u.get("mid", 1) > 1.45:
            add(3, "Balance", f"Mid-corner understeer (steering {u['mid']:.1f}x what the turn needs).",
                "Lower the front anti-roll bar or raise the rear one. Keep front and rear natural frequency "
                "balanced, and don't run the ride height too low.")
        elif u.get("mid", 1) < 0.75:
            add(2, "Balance", "The car is loose through the middle of corners.",
                "Raise the front anti-roll bar or lower the rear one.")
        if u.get("exit", 1) > 1.45:
            add(3, "Balance", f"Understeer on throttle at corner exit (steering {u['exit']:.1f}x).",
                "Lower the differential initial torque (too high pushes wide). On rear-wheel drive, raise "
                "acceleration sensitivity for more rotation under power; on front-wheel drive, even out "
                "front tyre spin.")
        elif u.get("exit", 1) < 0.75:
            add(3, "Balance", "Power oversteer on corner exit.",
                "Lower the differential acceleration sensitivity, and add rear toe-in on rear-wheel drive "
                "for stability under power.")
        if u.get("fast", 1) > 1.45:
            add(2, "Aero", "Understeer in fast corners.",
                "Add rear downforce (it costs top speed and tyre wear).")
        elif u.get("fast", 1) < 0.75:
            add(2, "Aero", "The rear is light in fast corners.",
                "Add rear downforce for stability, or reduce front downforce.")

    # ── traction + brakes ──
    S = {k: _col(df, f"tyre_slip_{k}") for k in ("fl", "fr", "rl", "rr")}
    if S["fl"].max() > 0.2:
        p_on = (thr > 0.8) & (v > 3) & (gear >= 1)
        if p_on.sum() > 100:
            rear = np.maximum(S["rl"], S["rr"])[p_on]
            front = np.maximum(S["fl"], S["fr"])[p_on]
            spin = float(np.mean(np.maximum(rear, front) > 1.15))
            if spin > 0.12:
                axle = "rear" if np.mean(rear > 1.15) >= np.mean(front > 1.15) else "front"
                add(2, "Traction", f"Wheelspin ({axle} axle) in {spin * 100:.0f}% of your full-throttle time.",
                    "Add rear toe-in for stability under power (rear-wheel drive), review differential "
                    "acceleration sensitivity, and lift sooner or progressively on exit.")
        b_on = (brk > 0.5) & (v > 10)
        if b_on.sum() > 60:
            fl = float(np.mean(np.minimum(S["fl"], S["fr"])[b_on] < 0.85))
            rr = float(np.mean(np.minimum(S["rl"], S["rr"])[b_on] < 0.85))
            if fl > 0.08 and fl >= rr:
                add(2, "Brakes", f"The front tyres lock in {fl * 100:.0f}% of heavy braking.",
                    "Move brake balance a click or two toward the rear, or brake slightly earlier and softer.")
            elif rr > 0.05:
                add(2, "Brakes", f"The rear tyres lock in {rr * 100:.0f}% of heavy braking.",
                    "Move brake balance toward the front for stability.")

    # ── tyres + ride height ──
    ft = (_col(df, "tyre_temp_fl") + _col(df, "tyre_temp_fr")) / 2
    rt = (_col(df, "tyre_temp_rl") + _col(df, "tyre_temp_rr")) / 2
    if ft.max() > 20 and rt.max() > 20:
        dtp = float(np.mean(rt[v > 5]) - np.mean(ft[v > 5])) if (v > 5).any() else 0.0
        if dtp > 10:
            add(1, "Tyres", f"The rear tyres run {dtp:.0f}C hotter than the fronts.",
                "They will wear faster: try moving ballast forward and adjust the spring rate to match.")
        elif dtp < -10:
            add(1, "Tyres", f"The front tyres run {-dtp:.0f}C hotter than the rears.",
                "Fronts are doing the work: lower the front anti-roll bar or add rear downforce to share it.")
    rh = _col(df, "ride_height_mm")
    if rh.max() > 5 and (v > 10).any() and float(np.mean(rh[v > 10] < 5.0)) > 0.01:
        add(1, "Suspension", "The car may be bottoming out (ride height near zero).",
            "Raise the ride height, or stiffen the springs: keep it as low as you can without bottoming.")

    hints.sort(key=lambda h: -h["sev"])
    return hints
