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
  lap_analyst.py    Lap analysis — 16 chart groups, A/B compare, replay,
                    CSV export, micro-sector heatmap, track boundary
                    overlay, leaderboard/ghost/consensus panel
  race_analyst.py   Race analysis — 15 chart groups, race-oriented
  udp.py            UDP capture, Salsa20 decrypt, packet parsing
  config.py         Settings persistence (IP, laps folder, sample rate,
                    analytics opt-out, Prometheus metrics opt-in and
                    remote-access opt-in)
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
                    REMOTE" in the Live Dashboard header.
  cars.py           Car ID -> name lookup (car_ids.csv)
  tracks.py         Track ID -> name lookup (course_ids.csv), plus track
                    boundary extraction from a lap's GPS trace
  add_car.py        CLI tool: add a missing car to car_ids.csv.
                    Run it as:  python -m gt7telem.add_car
  add_track.py      CLI tool: add a missing track to course_ids.csv.
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
