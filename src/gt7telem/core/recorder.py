"""Lap and race recording without a window: the packet thinning, the start-line state machine, auto lap, pause
handling, the lap/race file format, personal bests and the reference lap.

The Tk Dashboard imports the pure pieces from here (sample_dict, thin_accept, the auto-lap rules, the file builders,
personal bests, the reference store), and the terminal UI drives the whole Recorder. Files written by either are
identical, and tests/test_recorder.py feeds one packet stream to both and compares what they save.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import lapclean, leaderboard
from .. import udp as telem

# ── rates and thresholds (the Dashboard re-exports these) ────────────────────────────────────────────────────────
RECORD_TAP_SLACK_S = 0.004
RECORD_QUEUE_MAX = 6000
AUTO_LAP_MIN_KPH = 5.0
AUTO_LAP_ARM_HOLD_S = 1.5
AUTO_LAP_LEAVE_HOLD_S = 3.0
RACE_SAMPLE_WARN = 150_000
RACE_SAMPLE_MAX = 400_000
SKIPPED = Path("skipped")          # returned by a save that was deliberately not written (junk / duplicate)
FALLBACK_SAVE_DIR = Path.home() / "TRACE" / "unsaved"


def next_due(due, now, dt):
    nxt = due + dt if due > 0 else now + dt
    return now + dt if nxt <= now else nxt


def thin_accept(due, now, dt):
    """Is the packet that arrived at `now` a sample when one is wanted every `dt` seconds? -> (accept, new_due)."""
    if due <= 0.0:
        return True, now + dt
    if now + RECORD_TAP_SLACK_S < due:
        return False, due
    nxt = due + dt
    if nxt < now - dt:
        nxt = now + dt
    return True, nxt


def auto_lap_in_session(d):
    if d.get("loading") or not d.get("car_on_track", False):
        return False
    return int(d.get("total_laps") or 0) == 0


def auto_lap_can_arm(d):
    if d.get("paused") or not auto_lap_in_session(d):
        return False
    return float(d.get("speed_kmh") or 0.0) >= AUTO_LAP_MIN_KPH


def lap_clock_running(cur_lap, total_laps, paused, on_track, loading):
    if cur_lap < 1 or paused or not on_track or loading:
        return False
    return not (total_laps > 0 and cur_lap > total_laps)


def ms_to_laptime(ms):
    if not ms or ms <= 0:
        return "--:--.---"
    m, rest = divmod(int(ms), 60000)
    return f"{m}:{rest / 1000:06.3f}"


class Session:
    """Everything that is 'the current recording'. One shared instance (`session`) is used by the Dashboard."""
    recording = False
    waiting = False
    samples: list = []
    start_t = 0.0
    cur_lap = 0
    prev_lap = 0
    laps_saved: list = []
    race_recording = False
    race_samples: list = []
    race_start_t = 0.0
    paused = False
    paused_at = None

    def __init__(self):
        self.samples, self.laps_saved, self.race_samples = [], [], []


session = Session()


# ── the sample and the file formats ──────────────────────────────────────────────────────────────────────────────
def sample_dict(d, start_t, now=None):
    if now is None:
        now = time.monotonic()

    def _f(k):
        try:
            return round(float(d.get(k) or 0), 4)
        except Exception:
            return 0.0

    def _i(k):
        try:
            return int(d.get(k) or 0)
        except Exception:
            return 0

    def _b(k):
        try:
            return bool(d.get(k))
        except Exception:
            return False

    def _f_tuple(tup, idx):
        try:
            return round(float(tup[idx]), 4) if tup else 0.0
        except Exception:
            return 0.0

    return {
        "t": round(now - start_t, 3),
        "world_x": _f("world_x"), "world_y": _f("world_y"),
        "world_z": _f("world_z"), "heading": _f("heading"),
        "track_position": _f("track_position"),
        "speed_kmh": _f("speed_kmh"),
        "vel_x": _f("vel_x"), "vel_y": _f("vel_y"),
        "vel_z": _f("vel_z"), "ang_x": _f("ang_x"),
        "ang_y": _f("ang_y"), "ang_z": _f("ang_z"),
        "throttle": _f("throttle"), "brake": _f("brake"),
        "steering": _f("steering"), "clutch": _f("clutch"),
        "steering_source": d.get("steering_source") or "heading",
        "clutch_engaged": _f("clutch_engaged"),
        "gear": _i("gear"), "suggested_gear": _i("suggested_gear"),
        "rpm": _f("rpm"), "max_rpm": _i("max_rpm"),
        "rpm_warning": _i("rpm_warning"), "rpm_limiter": _i("rpm_limiter"),
        "rpm_after_clutch": _f("rpm_after_clutch"),
        "boost": _f("boost"),
        "tyre_temp_fl": _f("tyre_temp_fl"), "tyre_temp_fr": _f("tyre_temp_fr"),
        "tyre_temp_rl": _f("tyre_temp_rl"), "tyre_temp_rr": _f("tyre_temp_rr"),
        "tyre_slip_fl": _f("tyre_slip_fl"), "tyre_slip_fr": _f("tyre_slip_fr"),
        "tyre_slip_rl": _f("tyre_slip_rl"), "tyre_slip_rr": _f("tyre_slip_rr"),
        "susp_fl": _f("susp_fl"), "susp_fr": _f("susp_fr"),
        "susp_rl": _f("susp_rl"), "susp_rr": _f("susp_rr"),
        "ride_height_mm": _f("ride_height_mm"),
        "fuel_remaining": _f("fuel_remaining"), "fuel_capacity": _f("fuel_capacity"),
        "is_ev": _b("is_ev"),
        "oil_temp": _f("oil_temp"), "water_temp": _f("water_temp"),
        "oil_pressure": _f("oil_pressure"),
        "lap_number": _i("lap_number"),
        "current_position": _i("current_position"),
        "total_positions": _i("total_positions"),
        "in_pit": _b("in_pit"),
        "time_of_day": _i("time_of_day"),
        "est_top_speed": _i("est_top_speed"),
        "car_id": _i("car_id"),
        "flags_8e": _i("flags_8e"),
        "flags_8f": _i("flags_8f"),
        "flags_93": _i("flags_93"),
        "wheel_rotation": _f("wheel_rotation"),
        "steering_angular_velocity": _f("steering_angular_velocity"),
        "sway": _f("sway"),
        "heave": _f("heave"),
        "surge": _f("surge"),
        "torque_vectors": d.get("torque_vectors") or [],
        "energy_recovery": _f("energy_recovery"),
        "throttle_filtered": _f("throttle_filtered"),
        "brake_filtered": _f("brake_filtered"),
        "surface_type": "".join(d.get("surface_type") or []),
        "current_lap_ms": _i("current_lap_ms"),
        "wheel_steering_angle_l": _f_tuple(d.get("wheel_steering_angle"), 0),
        "wheel_steering_angle_r": _f_tuple(d.get("wheel_steering_angle"), 1),
        "wheel_base": _f("wheel_base"),
        "car_category": d.get("car_category") or "",
    }


def lap_distance(samples):
    if any(s.get("world_x", 0) != 0 for s in samples):
        return sum(math.sqrt((samples[i]["world_x"] - samples[i - 1]["world_x"]) ** 2 +
                             (samples[i]["world_z"] - samples[i - 1]["world_z"]) ** 2)
                   for i in range(1, len(samples)))
    return sum(samples[i].get("speed_kmh", 0) / 3.6 * max(0.0, samples[i].get("t", 0) - samples[i - 1].get("t", 0))
               for i in range(1, len(samples)))


def build_lap_data(ts, track, car, ui_track, ui_car, lap_s, samples, incomplete, gear_ratios, tyre_radius_m):
    return {
        "recorded_at": ts, "track": track, "car": car,
        "track_display": ui_track or track, "car_display": ui_car or car,
        "lap_time_s": round(lap_s, 3), "lap_distance_m": round(lap_distance(samples), 1),
        "total_samples": len(samples), "incomplete": incomplete, "physics_era": leaderboard.PHYSICS_ERA,
        "gear_ratios": gear_ratios, "tyre_radius_m": round(float(tyre_radius_m or 0.0), 4), "samples": samples,
    }


def build_race_data(ts, track, car, ui_track, ui_car, duration_s, samples, gear_ratios):
    return {
        "recorded_at": ts, "track": track, "car": car,
        "track_display": ui_track or track, "car_display": ui_car or car,
        "race_duration_s": round(duration_s, 3), "physics_era": leaderboard.PHYSICS_ERA,
        "total_samples": len(samples), "gear_ratios": gear_ratios, "samples": samples,
    }


def save_json(path, data, what, log=print, fallback=True):
    """Write a recording through a temp file; return the path written or None. Never raises."""
    targets = [path] + ([FALLBACK_SAVE_DIR / path.name] if fallback else [])
    for target in targets:
        tmp = target.with_name(target.name + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp, target)
        except Exception as e:
            log(f"SAVE FAILED: couldn't write {what} to {target.parent} ({e})")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if target != path:
            log(f"{what.capitalize()} saved to the fallback folder instead: {target}")
        return target
    return None


# ── personal bests and the reference lap ─────────────────────────────────────────────────────────────────────────
def reference_path(laps_folder, track, car_safe):
    return Path(laps_folder) / track / f"reference_{car_safe}.json"


def pb_path(laps_folder, track):
    return Path(laps_folder) / track / "personal_bests.json"


def load_pbs(laps_folder, track):
    p = pb_path(laps_folder, track)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_new_pb(laps_folder, track, car_safe, new_time):
    entry = load_pbs(laps_folder, track).get(car_safe, {})
    prev = entry.get("lap_time_s")
    if prev is not None and not leaderboard.is_current_era(entry):
        prev = None
    return prev is None or new_time < prev


def write_pb(laps_folder, track, car_safe, car_display, new_time, ts, log=print):
    bests = load_pbs(laps_folder, track)
    bests[car_safe] = {"lap_time_s": round(new_time, 3), "car_display": car_display, "recorded_at": ts,
                       "physics_era": leaderboard.PHYSICS_ERA}
    save_json(pb_path(laps_folder, track), bests, "personal best", log, fallback=False)


class ReferenceStore:
    """Cached read of a track+car reference lap, plus the live delta against it."""

    def __init__(self, laps_folder_fn):
        self._folder = laps_folder_fn
        self.cache = {"key": None, "samples": None, "positions": None, "mtime": None, "checked": 0.0}

    def invalidate(self):
        self.cache["key"] = None

    def samples(self, track, car_safe):
        key = (track, car_safe)
        c = self.cache
        now = time.monotonic()
        if c["key"] == key and now - c.get("checked", 0.0) < 2.0:
            return c["samples"]
        path = reference_path(self._folder(), track, car_safe)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if c["key"] == key and c.get("mtime") == mtime:
            c["checked"] = now
            return c["samples"]
        samples = positions = None
        if mtime is not None:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    ref = json.load(f)
                ss = sorted((s for s in ref.get("samples", []) if isinstance(s, dict)),
                            key=lambda s: float(s.get("track_position") or 0))
                if ss and leaderboard.is_current_era(ref):
                    samples = ss
                    positions = [float(s.get("track_position") or 0) for s in ss]
            except Exception:
                samples = positions = None
        c.update(key=key, samples=samples, positions=positions, mtime=mtime, checked=now)
        return samples

    def delta(self, track, car_safe, track_pos, elapsed):
        """Seconds ahead (-) / behind (+) the reference at this spot, or None."""
        ref = self.samples(track, car_safe)
        if not ref or elapsed <= 0.5 or track_pos <= 0:
            return None
        pos = self.cache["positions"]
        j = bisect.bisect_left(pos, track_pos)
        cands = [k for k in (j - 1, j) if 0 <= k < len(pos)]
        nearest = ref[min(cands, key=lambda k: abs(pos[k] - track_pos))]
        t = nearest.get("t")
        return None if t is None else elapsed - t


# ── the recorder ─────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Hooks:
    """What the recorder needs from its front end. Every field has a harmless default."""
    laps_folder: callable = lambda: Path.home() / "TRACE" / "laps"
    track: callable = lambda: ""
    car: callable = lambda: ""
    rate: callable = lambda: 30
    log: callable = print
    state: callable = lambda kind: None             # "armed" | "recording" | "idle" | "race" | "race_idle"
    ask_track: callable = lambda kind, samples: ""  # front end may pick a track name when none is set
    notify: callable = lambda title, msg: None
    lap_saved: callable = lambda meta, is_pb: None
    auto_lap_enabled: callable = lambda: False
    gear_info: callable = lambda: ([], 0.0)         # (gear ratios, tyre radius)


class Recorder:
    def __init__(self, hooks: Hooks | None = None, session_obj: Session | None = None):
        self.h = hooks or Hooks()
        self.s = session_obj or session
        self.q = deque(maxlen=RECORD_QUEUE_MAX)
        self.rec_due = self.rec_due_race = 0.0
        self.lap_since = self.race_since = 0.0
        self.auto_since = self.auto_off_since = None
        self.auto_active = self.auto_block = False
        self.last_sig = None
        self.refs = ReferenceStore(lambda: self.h.laps_folder())
        self.last_saved: list[Path] = []
        self._lock = threading.Lock()
        self.threads: list[threading.Thread] = []
        self.on_lap_complete = None      # callback(samples, ref_samples) after a timed lap is saved

    # -- packets ---------------------------------------------------------------------------------
    def tap(self, d, mono):
        """Receive thread, once per good packet: thin to the record rate and queue."""
        if self.s.paused:
            return
        dt = 1.0 / max(self.h.rate(), 1)
        if self.s.recording or self.s.waiting:
            ok, self.rec_due = thin_accept(self.rec_due, mono, dt)
            if ok:
                self.q.append(("lap", mono, dict(d)))
        if self.s.race_recording:
            ok, self.rec_due_race = thin_accept(self.rec_due_race, mono, dt)
            if ok:
                self.q.append(("race", mono, dict(d)))

    def drain(self):
        """Process the queue (call at ~30 Hz from the UI thread)."""
        q = self.q
        while q:
            try:
                kind, mono, d = q.popleft()
            except IndexError:
                break
            if kind == "lap":
                if (self.s.recording or self.s.waiting) and mono >= self.lap_since:
                    self.record_sample(d, mono)
            elif self.s.race_recording and mono >= self.race_since:
                self.record_race_sample(d, mono)

    # -- lap -------------------------------------------------------------------------------------
    def start_lap(self):
        self.rec_due = 0.0
        self.lap_since = time.monotonic()
        s = self.s
        s.waiting, s.recording, s.samples = True, False, []
        s.cur_lap = telem.get_int("lap_number")
        s.prev_lap = s.cur_lap
        self.h.state("armed")
        self.h.log("Armed -- cross the start line to begin recording")

    def stop_lap(self):
        if self.auto_active:
            self.auto_active, self.auto_block = False, True
        s = self.s
        s.recording = s.waiting = False
        self.h.state("idle")
        samples, s.samples = s.samples, []
        if samples:
            self.save_lap(samples, None, incomplete=True)
        else:
            self.h.log("Stopped -- no samples recorded.")

    def record_sample(self, d, now=None):
        s = self.s
        now = time.monotonic() if now is None else now
        try:
            lap = int(d.get("lap_number")) if d.get("lap_number") is not None else s.prev_lap
        except (TypeError, ValueError):
            lap = s.prev_lap
        if s.waiting:
            if lap > s.prev_lap:
                s.waiting, s.recording, s.start_t, s.prev_lap = False, True, now, lap
                self.h.state("recording")
                self.h.log(f"Start line crossed -- recording lap {lap}")
            else:
                s.prev_lap = lap
            return
        if lap < s.prev_lap:
            n = len(s.samples)
            if n >= 100:
                self.h.log(f"Lap counter reset (lap {s.prev_lap} -> {lap}) -- saving the partial lap ({n} samples)")
                self.save_lap(s.samples, None, incomplete=True, background=True)
            else:
                self.h.log("Lap counter reset -- discarded a short partial lap")
            s.samples, s.prev_lap, s.recording, s.waiting = [], lap, False, True
            self.h.state("armed")
            return
        if lap > s.prev_lap:
            n = len(s.samples)
            if n >= 100:
                self.h.log(f"Lap complete -- {n} samples")
                last_ms = int(d.get("last_lap_ms") or 0)
                self.save_lap(s.samples, last_ms if last_ms > 0 else None, background=True)
            else:
                self.h.log(f"Lap changed after only {n} samples -- not saved")
            s.samples, s.start_t = [], now
        s.prev_lap = lap
        s.samples.append(sample_dict(d, s.start_t, now))

    # -- race ------------------------------------------------------------------------------------
    def start_race(self):
        s = self.s
        s.race_samples, s.race_start_t = [], time.monotonic()
        self.race_since, self.rec_due_race = s.race_start_t, 0.0
        s.race_recording = True
        self.h.state("race")
        self.h.log("Race recording started")

    def stop_race(self, notify_end=False):
        s = self.s
        s.race_recording = False
        self.h.state("race_idle")
        samples, s.race_samples = s.race_samples, []
        if samples:
            self.save_race(samples, background=True, notify_end=notify_end)
        else:
            self.h.log("Race recording stopped -- no samples recorded.")

    def record_race_sample(self, d, now=None):
        s = self.s
        if not s.race_recording:
            return
        s.race_samples.append(sample_dict(d, s.race_start_t, now))
        n = len(s.race_samples)
        if n == RACE_SAMPLE_WARN:
            self.h.log(f"Race recording is large ({n:,} samples). It is held in memory until the race ends.")
        elif n >= RACE_SAMPLE_MAX:
            self.h.log(f"Race recording hit the {RACE_SAMPLE_MAX:,}-sample limit -- stopping and saving now.")
            self.stop_race()

    def on_race_start(self):
        if not self.s.race_recording:
            self.start_race()
            self.h.log("Auto: race start detected -- recording started")

    def on_race_end(self):
        if self.s.race_recording:
            self.h.log("Auto: race end detected -- stopping recording")
            self.stop_race(notify_end=True)

    # -- pause -----------------------------------------------------------------------------------
    def pause(self):
        if not self.s.paused:
            self.s.paused_at = time.monotonic()
        self.s.paused = True
        self.h.log("Auto: game paused -- recording frozen")

    def resume(self):
        s = self.s
        if s.paused and s.paused_at is not None:
            gap = max(0.0, time.monotonic() - s.paused_at)
            s.start_t += gap
            s.race_start_t += gap
        s.paused_at, s.paused = None, False
        self.h.log("Auto: game resumed -- recording continues")

    # -- auto lap --------------------------------------------------------------------------------
    def auto_lap_disarm(self, why):
        s = self.s
        n = len(s.samples)
        s.recording = s.waiting = False
        s.samples = []
        self.auto_active = False
        self.auto_since = self.auto_off_since = None
        self.h.state("idle")
        self.h.log(f"Auto lap: {why}" + (f" -- partial lap ({n} samples) discarded" if n else ""))

    def auto_lap_step(self, d, now):
        s = self.s
        if not self.h.auto_lap_enabled():
            if self.auto_active:
                self.auto_lap_disarm("turned off")
            self.auto_since = None
            return
        in_session = auto_lap_in_session(d)
        if not in_session:
            self.auto_block = False
        if self.auto_active:
            if in_session:
                self.auto_off_since = None
            elif self.auto_off_since is None:
                self.auto_off_since = now
            elif now - self.auto_off_since >= AUTO_LAP_LEAVE_HOLD_S:
                self.auto_lap_disarm("session ended")
            return
        if s.recording or s.waiting or s.race_recording or s.paused or self.auto_block:
            self.auto_since = None
            return
        if not auto_lap_can_arm(d):
            self.auto_since = None
            return
        if self.auto_since is None:
            self.auto_since = now
        elif now - self.auto_since >= AUTO_LAP_ARM_HOLD_S:
            self.auto_since = self.auto_off_since = None
            self.auto_active = True
            self.start_lap()
            self.h.log("Auto lap: armed -- recording starts at the next start/finish crossing")

    # -- saving ----------------------------------------------------------------------------------
    def _run(self, fn, background):
        def safe():
            try:
                with self._lock:
                    return fn()
            except Exception as e:
                self.h.log(f"SAVE ERROR: {type(e).__name__}: {e}")
                return None
        if not background:
            return safe()
        self.threads = [t for t in self.threads if t.is_alive()]
        t = threading.Thread(target=safe, daemon=True)
        self.threads.append(t)
        t.start()
        return None

    def wait(self, timeout=5.0):
        for t in list(self.threads):
            t.join(timeout)

    def save_lap(self, samples, lap_time_ms=None, incomplete=False, background=False, final=True):
        h, s = self.h, self.s
        gt7 = bool(lap_time_ms and lap_time_ms > 0)
        secs = (lap_time_ms / 1000.0) if gt7 else max(0.0, time.monotonic() - s.start_t)
        why = lapclean.junk_reason(samples, secs, lap_distance(samples) if len(samples) > 1 else 0.0)
        if why:
            h.log(f"Lap not saved -- {why}")
            return SKIPPED
        sig = lapclean.samples_signature("", "", secs, samples)
        if sig is not None and sig == self.last_sig:
            h.log("Lap not saved -- identical to the lap just saved (duplicate)")
            return SKIPPED
        ui_track = h.track().strip()
        if not ui_track and final:
            ui_track = (h.ask_track("lap", samples) or "").strip()
        ui_car = h.car().strip()
        track, car = telem.sanitize(ui_track), ui_car or "unknown"
        car_safe = telem.sanitize(car)
        folder = Path(h.laps_folder())
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        lap_path = folder / track / f"{car_safe}_{ts}.json"
        ref = reference_path(folder, track, car_safe)
        h.log(f"Lap time: {ms_to_laptime(int(secs * 1000))}  ({'GT7' if gt7 else 'elapsed'}, {len(samples)} samples"
              f"{'  INCOMPLETE' if incomplete else ''})")
        lap_num = int(samples[-1].get("lap_number", 0)) if samples else 0
        ratios, radius = h.gear_info()
        fuel_used = (samples[0].get("fuel_remaining", 0) - samples[-1].get("fuel_remaining", 0)) if samples else 0.0

        def work():
            data = build_lap_data(ts, track, car, ui_track, ui_car, secs, samples, incomplete, list(ratios), radius)
            is_pb = (not incomplete and gt7 and is_new_pb(folder, track, car_safe, secs))
            saved = save_json(lap_path, data, "lap", h.log)
            meta = {k: v for k, v in data.items() if k != "samples"}
            meta["fuel_used"] = round(max(0.0, fuel_used), 2)
            meta["lap_number"] = lap_num
            if saved is None:
                h.log("Lap NOT saved -- it's still in memory.")
                if final:
                    s.laps_saved.append(meta)
                return None
            meta["file"] = str(saved)
            s.laps_saved.append(meta)
            self.last_sig = sig
            self.last_saved.append(saved)
            h.log(f"Saved: {saved}")
            h.lap_saved(meta, is_pb)
            if incomplete or saved != lap_path:
                return saved
            if is_pb:
                write_pb(folder, track, car_safe, ui_car or car, secs, ts, h.log)
                h.log(f"New personal best for {ui_car or car} @ {track}!")
                h.notify("New Personal Best", f"{ui_car or car} @ {track}\n{ms_to_laptime(int(secs * 1000))}")
            if not gt7:
                return saved
            ref_time, ref_samples = 0.0, None
            if ref.exists():
                try:
                    with open(ref, encoding="utf-8-sig") as f:
                        rd = json.load(f)
                    if leaderboard.is_current_era(rd):
                        ref_time = float(rd.get("lap_time_s") or 0)
                        ref_samples = rd.get("samples")
                except Exception:
                    ref_time = 0.0
            if ref_samples and self.on_lap_complete:
                try:
                    self.on_lap_complete(samples, ref_samples)
                except Exception:
                    pass
            if (ref_time <= 0 or secs < ref_time) and save_json(ref, data, "reference lap", h.log, fallback=False):
                h.log(f"Reference updated -> {ref.name}" + (f"  (new best by {ref_time - secs:.3f}s)"
                                                              if ref_time > 0 else "  (first lap)"))
                self.refs.invalidate()
            return saved

        return self._run(work, background)

    def save_race(self, samples, background=False, final=True, notify_end=False):
        h, s = self.h, self.s
        why = lapclean.junk_reason(samples)
        if why:
            h.log(f"Race not saved -- {why}")
            return SKIPPED
        ui_track = h.track().strip()
        if not ui_track and final:
            ui_track = (h.ask_track("race", samples) or "").strip()
        ui_car = h.car().strip()
        track, car = telem.sanitize(ui_track), ui_car or "unknown"
        car_safe = telem.sanitize(car)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = Path(h.laps_folder()) / track / "races"
        paused_for = max(0.0, time.monotonic() - s.paused_at) if s.paused and s.paused_at is not None else 0.0
        duration = max(0.0, time.monotonic() - s.race_start_t - paused_for)
        ratios, _r = h.gear_info()

        def work():
            data = build_race_data(ts, track, car, ui_track, ui_car, duration, samples, list(ratios))
            saved = save_json(folder / f"race_{car_safe}_{ts}.json", data, "race", h.log)
            if saved is not None:
                self.last_saved.append(saved)
                h.log(f"Race saved: {saved}  ({len(samples)} samples, {duration:.1f}s)")
            else:
                h.log(f"Race NOT saved ({len(samples)} samples)")
            if notify_end:
                h.notify("Race Ended", "Recording saved." if saved is not None else "Recording could NOT be saved.")
            return saved

        return self._run(work, background)
