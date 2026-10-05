# TRACE — GT7 Telemetry Suite

**T**elemetry **R**acing **A**nalytics & **C**omparative **E**ngine

[![CI](https://github.com/ransh2014/gt7telemtrace/actions/workflows/ci.yml/badge.svg)](https://github.com/ransh2014/gt7telemtrace/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gt7tracetelem?color=00e5ff&label=PyPI&cacheSeconds=300)](https://pypi.org/project/gt7tracetelem/)
[![Python versions](https://img.shields.io/pypi/pyversions/gt7tracetelem?color=7c5cff&cacheSeconds=300)](https://pypi.org/project/gt7tracetelem/)
[![Downloads](https://img.shields.io/pypi/dm/gt7tracetelem?color=ff5c8a&cacheSeconds=300)](https://pypi.org/project/gt7tracetelem/)
[![License: MIT](https://img.shields.io/github/license/ransh2014/gt7telemtrace?cacheSeconds=300)](https://github.com/ransh2014/gt7telemtrace/blob/main/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/ransh2014/gt7telemtrace?cacheSeconds=300)](https://github.com/ransh2014/gt7telemtrace/commits/main)

A live telemetry dashboard, lap analyst, and race analyst for **Gran Turismo 7** — reads the UDP telemetry stream straight off your PS4/PS5 over your local network. No mods, no jailbreak, just the game's own broadcast data, wrapped in three desktop tools you can install with one `pip install`.

Also published on PyPI as [`gt7tracetelem`](https://pypi.org/project/gt7tracetelem/) · Site: **[gt7trace.netlify.app](https://gt7trace.netlify.app)**

> Community tool — not affiliated with, endorsed by, or connected to Polyphony Digital or Sony Interactive Entertainment.

> **Project status:** actively maintained. Regular releases with bug fixes, car/track database refreshes (as [ddm999's gt7info](https://github.com/ddm999/gt7info) updates) and the occasional new feature.

---

## Table of contents

- [Screenshots](#screenshots)
- [What's in the box](#whats-in-the-box)
- [Install](#install)
- [Code signing](#code-signing)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Public data API](#public-data-api)
- [Privacy & analytics](#privacy--analytics)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)
- [Support](#support)

---

## Screenshots

![Live Dashboard](https://raw.githubusercontent.com/ransh2014/gt7telemtrace/main/docs/screenshot-dashboard.png)

![Lap Analyst — Tyres](https://raw.githubusercontent.com/ransh2014/gt7telemtrace/main/docs/screenshot-lapanalyst.png)

![Race Analyst — Inputs](https://raw.githubusercontent.com/ransh2014/gt7telemtrace/main/docs/screenshot-raceanalyst.png)

---

## What's in the box

### Live Dashboard
- Real-time speed, RPM, gear, throttle/brake, tyre temps & slip
- Mini track map with live position
- Configurable recording sample rate (10/20/30/60 Hz)
- Live delta vs. your best lap in the same car on the same track (`reference_<car>.json`)
- Connection diagnostics — heartbeats, packet loss, decrypt/parse failures, actionable error messages
- Remembers every PS4/PS5 IP you've connected to (most recent first), plus an Auto-Detect button that finds the console on your network
- Automatic race recording — starts on a detected race start (race sessions only, not time trial/practice), stops at the flag or when you quit
- **Fuel & tyre strategy (estimates)** — median fuel burn per lap, laps of fuel left, a finish check and a suggested pit-by lap, plus a tyre-stint readout (pace loss, trend, last-lap temperatures). GT7 sends no tyre wear, so the tyre figures are estimates and are labelled that way
- **Spoken alerts** — fuel-low and tyre hot/cold warnings are read out through your system's voice (Windows speech and macOS `say` are built in; on Linux install `speech-dispatcher` for `spd-say`, or `espeak-ng`/`espeak`), falling back to the alert sound if no voice is available. Each alert is spoken when it turns on; fuel and hot-tyre warnings repeat every minute while they last. Untick **`VOICE ALERTS`** in the header's Preferences row to mute. It never blocks the display
- **Live gap to the leaderboard's top lap** — the `VS TOP` readout shows how far ahead or behind the fastest public lap for your car and track you are, at the same spot on the track. It makes one read-only request per car and track per run (once they've stayed put for a few seconds, so typing a name by hand doesn't send a request per letter), and stays silent if you're offline or nobody has set a lap yet. Untick **`TOP-LAP GHOST`** to turn the request off (see [Privacy & analytics](#privacy--analytics))
- **Time-loss summary** — after each lap, the log names the three corners that cost the most time against your reference lap (once you have one), e.g. `Time lost: T4 +0.21s, T9 +0.12s, T2 +0.08s`
- **Preferences row** — `SHARE USAGE DATA`, **`CHECK FOR UPDATES`**, `TOP-LAP GHOST`, **`NOTIFICATIONS`**, `VOICE ALERTS` and `DEBUG LOG` checkboxes, saved to `settings.json`. The `METRICS` and `ALLOW REMOTE` switches (below) sit in the console row above it
- **Prometheus metrics export (off by default)** — tick **`METRICS :9109`** to serve live gauges (speed, RPM, throttle, brake, fuel, current lap time and a connected flag) at `http://127.0.0.1:9109/metrics` for Grafana or any Prometheus scraper. It listens on this PC only. **`ALLOW REMOTE`** widens it to your whole local network (`0.0.0.0`) so a Grafana/Prometheus box elsewhere can scrape it — the endpoint has no password, so anyone on the same Wi-Fi/LAN could then read your live telemetry. Leave it off unless you need it and trust the network. Nothing is sent to the internet either way
- Incident timeline
- Lap history table with alerts

### Lap Analyst
- **19 chart groups**: Inputs, Engine, Tyres, Dynamics, Maps, G-Force, Fuel, Braking, Sectors, Traction, Tele Diff, Ratings, Heat Maps, Timeline, Extended, Consensus, Style, Corners, Theoretical
- A/B lap comparison across every chart group
- **Theoretical tab** — stitches the fastest ~25 m micro-sector from each lap you've driven in this car on this track (Lap A, Lap B, any overlays, and your other saved laps) into the lap you *could* drive, and shows the zones where your best real lap is held back, and by whom. It's an optimistic ceiling: a faster exit carries into the next sector, so the real gain from fixing a weak zone is usually smaller
- **Corners tab** — finds every corner on the lap and, with your PB loaded as lap B, scores each one against the same corner on the PB: brake point, minimum speed, throttle-on point and time gained or lost, and a line naming the three corners that cost the most time
- **Style tab** — classifies the lap as Smooth / Balanced / Aggressive and reads cornering balance (understeer- vs oversteer-leaning) from front vs rear tyre slip
- **3+ lap overlay** — add any number of extra saved laps on top of A/B as dotted lines on the Inputs tab
- **Searchable lap picker** — filter your saved laps by car, track or date
- **Backup All Laps** — zips your laps folder to `~/TRACE/backups/`
- Dual replay — synced and realtime
- Driver ratings radar
- Track-map heatmaps across 9 metrics (speed, throttle, brake, lateral/longitudinal/total G, tyre temp, RPM, steering)
- CSV and HTML chart export
- **Lap card** — one click turns a lap into a shareable 1200x630 PNG with four blocks: headline stats (car, track, lap time, top/average speed, full-throttle share, max lateral G, tyre temperature, driving style), the speed trace cut into sectors with their times, a speed-coloured track map, and the fuel used on the lap

### Race Analyst
- Same chart-group depth as Lap Analyst (17 groups, race-oriented: Race overview + Laps in place of Sectors + Extended, plus Corners and Consistency)
- Race timeline, minimap heatmap, replay with speed control up to 32×
- Per-lap splits (a partial final lap is shown but never counts as best/average), starting grid slot, and pit stops worked out from refuelling — GT7 sends no pit flag or live race position
- Fuel per lap and laps per full tank, measured from the clean laps of the recording
- **Corners tab** — scores *every* lap of the race corner by corner (time gained or lost per corner, as a lap × corner heat-map plus the average per corner). Switch the baseline with the **Corner baseline** button: the best lap of the race, or your saved PB lap for that car and track (`reference_<car>.json`, the file the Dashboard keeps)
- **Consistency tab** — lap-time spread, trend (speeding up or slowing down per lap), a 0–100 consistency score, and the most and least consistent corners over the race. Laps more than 7 % slower than your best (pit laps, spins) are left out of the numbers and shown dimmed. The score is 100 minus the standard deviation of your clean laps as a share of the average lap, scaled so a 2 % standard deviation scores 0
- **Race card** — a shareable 1200x630 PNG built the same way as the lap card: headline stats (race time, best/average lap, consistency, laps, top speed, fuel used), every lap time with the best highlighted, a speed-coloured map of the best lap, and pit-stop laps with fuel burned per lap

### All tools
- **Records** — the *Records* link in the Launcher footer opens a table of your best lap for every car and track, built from your saved laps (incomplete laps and laps from before GT7 update 1.71 are left out unless you tick the box). It reads only the summary at the start of each lap file, so even a big laps folder opens quickly
- Desktop notifications (with a sound where the system supports one) on a new personal best and when a race recording ends automatically. Switch them off with the **`NOTIFICATIONS`** checkbox in the Live Dashboard header
- Dark / light theme toggle in the Launcher footer (applies the next time you open a tool)
- Update check — the Launcher quietly looks for a newer GitHub release on startup and shows a small banner. Turn it off with the **`CHECK FOR UPDATES`** checkbox in the Live Dashboard header (see [Privacy & analytics](#privacy--analytics))

### Tooling
- `gt7telem-add-car` / `gt7telem-add-track` — add a missing car/track ID to the local database from the terminal (also runnable as `python -m gt7telem.add_car` / `python -m gt7telem.add_track`). Additions are saved beside `settings.json` (`car_ids_local.csv` / `course_ids_local.csv`), so they survive upgrades
- Ships with **580+ cars** and **120+ tracks** pre-resolved out of the box; the car database is refreshed every time 10 or more new cars have been added since the last update

### Community Leaderboard (Lap Analyst)
- Submit any recorded lap to a public per-car, per-track leaderboard with one click; live Top-10 panel in the sidebar
- Server-side anti-cheat — physically-impossible times are rejected outright; times beating the record by more than 20%, and suspiciously fast first times for a car/track, are held for manual review
- Ghost lap download — pull any Top-10 lap straight into the A/B compare view as Lap B
- Consensus racing line — overlay a community-average speed/throttle/brake line, bucketed from the current top 10 laps for your car and track
- Optional free account (display name only, no email or password) so a submission ties to something real; entirely skippable — viewing the leaderboard, ghost downloads, and the consensus line all work without one

---

## Install

### Option 1 — pip
```bash
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
Works on Windows, macOS, or Linux with Python 3.10+. On Debian/Ubuntu you may also need Tk: `sudo apt install python3-tk`.
```bash
git clone https://github.com/ransh2014/gt7telemtrace.git
cd gt7telemtrace
pip install -e .
gt7telem
```

### Option 3 — standalone binaries
No Python required — grab a prebuilt Windows `.exe`, Linux binary, or macOS `.app` from **[gt7trace.netlify.app/setup.html](https://gt7trace.netlify.app/setup.html)**. The macOS build is produced automatically on a GitHub-hosted Apple Silicon runner (see [`build-macos.yml`](https://github.com/ransh2014/gt7telemtrace/blob/main/.github/workflows/build-macos.yml)) — on an older Intel Mac, use Option 1 or 2 instead.

### Option 4 — package managers
On Windows:
```powershell
winget install ransh2014.TRACE

# or Scoop
scoop bucket add gt7telem https://github.com/ransh2014/scoop-gt7telem
scoop install gt7telem/gt7telem

# or Chocolatey
choco install gt7telem
```
On macOS (Apple Silicon):
```bash
brew tap ransh2014/gt7telem
brew install --cask gt7telem
```
Package-manager releases go through a review step, so a new version can take a little while to appear there. The binaries above are always the latest build.

---

## Code signing

TRACE's binaries are unsigned on Windows today. If your browser or Windows SmartScreen flags the `.exe` as untrusted, that's expected for now — see the status below for each platform:

- **Windows** — Not yet signed. An application is in with **[SignPath Foundation](https://signpath.org/)**, a free code-signing program for open-source projects, and TRACE is expected to have a trusted signed `.exe` within the next month or two once that's approved.
- **macOS** — `TRACE.app` is **ad-hoc signed** (a local signature with no Apple-issued certificate). This satisfies macOS's baseline "must be signed" check and avoids "app is damaged" errors, but you'll still see an "unidentified developer" warning on first launch — right-click the app and choose **Open** to bypass it. Full notarization requires a paid Apple Developer account, which isn't in place yet.
- **Linux** — The binary is built on Ubuntu 22.04, so it needs glibc 2.35 or newer (Ubuntu 22.04+, Fedora 36+, Debian 12+ and equivalents). The release zip is signed with a dedicated GPG key so you can verify it wasn't tampered with after being built. Import the public key and verify with:
  ```bash
  gpg --import trace-public-key.asc
  gpg --verify gt7telem-linux.zip.asc gt7telem-linux.zip
  ```
  The public key is available in the repo at [`gpg-signing-key/trace-public-key.asc`](https://github.com/ransh2014/gt7telemtrace/blob/main/gpg-signing-key/trace-public-key.asc).

---

## Quick start

1. In GT7, make sure telemetry broadcasting is available (it's on by default while a session/menu is active — no separate settings toggle needed).
2. Launch `gt7telem` (or run the source/binary).
3. Pick **Live Dashboard**, **Lap Analyst**, or **Race Analyst** from the menu.
4. Enter your console's IP in the Dashboard field and hit Enter — it's remembered for next time.
5. Recorded laps save to `~/TRACE/laps`. The one exception is a standalone .exe/binary unzipped into a folder you can write to — that stays portable and keeps a `laps/` folder next to the executable. (Chocolatey/WinGet installs and the macOS `.app` always use `~/TRACE/laps`, so a package upgrade can't take your laps with it.) To move it, edit `LAPS_FOLDER` in `settings.json` — there's no in-app control for it.

Find your console's IP: **Settings → Network → View Connection Status** on your PS4/PS5. Your PC and console need to be on the same local network.

---

## How it works

TRACE talks directly to the console for all core telemetry — no server, no browser, no cloud involved in reading your live data:

- A UDP **heartbeat** (`"C"`) is sent to `<console IP>:33739` roughly once a second, requesting GT7's fullest telemetry packet (the "extended" B + ~ + C fields — motion data, filtered inputs, and energy recovery all in one).
- GT7 streams packets back to your PC on port `33740`.
- Each packet is decrypted with Salsa20 and unpacked into a `Telemetry` snapshot — the protocol details this library relies on were reverse-engineered by [Bornhall](https://github.com/Bornhall/gt7telemetry) (see [Credits](#credits)).
- Because TRACE always requests the extended packet, you get motion/sway/heave/surge and filtered-input data automatically — there's no separate "heartbeat type" setting to configure.
- Settings (last-used IP, sample rate, known-good IPs, and the analytics, update-check, notification, voice-alert and top-lap-ghost switches) persist to a `settings.json`, so there's nothing to reconfigure between sessions. It lives in `~/.gt7telem/`, which survives `pip install --upgrade` and package-manager upgrades — except for a standalone .exe/binary unzipped into a folder you can write to, which keeps it next to the executable so it stays portable.

The exceptions are the optional leaderboard and the Dashboard's top-lap ghost (the optional Prometheus metrics endpoint is separate again: it is a local server, not something TRACE sends anywhere): submitting, browsing the Top-10, downloading a ghost lap, or loading the consensus line talks to a Supabase backend over HTTPS, and so does the Dashboard's one-off top-lap fetch (see below). Nothing about your live session is ever sent unless you press "Submit to Leaderboard."

---

## Privacy & analytics

TRACE sends a small anonymous usage ping (which tool launched, TRACE version, OS, a timestamp — five fields, nothing else) each time you open Live Dashboard, Lap Analyst, or Race Analyst. It's **on by default** and helps decide where to spend limited development time; it never includes telemetry content, lap data, PSN name, or IP.

Turn it off any time: **Live Dashboard → `SHARE USAGE DATA` checkbox** (in the header's Preferences row) — the setting applies to all three tools since they share the same `settings.json`.

The Launcher also checks GitHub's public releases API for a newer version on startup, so it can show an update banner. That request goes to GitHub, not to this project's database, and carries no version, ID or usage data (just a fixed `TRACE-updater` label) — though GitHub can see your IP, as with any web request. It's **on by default**; untick **Live Dashboard → `CHECK FOR UPDATES`** (right beside `SHARE USAGE DATA`) to turn it off from then on — no need to edit `settings.json`.

The Live Dashboard's **top-lap ghost** makes one read-only request to the leaderboard database once your car and track have both been known and unchanged for a few seconds in a run (and once more for each new car/track you switch to): it asks for the single fastest public lap for that exact car and track so it can show your live gap. The request carries only the car and track names — no account, PSN name, lap or telemetry data — and stays silent if you're offline or no lap exists. Untick **`TOP-LAP GHOST`** and the Dashboard makes no leaderboard requests at all.

The optional **metrics export** (off by default) isn't data sent anywhere: it only serves live gauges from a small web server on your own PC (`127.0.0.1`, port 9109). Ticking **`ALLOW REMOTE`** makes that server reachable by other devices on your local network, with no password, so only do it on a network you trust.

Submitting a lap to the leaderboard is a separate, always-explicit opt-in (a button you press). A signed-in submission also carries your anonymous account ID (the database needs it to attach the lap to an account; it isn't shown publicly). The optional free account only ever stores a display name and that anonymous ID — no email, no password. If a submission is rejected because your login has expired, TRACE sends your stored refresh token to the same database to renew it and retries once — only during a submission you started, never in the background.

Full disclosure of exactly what's sent for both: **[gt7trace.netlify.app/privacy.html](https://gt7trace.netlify.app/privacy.html)**

---

## Public data API

```python
import gt7telem

gt7telem.set_ip("192.168.1.X")
gt7telem.wait_for_connection()

snap = gt7telem.get_snapshot()
print(gt7telem.get_car_name(snap.get("car_id")))
print(gt7telem.get_diagnostics())   # dict of heartbeat / packet / decrypt counters
print(gt7telem.get_last_error())    # one-line explanation when not connected, else None
```

See `gt7telem.__all__` for the full list of exported functions (`get_snapshot`, `set_car`, `set_track`, `get_diagnostics`, `get_car_name`, `get_track_name`, `all_track_names`, settings persistence, and more). The GUI apps (`dashboard`, `lap_analyst`, `race_analyst`) are available as submodules for anyone building on top of TRACE directly:

```python
from gt7telem import dashboard
dashboard.App().mainloop()
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Can't send heartbeat to `<ip>`:33739" | Wrong console IP, or PC/console aren't on the same network |
| Heartbeats sending but nothing comes back | Firewall is blocking inbound UDP on port `33740` — allow it for Python/the TRACE executable |
| Packets arriving but not decrypting | Something else is bound to port 33740, an unexpected console/game version, or a mid-stream corrupt packet (usually self-resolves) |
| A module named `gt7telem` already exists | You (or another package) installed an unrelated `gt7telem`; run `pip show gt7telem` to check before installing |
| Car/track shows as a blank name | The ID isn't in the local database yet — run `gt7telem-add-car` / `gt7telem-add-track`, or wait for the next scheduled DB refresh |

Live connection diagnostics (heartbeat count, packet loss, last error) are always visible in the Dashboard, and available programmatically via `gt7telem.get_diagnostics()`.

---

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](https://github.com/ransh2014/gt7telemtrace/blob/main/CONTRIBUTING.md) for the quick version (dev setup, where the GUI code lives, and how the car/track databases get refreshed).

---

## Credits

TRACE stands on the shoulders of people who did the hard, unglamorous work this project depends on:

- **[Bornhall](https://github.com/Bornhall/gt7telemetry)** — reverse-engineered GT7's UDP telemetry protocol, including the Salsa20 decryption key and full packet byte structure. Every telemetry value TRACE reads comes from this work.
- **[ddm999](https://github.com/ddm999/gt7info)** — maintains [gt7info](https://ddm999.github.io/gt7info/), the community car and track database that resolves every car/track ID to a real name shown anywhere in TRACE.
- **[MacManley](https://github.com/MacManley/gt7-udp)** and **[Nenkai](https://github.com/Nenkai/PDTools)** — the community packet documentation `udp.py` cross-references for the per-version packet lengths (A/B/~/C) and the extended-field byte offsets.

Full writeup and credits: **[gt7trace.netlify.app/about.html](https://gt7trace.netlify.app/about.html)**.

## License

MIT — see [LICENSE](https://github.com/ransh2014/gt7telemtrace/blob/main/LICENSE).

## Support

TRACE is free and always will be. If it's improved your GT7 sessions, a coffee is appreciated: **[ko-fi.com/ransh](https://ko-fi.com/ransh)**.
