TRACE — GT7 Telemetry Suite (Python source)
=============================================

This is the plain Python source for TRACE — no pip install of the package
itself needed, just its dependencies.

REQUIREMENTS
------------
Python 3.10 or newer.

SETUP
-----
1. Install dependencies:

       pip install -r requirements.txt

2. Run TRACE from THIS folder (the one containing requirements.txt,
   launcher.py, and the gt7telem/ folder):

       python launcher.py

   This opens the TRACE menu — pick Live Dashboard, Lap Analyst, or Race
   Analyst. (The Track Map isn't a separate menu entry; it's a live panel
   inside the Live Dashboard.)

WHAT'S INSIDE
-------------
launcher.py       Entry point you actually run — a thin wrapper around
                  gt7telem/launcher.py, kept at the top level so
                  `python launcher.py` works with no extra setup.

gt7telem/         The TRACE package itself (same source that ships on
                  PyPI as `gt7tracetelem`). Its files use relative
                  imports between each other, which is why launcher.py
                  is a wrapper rather than living inside this folder.

  dashboard.py      Live telemetry dashboard (includes the Track Map panel
                    and the RECORD_RATE_OPTIONS list, if you want to add
                    your own recording sample rates)
  lap_analyst.py    Lap analysis — 20 chart groups, A/B compare, replay,
                    CSV export, micro-sector heatmap, track boundary
                    overlay, leaderboard/ghost/consensus panel
  race_analyst.py   Race analysis — 17 chart groups, race-oriented (incl. per-lap
                    Corners and a Consistency report), race card PNG
  udp.py            UDP capture, Salsa20 decrypt, packet parsing
  config.py         Settings persistence (IP, laps folder, sample rate,
                    analytics opt-out, update-check, notification, voice-alert and
                    top-lap-ghost switches, Prometheus metrics and web UI opt-ins and their remote-access opt-ins)
  auth.py           Optional free account — Supabase anonymous sign-in,
                    display name only, no email or password
  leaderboard.py    Global lap leaderboard, ghost-lap download, and
                    consensus racing-line client
  analytics.py      Anonymous usage ping (on by default) — see
                    gt7trace.netlify.app/privacy.html for exactly what it
                    sends. Turn off with the SHARE USAGE DATA checkbox
                    in the Live Dashboard.
  metrics_server.py Optional Prometheus metrics export (off by default) —
                    exposes speed/rpm/throttle/brake/fuel/lap-time gauges
                    for Grafana or any Prometheus-compatible scraper.
                    Binds to localhost only unless you tick "ALLOW
                    REMOTE (METRICS)" in the Live Dashboard header.
  webui_server.py   Optional LAN web dashboard (off by default) -- serves a live,
                    read-only gauges page to phones/tablets/PCs on your network
                    (HTTP + WebSocket on one port, default 8080). Localhost only
                    unless you tick "ALLOW REMOTE (WEB UI)" next to WEB UI in the Live
                    Dashboard; other devices then need a 6-digit code (or the
                    Dashboard's QR). Needs the `websockets` package. Nothing
                    leaves your LAN.
  webui_auth.py     The WEB UI passcode: 6-digit code, one-time QR login link,
                    lockout after wrong tries, remembered devices (devices.json
                    keeps only hashes) and LOG OUT ALL DEVICES
  webui_login.py    The small "enter code" page shown before the passcode
  qr.py             Tiny built-in QR code encoder for the WEB UI login link
  webui_page.py     The web dashboard's page (HTML/CSS/JS in one string, no CDN)
  webui_fonts.py    The website's fonts (Anton, Sora, Manrope, JetBrains Mono;
                    SIL OFL) embedded so the web dashboard works offline
  corners.py        Corner detection and per-corner analysis (the Corners chart
                    group in Lap Analyst)
  consistency.py    Lap-time spread, trend and 0-100 consistency score (Race
                    Analyst Consistency tab)
  share_card.py     Shared 1200x630 PNG renderer behind the lap card and race card
  labels.py         Shared UI wording (titles, buttons, preference names) used by
                    every window; the Android app's strings are generated from it.
  updates.py        Launcher update check (GitHub latest release, cached, silent on error).
  dyno.py           Dyno & Tune: power/torque curve, gearbox, shift points and tuning
                    hints rebuilt from one lap (Dyno & Tune chart group in Lap Analyst)
  theoretical.py    Theoretical best lap: stitches the fastest micro-sectors of
                    your laps (Theoretical chart group in Lap Analyst)
  ghost.py          Live gap to the leaderboard's fastest lap in the Live Dashboard
                    (one read-only request per car and track; TOP-LAP GHOST switch)
  records.py        Records table (best lap per car and track from your saved
                    laps) -- the Records link on the launcher menu
  keepawake.py      Keeps the PC from idle-sleeping/hibernating (Windows, macOS,
                    Linux) while the Dashboard is open (KEEP AWAKE switch), so
                    races don't end up incomplete; lets go when it closes
  lapclean.py       Junk-lap guards (too short / parked / double save) and the
                    duplicate-lap finder (extras move to _duplicates, never deleted)
  notify.py         Desktop toast + sound alerts (new PB, race end); switch it
                    off with NOTIFICATIONS in the Live Dashboard header
  trackdetect.py    Track auto-detect: learns each layout's start-line position +
                    heading (and lap length) and suggests the track for you to confirm
  theme.py          The website's colours and fonts (Anton, Sora, Manrope,
                    JetBrains Mono) for the desktop windows; the font files
                    themselves (and their SIL OFL licence, OFL.txt) are in gt7telem/fonts/
  uiscroll.py       Scrolling for the Lap and Race Analyst windows: side panel,
                    chart area (both ways) and the chart tab row
  voice.py          Spoken fuel / tyre alerts in the Live Dashboard via the OS
                    voice (Windows SAPI, macOS say, Linux spd-say/espeak);
                    falls back to the notify.py sound. Mute with VOICE ALERTS.
  coach.py          Corner-by-corner driving coach: one plain-English tip per costly
                    corner vs your reference lap, spoken before those corners on the
                    next lap through voice.py (VOICE ALERTS switch)
  setup_advisor.py  Understeer / oversteer / wheelspin / lock-up / bottoming-out from one
                    lap, with setup fixes to try, and an A/B check of a setup change
  engineer.py       Race engineer: pit window, undercut / overcut maths, live "box" calls
                    and a simulated finishing position
  replay3d.py       3D lap replay as one offline HTML file (speed-coloured line, ghost,
                    synced inputs) plus a GIF export
  driver_dna.py     Driver DNA: five style traits, history over weeks, share-code compare
  planner.py        Practice planner: the corners worth drilling, a target for each,
                    progress between sessions
  validate.py       Lap validation: teleports, shortcuts, impossible data, wall hits
  dyno_compare.py   Car-vs-car power overlay and final-drive advice
  groups.py         Friends groups, weekly challenges and sector boards, shared as
                    pasteable codes (no server)
  overlay_page.py   The transparent OBS overlay served at /overlay by the web UI
  cli.py            gt7telem-tools: all of the above from the command line
  cars.py           Car ID -> name lookup (car_ids.csv)
  tracks.py         Track ID -> name lookup (course_ids.csv), plus track
                    boundary extraction from a lap's GPS trace
  add_car.py        CLI tool: add a missing car. Saved to car_ids_local.csv
                    next to settings.json, so it survives upgrades.
                    Run it as:  python -m gt7telem.add_car
  add_track.py      CLI tool: add a missing track. Saved to
                    course_ids_local.csv next to settings.json.
                    Run it as:  python -m gt7telem.add_track

WHERE YOUR FILES GO
-------------------
Running from this source download (not the standalone .exe/.app):

  Settings:  ~/.gt7telem/settings.json
  Laps:      ~/TRACE/laps

(`~` is your home folder — C:\Users\<you> on Windows, /home/<you> or
/Users/<you> elsewhere.) To move the laps folder, edit LAPS_FOLDER in
settings.json -- there's no in-app control for it. A standalone .exe/binary
unzipped into a folder you can write to is portable instead and keeps both
next to the executable.

PREFER `python -m gt7telem.launcher` INSTEAD?
----------------------------------------------
That still works too, from this same top-level folder — both invocations
end up calling the same gt7telem.launcher.main(). Use whichever you like.

MORE INFO
---------
Site:    https://gt7trace.netlify.app
GitHub:  https://github.com/ransh2014/gt7telemtrace
PyPI:    https://pypi.org/project/gt7tracetelem/

TRACE is free, open source (MIT), and not affiliated with Polyphony
Digital or Sony Interactive Entertainment.

Linux: if Tk is missing, install it first (Debian/Ubuntu: sudo apt install python3-tk).
Linux: for spoken Dashboard alerts install a speech tool (sudo apt install speech-dispatcher
or espeak-ng); without one TRACE plays the alert sound instead. Windows and macOS need nothing.
