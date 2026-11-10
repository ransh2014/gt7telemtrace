"""Everything the Live screen does each frame, without a window: lap clock, delta vs reference, gap to the top lap,
fuel and tyre strategy, spoken alerts, coach cues, pit calls, track detection and recording.

A front end gives it a `Hooks` (where laps live, what the track and car boxes say, how to log) and calls `poll()` about
ten times a second; it gets back one plain dict to draw. The terminal UI does exactly this; the Tk Dashboard shares the
pure pieces (recorder.py) and the same rules.
"""

from __future__ import annotations

import threading
import time

from .. import coach, engineer, ghost, leaderboard, trackdetect, voice
from .. import udp as telem
from . import recorder as rec

GHOST_SETTLE_S = 3.0
TYRE_HOT_C = 100
TYRE_COLD_C = 60


def tyre_alerts(temps):
    """(hot, cold) for the four tyre temperatures: the same rule the Dashboard's alert lights use."""
    live = [t for t in temps if t is not None]
    return any(t > TYRE_HOT_C for t in live), any(t < TYRE_COLD_C for t in live)


class LiveEngine:
    def __init__(self, hooks: rec.Hooks | None = None, speak=voice.speak, voice_enabled=lambda: True,
                 ghost_enabled=lambda: True, analytics_enabled=lambda: False):
        self.hooks = hooks or rec.Hooks()
        self.recorder = rec.Recorder(self.hooks, rec.Session())
        self.recorder.on_lap_complete = self._after_lap
        self.session = self.recorder.s
        self.strategy = telem.StintTracker()
        self.announcer = voice.AlertAnnouncer()
        self.live_coach = coach.LiveCoach()
        self.speak, self.voice_enabled = speak, voice_enabled
        self.ghost_enabled, self.analytics_enabled = ghost_enabled, analytics_enabled
        self.ghosts, self._ghost_fetching = {}, set()
        self._ghost_stable = {"key": None, "since": 0.0}
        self.radio = {"text": "", "until": 0.0, "lap": None}
        self.lap_start_t = None
        self.prev_lap = None
        self.td_store = trackdetect.FingerprintStore()
        self.td_lap, self.td_rejected, self.track_offer = -1, set(), None
        self._td_race = {"done": False, "idx": [], "tried": set(), "lap": None}
        self.tips = []
        self._started = False

    # -- wiring ----------------------------------------------------------------------------------
    def start(self):
        """Hook the live stream: packet tap and the race/pause events."""
        if self._started:
            return
        self._started = True
        telem.set_packet_tap(self.recorder.tap)
        telem.register_event("race_start", lambda p: self.recorder.on_race_start())
        telem.register_event("race_end", lambda p: self.recorder.on_race_end())
        telem.register_event("pause", lambda p: self.recorder.pause())
        telem.register_event("resume", lambda p: self.recorder.resume())
        threading.Thread(target=self._fetch_shared, daemon=True).start()

    def _fetch_shared(self):
        rows = trackdetect.fetch_shared()
        if rows is not None:
            self.td_store.set_shared(rows)

    def _after_lap(self, samples, ref_samples):
        from .. import corners
        lap_df, ref_df = corners.frame_from_samples(samples), corners.frame_from_samples(ref_samples)
        if lap_df is None or ref_df is None:
            return
        _a, _b, deltas = corners.match_corners(lap_df, ref_df)
        if deltas:
            self.hooks.log(f"{corners.describe_time_loss(deltas, 3)}  (vs your reference lap)")
        self.tips = coach.analyse(lap_df, ref_df)
        self.hooks.log(coach.describe(self.tips))
        self.live_coach.set_tips(self.tips)

    def _learn_track(self, samples, incomplete, ui_track):
        if incomplete or not ui_track or not trackdetect.can_learn(ui_track, samples):
            return
        fp = trackdetect.fingerprint_of(samples)
        if fp is not None and self.td_store.add(ui_track, fp) and self.analytics_enabled():
            threading.Thread(target=trackdetect.submit_shared,
                             args=(ui_track, fp, trackdetect.lap_length(samples)), daemon=True).start()

    # -- controls --------------------------------------------------------------------------------
    def toggle_lap(self):
        r = self.recorder
        if self.session.recording or self.session.waiting:
            r.stop_lap()
        else:
            r.start_lap()

    def toggle_race(self):
        r = self.recorder
        if self.session.race_recording:
            r.stop_race()
        else:
            r.start_race()
            self._td_race.update(done=False, idx=[], tried=set(), lap=None)

    def accept_track(self):
        if self.track_offer and not self.hooks.track().strip():
            name, self.track_offer = self.track_offer, None
            self.hooks.log(f"Track set to {name} (detected from the start line)")
            return name
        self.track_offer = None
        return None

    def decline_track(self):
        if self.track_offer:
            self.td_rejected.add(self.track_offer)
        self.track_offer = None

    # -- one frame -------------------------------------------------------------------------------
    def poll(self, d: dict | None = None, now: float | None = None) -> dict:
        d = telem.get_snapshot() if d is None else d
        now = time.monotonic() if now is None else now
        connected = bool(telem.is_connected())
        s = self.session
        self.recorder.drain()
        out = {"connected": connected, "recording": s.recording, "armed": s.waiting, "race_recording": s.race_recording,
               "paused": s.paused, "delta": None, "gap": None, "cur_elapsed": 0.0, "strategy": {}, "cue": "",
               "track_offer": self.track_offer, "alerts": [], "tips": self.tips}
        if not d or not connected:
            return out
        cur_lap, tot = int(d.get("lap_number") or 0), int(d.get("total_laps") or 0)
        temps = tuple(d.get(k) or 0 for k in ("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr"))
        try:
            self.strategy.update(cur_lap, tot, float(d.get("fuel_remaining") or 0), d.get("last_lap_ms"), temps,
                                 float(d.get("speed_kmh") or 0))
            snap = self.strategy.snapshot()
        except Exception:
            snap = {}
        out["strategy"] = snap
        self.recorder.auto_lap_step(d, now)

        # lap clock
        if cur_lap != self.prev_lap:
            self.lap_start_t, self.prev_lap = now, cur_lap
        pos = float(d.get("track_position") or 0)
        running = rec.lap_clock_running(cur_lap, tot, s.paused, bool(d.get("car_on_track", True)), d.get("loading"))
        live_ms = d.get("current_lap_ms")
        if not running:
            elapsed, self.lap_start_t = 0.0, None
        elif live_ms is not None and live_ms > 0:
            elapsed = live_ms / 1000.0
        elif self.lap_start_t is not None:
            elapsed = now - self.lap_start_t
        else:
            elapsed = 0.0
        out["cur_elapsed"] = elapsed

        track, car = self.hooks.track().strip(), self.hooks.car().strip()
        if track:
            out["delta"] = self.recorder.refs.delta(telem.sanitize(track), telem.sanitize(car or "unknown"),
                                                    pos, elapsed)
        self._maybe_fetch_ghost(car, track, now)
        top = self.ghosts.get((car, track)) if self.ghost_enabled() else None
        out["gap"] = top.gap(pos, elapsed) if top is not None and elapsed > 0.5 else None

        # alerts, coach cue and pit call
        fuel_laps = snap.get("laps_of_fuel")
        hot, cold = tyre_alerts(temps)
        active = {k for k, on in (("fuel", fuel_laps is not None and fuel_laps < 2), ("hot", hot), ("cold", cold)) if on}
        out["alerts"] = sorted(active)
        if self.voice_enabled() and not s.paused:
            for line in self.announcer.update(active, now, fuel_laps=fuel_laps if fuel_laps is not None else 99):
                self.speak(line)
        msg = None
        if not s.paused:
            msg = self.live_coach.update(pos, float(d.get("speed_kmh") or 0))
            if not msg and tot > 0 and cur_lap > 0:
                call = engineer.pit_call(cur_lap, tot, snap, last_called=self.radio["lap"])
                if call:
                    msg, self.radio["lap"] = call[0], cur_lap
        if msg:
            self.radio.update(text=msg, until=now + 6.0)
            if self.voice_enabled():
                self.speak(msg)
        out["cue"] = self.radio["text"] if now < self.radio["until"] else ""

        self._detect_track(d, cur_lap)
        out["track_offer"] = self.track_offer
        return out

    # -- ghost -----------------------------------------------------------------------------------
    def _maybe_fetch_ghost(self, car, track, now):
        key = (car, track) if car and track else None
        if key is None or not self.ghost_enabled() or key in self._ghost_fetching:
            return
        if self._ghost_stable["key"] != key:
            self._ghost_stable.update(key=key, since=now)
            return
        if now - self._ghost_stable["since"] < GHOST_SETTLE_S:
            return
        self._ghost_fetching.add(key)

        def work():
            g = None
            try:
                row = leaderboard.get_top_lap_ghost(*key)
                if row:
                    g = ghost.build_ghost(row.get("samples"), row.get("lap_time_ms"))
                    if g is not None:
                        by = f" by {row['psn_name']}" if row.get("psn_name") else ""
                        self.hooks.log(f"Top-lap ghost loaded: {rec.ms_to_laptime(int(row['lap_time_ms']))}{by}")
            except Exception:
                g = None
            self.ghosts[key] = g

        threading.Thread(target=work, daemon=True).start()

    # -- track detection -------------------------------------------------------------------------
    def _detect_track(self, d, lap):
        s = self.session
        if self.hooks.track().strip():
            self.td_lap = lap
            self._td_race["done"] = True
            return
        if s.recording and self.td_lap != lap:
            fp = trackdetect.fingerprint_of(s.samples)
            if fp is None:
                if len(s.samples) > 900:
                    self.td_lap = lap
            else:
                self.td_lap = lap
                for name, _d in self.td_store.match(fp):
                    if name not in self.td_rejected:
                        self.track_offer = name
                        return
        if s.race_recording and not self._td_race["done"] and s.race_samples:
            t = self._td_race
            smp = s.race_samples
            try:
                lp = int(smp[-1].get("lap_number"))
            except (TypeError, ValueError):
                return
            if t["lap"] is not None and lp > t["lap"] and len(t["idx"]) < 2:
                t["idx"].append(len(smp) - 1)
            t["lap"] = lp
            for i in t["idx"]:
                if i in t["tried"]:
                    continue
                fp = trackdetect.fingerprint_of(smp[i:i + 1500])
                if fp is None:
                    continue
                t["tried"].add(i)
                for name, _d in self.td_store.match(fp):
                    if name not in self.td_rejected:
                        t["done"] = True
                        self.track_offer = name
                        return
            if len(t["tried"]) >= 2:
                t["done"] = True

    def learn_after_save(self, ui_track, samples, incomplete):
        try:
            self._learn_track(samples, incomplete, ui_track)
        except Exception:
            pass
