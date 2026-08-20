# TRACE — GT7 Telemetry Suite

**T**elemetry **R**acing **A**nalytics & **C**omparative **E**ngine

A live telemetry dashboard, lap analyst, and race analyst for **Gran Turismo 7** — reads the UDP telemetry stream straight off your PS4/PS5 over your local network. No mods, no jailbreak, just the game's own broadcast data.

Also published on PyPI as [`gt7tracetelem`](https://pypi.org/project/gt7tracetelem/).

Site: **https://gt7trace.netlify.app**

---

## What's in the box

**Live Dashboard**
- Real-time speed, RPM, gear, throttle/brake, tyre temps & slip
- Mini track map with live position
- Configurable recording sample rate (10/20/30/60 Hz)
- Live delta vs. a reference lap
- Connection diagnostics — heartbeats, packet loss, decrypt/parse failures, actionable error messages
- Remembers known-good PS4/PS5 IPs
- Incident timeline
- Lap history table with alerts

**Lap Analyst**
- 14 chart groups: inputs, engine, tyres, dynamics, braking, laps, traction, ratings, and more
- A/B lap comparison
- Dual replay — synced and realtime
- Driver ratings radar
- Track map heatmaps across 9 metrics
- CSV / HTML export

**Race Analyst**
- Same chart-group depth as Lap Analyst, adapted for full races
- Race timeline, minimap heatmap, replay

**Tooling**
- `add_car.py` / `add_track.py` — add missing car/track IDs to the local database
- Car and track ID databases kept current -- the car database is refreshed every time 10 or more new cars have been added since the last update

---

## Install

### Option 1 — pip
```
pip install gt7tracetelem
```
The importable module is **`gt7telem`** (not `gt7tracetelem`) — the PyPI distribution name and the import name are different:
```python
import gt7telem
gt7telem.get_snapshot()
```
Or just run `gt7telem` from a terminal to launch the GUI menu.

> If you already have an unrelated package that installs a top-level `gt7telem` module, the two will conflict. Check first with `pip show gt7telem`.

### Option 2 — from source
Works on Windows, macOS, or Linux with Python 3.10+.
```
git clone https://github.com/ransh2014/gt7telemtrace.git
cd gt7telemtrace
pip install -e .
gt7telem
```

### Option 3 — standalone binaries
No Python required — grab a prebuilt Windows `.exe` or Linux binary from **[gt7trace.netlify.app/setup.html](https://gt7trace.netlify.app/setup.html)**.

---

## Quick start

1. Launch `gt7telem` (or run the source/binary).
2. Pick **Live Dashboard**, **Lap Analyst**, or **Race Analyst** from the menu.
3. Make sure GT7 is running on your PS4/PS5.
4. Enter your console's IP in the Dashboard field and hit Enter — it's remembered for next time.
5. Recorded laps save to a `laps/` folder next to wherever you run it from.

Find your console's IP: **Settings → Network → View Connection Status** on your PS4/PS5.

---

## Public data API

```python
import gt7telem

gt7telem.set_ip("192.168.1.X")
gt7telem.wait_for_connection()

snap = gt7telem.get_snapshot()
print(gt7telem.get_car_name(snap))
```

See `gt7telem.__all__` for the full list of exported functions (`get_snapshot`, `set_car`, `set_track`, `get_diagnostics`, `get_car_name`, `get_track_name`, `all_track_names`, settings persistence, and more). The GUI apps (`dashboard`, `lap_analyst`, `race_analyst`) are available as submodules for anyone building on top of TRACE directly.

---

## Credits

TRACE stands on the shoulders of the people who reverse-engineered GT7's telemetry protocol and maintain the community car/track databases it relies on. Full writeup and credits: **[gt7trace.netlify.app/about.html](https://gt7trace.netlify.app/about.html)**.

## License

MIT — see [LICENSE](LICENSE).

## Support

TRACE is free and always will be. If it's improved your GT7 sessions, a coffee is appreciated: **[ko-fi.com/ransh](https://ko-fi.com/ransh)**.
