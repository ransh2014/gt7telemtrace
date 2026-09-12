# Changelog

All notable changes to TRACE are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.9] - 2026-09-12
- Live Dashboard: Auto-Detect stopped reconnecting when the console it found
  was already the IP in the box -- a regression from 0.3.8's IP-box fix
  below, caught in a follow-up re-review of that release. Auto-Detect is a
  manual action, so it now always (re)connects, the same as pressing Enter.

## [0.3.8] - 2026-09-12
Final audit pass -- every module read end to end; the race-event and
Salsa20 bugs were reproduced before fixing and are now pinned by tests.

- Race detection: after the chequered flag, a car still above 80 km/h on the
  cool-down lap re-triggered race start + race end every 2 s until the
  results screen, so the Dashboard started and saved a near-empty race each
  time. Neither start condition can fire once `lap_number > total_laps`.
- Live delta: `track_position` now restarts in udp.py whenever GT7's lap
  counter changes. It used to reset only from Record Lap, so without a lap
  recording running the DELTA readout was wrong from lap 2 onward. Race
  recordings' `track_position` now restarts per lap too.
- Leaderboard: incomplete laps (stopped mid-lap, or saved on close) can no
  longer be submitted -- their time only covered part of a lap. New
  `leaderboard.lap_submission_error()`.
- Recording: a failed save (disk full, folder not writable) no longer loses
  the recording or silently stops recording for the rest of the session.
  Saves are logged, written via a temp file, and fall back to
  `~/TRACE/unsaved/`; if even that fails the lap/race stays in memory for
  Export Session. Closing with a recording that couldn't be saved now asks
  before discarding it. The display and recording timers always reschedule
  after an error.
- Standalone builds: settings and laps stay next to the executable only in a
  folder the user owns. Chocolatey/WinGet installs, the macOS `.app`, and
  read-only folders now use `~/.gt7telem` and `~/TRACE/laps` like pip
  installs -- under Chocolatey, settings silently never saved and lap saves
  failed, and a package upgrade replaces that folder. Existing portable
  installs (settings.json already beside the exe) are unchanged.
- Auto-Detect now also finds PS4s (UDP 987, protocol 00020020); it only ever
  sent the PS5 query (9302). Non-console replies are ignored.
- Replay (Lap and Race Analyst): 1x now plays in real time at any recording
  rate, and 0.25x/0.5x actually slow down (they were identical to 1x). A
  quick pause/play no longer doubles playback speed.
- Dashboard IP box: losing focus no longer reconnects when the IP hasn't
  changed (it dropped the live connection and logged "Connection dropped");
  Enter still forces a retry. New `udp.get_ip()`.
- Changing the console IP resets the connection diagnostics, so a wrong new
  IP gets a real explanation instead of "unknown error".
- Onboarding: pressing Enter while an account is being created no longer
  creates extra accounts.
- Session summary: the "Fuel %" column was litres (kWh for EVs); it's now
  "Fuel Used" with the unit. Session JSON key `fuel_used_pct` is now
  `fuel_used` (+ `fuel_unit`).
- udp.py's pure-Python Salsa20 fallback (used only without pycryptodome) was
  wrong -- 40 rounds instead of 20, and only the first 64 bytes decrypted.
- Docs: README's "up to 3" IPs and `get_car_name(snap)` example corrected;
  the source-zip README no longer claims an in-app laps-folder control or a
  Settings screen; data-location notes updated for the standalone change.

## [0.3.7] - 2026-09-10
- Live Dashboard: the remembered-good-IP list (the dropdown next to the
  console IP field) is no longer capped at 3 -- every console you've ever
  successfully connected to stays in it now.
- Live Dashboard: added an "Auto-Detect" button next to the IP field. It
  broadcasts a PS4/PS5 device-discovery query on the local network (UDP
  port 9302, the same protocol Sony's own apps use) and fills in whichever
  console answers first -- entirely optional, manual entry still works
  exactly as before. New `gt7telem.discover_ps_ip()` in the public API.

## [0.3.6] - 2026-09-09
- UDP parser: `gear_ratios[0]` was actually `TransmissionTopSpeed` (offset
  0x100), not gear 1's ratio -- confirmed against Nenkai/PDTools'
  SimulatorPacket.cs, which every other field offset in this parser already
  matches byte-for-byte. Every gear ratio read since was off by one, and the
  true 8th-gear slot (rare, only populated on 8+-gear cars) was silently
  folded into the array under the wrong label. Real gear ratios now come from
  0x104 (8 slots); the top-speed value is exposed separately as
  `transmission_top_speed`. Old saved lap/race JSON keeps its previous
  (off-by-one) `gear_ratios` values as-is -- nothing re-derives them from a
  loaded file.

## [0.3.5] - 2026-09-07
Second audit pass -- everything below came out of reading the GUI/analysis
modules and probing the live backend, neither of which the first pass covered.

- Live Dashboard: added a `WM_DELETE_WINDOW` handler. It was the only one of
  the three windows without one, and an in-progress recording lives purely in
  memory until the lap or race ends -- so closing mid-race silently discarded
  everything. Closing now offers save / discard / cancel, and stops the
  Prometheus exporter (which was only ever stopped from its own toggle).
- Recording: `gear_ratios` is a per-car constant that was being written onto
  every single sample -- 9.9% of a measured race file, and never read back off
  a sample by anything. It is now stored once at file level. Existing
  recordings in the old shape still load unchanged.
- Recording: a race is buffered entirely in memory until it ends, at ~1.6 KB
  per sample -- about 328 MB for an hour at 60 Hz, and json.dump doubles that
  on save. It now warns once at 150k samples and hard-stops (saving) at 400k
  rather than running the machine out of memory.
- `sanitize()`: input that reduced to nothing ("...", "   ") returned "" and
  dumped laps in the laps root instead of a track folder; Windows reserved
  device names (con/nul/prn/aux/com1..lpt9) made `mkdir` raise; and there was
  no length cap, so a very long name could push the path past MAX_PATH. All
  three are handled, and the function finally has tests.
- All file I/O in the GUI modules now passes an explicit `encoding="utf-8"`
  (11 call sites) instead of depending on the platform's locale codepage.
- Race Analyst: exported HTML now declares `<meta charset="utf-8">`. It was
  written as UTF-8 but had no declaration, so a browser opening it over
  file:// fell back to the locale encoding and mangled the em dash.
- Every window title now reads "TRACE <version> - <tool>". The Dashboard
  still said "GT7 Telemetry v2" (a name predating the TRACE rename), the two
  analysts had no TRACE branding, and no window showed the version at all --
  despite bug reports asking for it.
- `dashboard.py` reads `LAPS_FOLDER` from config at use time instead of
  binding it once at import.
- Tests: 19 -> 80. New coverage for `sanitize()`, the at-rest token
  encryption from 0.2.9, `leaderboard._eq`/`_compact_samples`, and the
  metrics server (including a regression guard on the prometheus_client
  >=0.20 requirement).
- Docs: corrected a claim that the laps folder is configurable from the
  Dashboard -- there is no such control; it is a `settings.json` edit.
- Minor: dropped a no-op `np.interp(pa_s, pa_s, ...)` in the Race Analyst's
  telemetry diff, and a dead `.btn-gold` CSS rule on the site.


## [0.3.4] - 2026-09-07
- Removed the duplicate `choco-publish.yml` workflow -- it fired on the same
  version tag as `choco-update.yml` and pushed the same package version, so
  one of the two always failed on Chocolatey's duplicate-version rejection.
  `choco-update.yml` (the one with the corrected checksum step and the
  `choco_version` override) is now the only Chocolatey publisher.
- Raised the `prometheus_client` floor from `>=0.19` to `>=0.20`.
  `metrics_server.start()` unpacks `start_http_server()`'s `(server, thread)`
  return, which only exists from 0.20.0 onward; on 0.19.x it returns `None`
  and the unpack raised a `TypeError` that the surrounding `except OSError`
  didn't catch, taking the Dashboard down when metrics export was enabled.
- `add_car.py` / `add_track.py` no longer print maintainer-only build
  instructions (a `rebuild_all.ps1` that isn't in the repo, and a note about
  asking Claude to rebuild the Linux zip) to end users. They now point at the
  issue tracker and warn that a local CSV edit doesn't survive an upgrade.
- Added `gt7telem-add-car` / `gt7telem-add-track` console scripts, so the two
  CLI helpers are actually reachable from a pip install instead of only by
  locating them inside site-packages.
- Added `tools/build_source_zip.py`, which builds `gt7telem-source.zip` from
  the live package source and refuses to run if `pyproject.toml` and
  `__init__.py` disagree on the version. The source download had been
  assembled by hand and was shipping 0.3.2 after 0.3.3 released.
- Docs: corrected the Lap Analyst chart-group count (16, not 15 -- the
  Consensus tab was never counted), the settings/laps locations for
  pip and from-source installs (`~/.gt7telem/` and `~/TRACE/laps`, not
  "next to the app"), and stale `gt7telem.py` / `gt7udp.py` / `car_db.py` /
  `runtime_config.py` filenames left in module docstrings.
- Credits: added MacManley's gt7-udp and Nenkai's PDTools, which `udp.py`
  cross-references for packet lengths and extended-field offsets but which
  were credited only in source comments, never in the README or on the site.
- Added Python 3.14 to the CI matrix and the PyPI classifiers.
- `udp.py`'s receive socket no longer sets `SO_REUSEADDR`. On UDP that let a
  second copy of TRACE share (Linux/macOS) or steal (Windows) port 33740, so
  two instances silently split the packet stream and the "another copy is
  already running" `EADDRINUSE` message almost never fired. A failed bind is
  now retried every 5s instead of killing the receive path for the life of
  the process, so freeing the port recovers without restarting TRACE.
- `udp.py`'s diagnostics no longer use bare `print()`. The shipped builds are
  PyInstaller `--windowed`, where `sys.stdout` isn't a real stream; the new
  `_log()` can't raise and retains the last 50 lines, exposed as
  `get_log_lines()`.
- `auth.set_display_name()` now asks PostgREST for the updated row
  (`Prefer: return=representation`) instead of treating any 2xx as success --
  a PATCH matching zero rows also returns 204, so the honest "name didn't
  sync" message added in 0.2.2 could report success on a silent no-op.
- `leaderboard.py` quotes `eq.` filter values, so a car or track name
  containing a comma can no longer truncate the query and match the wrong
  rows.
- Removed the README's "account creation and lap submission are currently
  failing server-side" notice -- the upstream Supabase auth issue behind it
  is resolved.

## [0.3.3] - 2026-09-05
Version bump only -- no code changes. Re-tagged to re-trigger the release
build and publish workflows.

## [0.3.2] - 2026-08-31
- Fixed leaderboard lap submissions silently failing once the Supabase
  session's access token expired (~1h). `_submit_leaderboard` now retries
  once with a refreshed access token (via the previously-unused
  `auth.refresh_session`) whenever a submit comes back with a "server"
  (RLS/auth) rejection.

## [0.3.1] - 2026-08-30
- Live Dashboard: new `ALLOW REMOTE` toggle for the Prometheus metrics
  server. The metrics endpoint binds `127.0.0.1` by default; turning this
  on rebinds it to `0.0.0.0` so a Grafana/Prometheus instance elsewhere on
  the LAN can scrape it. Off by default, and the log panel says plainly
  that anyone on the network can then read live telemetry.

  (There is no 0.3.0 -- 0.2.9 was followed directly by 0.3.1.)

## [0.2.9] - 2026-08-30
- Fixed a Unicode crash and tightened incident / pit-flag detection in
  `udp.py`.
- Supabase session tokens (`SUPABASE_ACCESS_TOKEN` / `REFRESH_TOKEN` /
  `USER_ID`) are now encrypted at rest with AES-GCM under a 32-byte key
  kept in a separate `.settings.key` file, so sharing a `settings.json`
  in a bug report or backup no longer hands over a usable session.
- Hardened UDP and metrics-server binding, including a cached
  `_expected_ps4_ip()` so the receive loop drops datagrams from hosts
  other than the configured console without a DNS lookup per packet.
- Pinned all runtime dependencies to compatible ranges (`numpy>=1.26,<3`,
  `pandas>=2.0,<3`, `matplotlib>=3.7,<4`, `pycryptodome>=3.19,<4`,
  `prometheus_client>=0.19,<1`) instead of leaving them unbounded.

## [0.2.8] - 2026-08-29
- Car database refresh from the ddm999/gt7info community database: 4 new
  cars added (Caterham Seven Superlight R500 '08, Hyundai IONIQ 6N '25,
  Toyota Mark II Tourer V '97, Toyota Chaser Tourer V '97), bringing
  `car_ids.csv` to 584 entries.
- Corrected 7 existing car names in the same refresh: restored the accent in
  "Chevelle SS 454 Sport Coupe" and "R8 Coupe V10 plus", added the missing
  space before the year on the four 2023-25 prototypes (M Hybrid V8 '25,
  499P '23, 9X8 '25, 963 '24), and expanded "911 Safety Car" to
  "911 Turbo S Safety Car (992)".

## [0.2.7] - 2026-08-28
- Lap Analyst: CSV export button on the Replay tab (distance_m, speed_kmh,
  throttle, brake, rpm, gear, steering).
- Lap Analyst: micro-sector heatmap toggle on the Replay tab, recolors the
  race-line by 25m-sector time-delta vs. a loaded reference lap.
- Lap Analyst: "Save Boundary" button estimates a track's left/right edge
  from the loaded lap's GPS trace and renders it under the race-line map;
  cached per-track and auto-loaded on future laps for that track.
- Live Dashboard: optional Prometheus metrics export (off by default) --
  exposes speed/rpm/throttle/brake/fuel/lap-time gauges for Grafana or any
  Prometheus-compatible scraper.

## [0.2.6] - 2026-08-27
- Added a Log Out link to the tool menu (launcher.py) for signed-in users
  -- clears the local Supabase session without deleting the account.

## [0.2.5] - 2026-08-27
Docs-only release:

- Added `cacheSeconds=300` to all README shields.io badges (PyPI version,
  Python versions, downloads, license, last commit) so they refresh within
  5 minutes instead of being stuck on stale cached values for up to an hour.

## [0.2.4] - 2026-08-26
Small UX polish (maintenance mode -- no new features):

- `leaderboard.submit_lap()` now distinguishes a genuine network failure
  from a server-side rejection (e.g. the ongoing Supabase auth bug)
  instead of collapsing both into one generic exception handler. The
  Lap Analyst's submit-failure message no longer tells you to "check
  your internet connection" when the real problem is server-side --
  it says so, so you're not chasing the wrong fix.

## [0.2.3] - 2026-08-26
Re-tag of 0.2.2 -- the v0.2.2 tag push never triggered CI/build/publish
for reasons unclear (GitHub Actions just didn't pick it up), so bumping
and re-tagging clean rather than chasing it further. No code changes
beyond the version bump.

## [0.2.2] - 2026-08-26
Bug fix (maintenance mode -- no new features):

- `launcher.py`'s onboarding screen and `lap_analyst.py`'s inline
  "Create a Free Account" dialog both ignored the return value of
  `auth.set_display_name()`, so when the display-name sync to Supabase
  failed silently -- as it currently does for every anonymous account,
  due to an unresolved upstream Supabase JWT/RLS auth bug, not anything
  on our end -- the app still showed the flow as fully successful.
  Both now check the result and tell you honestly when the name didn't
  sync, instead of a false "you're all set." The account/session itself
  still works fine either way; this only affected the display-name field.
- Fixed `__version__` in `gt7telem/__init__.py` being stuck on "0.2.0"
  while `pyproject.toml` had already moved on (cosmetic; `pip show`
  always reported the right version).

## [0.2.1] - 2026-08-25
Optional accounts + leaderboard identity (the last planned feature --
TRACE moves to maintenance mode after this: bug fixes and car/track
database updates only, no further new features):

- **Optional free account** via Supabase anonymous auth (new `auth.py`
  module) -- just a display name, no email or password ever. Submitting
  a lap to the leaderboard now requires a real (possibly anonymous)
  signed-in session instead of trusting a free-typed name; the `laps`
  table's INSERT policy enforces `auth.uid() = user_id` server-side
- New `profiles` table (auto-created on signup via trigger); `laps` and
  `flagged_submissions` both gained a `user_id` column so a submission,
  including one held for anti-cheat review, traces back to a real
  identity -- verified end-to-end
- Closed an abuse hole found in the process: the old anon-key-only INSERT
  policy on `laps` had let something spam six garbage rows into the
  public leaderboard table before this release: the new auth-required
  policy blocks that outright
- `launcher.py` rebuilt: larger resizable centered window, a one-time
  onboarding screen (create account or skip -- skipping still allows
  viewing the leaderboard, ghost downloads, and the consensus line, only
  submitting needs an account), and a restyled tool menu with a
  persistent "Sign In / Create Account" link if you skipped
- `lap_analyst.py`'s Submit-to-Leaderboard flow now prompts inline to
  create an account if you haven't, instead of failing silently
- Fixed `config.py`'s `_base_dir()`: for pip/source installs it used to
  resolve inside site-packages -- not reliably writable, and wiped on
  every `pip install --upgrade`, which would've silently broken
  onboarding persistence specifically. Now uses `~/.gt7telem`, verified
  from a real installed wheel in a clean venv, not just source. Laps
  default to the more visible `~/TRACE/laps` for non-frozen installs
- Fixed a stale `__version__ = "0.1.5"` left in `__init__.py` since
  0.1.6 -- now synced with the package version again
- Added a `pg_cron` job cleaning up `flagged_submissions` older than 30
  days, entirely server-side

## [0.2.0] - 2026-08-24
Supabase-backed community features, Phases 1-4 (the 0.1.5-0.1.10 releases
condensed below into one clean version -- those interim numbers existed
only to work around a PyPI upload snag and are otherwise identical in
substance to what's listed here):

- **Anonymous usage analytics** (Phase 1): a launch ping (`event`, `tool`,
  `version`, `os`, `created_at` -- exactly those 5 fields) sent once per
  tool launch via `analytics.py`. On by default, with a `SHARE USAGE DATA`
  checkbox in the Live Dashboard header to opt out
- **Global lap leaderboard** (Phase 2): a new `leaderboard.py` module
  backs a "Submit to Leaderboard" button and live Top-10 panel in Lap
  Analyst. Identity is a self-reported PSN name, remembered locally.
  Server-side anti-cheat rejects physically-impossible times outright and
  holds times beating the current record by more than 20% for manual
  review -- both verified end-to-end. Full per-sample telemetry is stored
  per leaderboard lap, not just the lap time
- **Crowdsourced car/track submissions**: an unrecognized live `car_id`,
  or a typed TRACK name not in `course_ids.csv`, is submitted to the
  community inbox automatically instead of staying unlabeled
- **Ghost lap download** (Phase 3): a ⬇ button on each Top-10 row
  downloads that lap's stored samples and loads it as Lap B, through the
  same `Replay.load_b()` / A-vs-B compare path as a local file
- **Consensus racing-line comparison** (Phase 4): a "Load Community Line"
  button and new Consensus chart tab overlay your lap against the
  community average speed/throttle/brake, bucketed by 10m track-position
  entirely server-side via a Postgres function
- Fixed `launcher.py` so it runs directly (F5, double-click, `python
  launcher.py`) without a relative-import error
- Switched `pyproject.toml`'s `license` field to the SPDX expression
  format, fixing a PyPI metadata-validation rejection
- Added a `privacy.html` page disclosing what analytics and leaderboard
  submissions collect and how to turn analytics off, linked from every
  page's nav/footer

## [0.1.4] - 2026-08-22
- Added real screenshots (Live Dashboard, Lap Analyst, Race Analyst) to the
  README, replacing the placeholder comment
- Added a real standalone macOS app (`TRACE.app`), built automatically on a
  GitHub-hosted Apple Silicon runner via `build-macos.yml` -- no physical
  Mac needed on this end. Replaces the old "email a Mac user and ask them
  to build one" flow. Attaches to the GitHub Release on every `vX.Y.Z` tag.
- Added a test suite (`tests/`) covering car/track DB lookups, settings
  persistence, and a full Salsa20 decrypt+parse round-trip against a
  synthetic packet
- Added CI (GitHub Actions): tests run on Python 3.10–3.13 on every push/PR,
  plus `ruff` linting
- Added a trusted-publishing workflow (`publish.yml`) that builds and
  uploads to PyPI automatically on a `vX.Y.Z` tag push -- no more manual
  local `twine upload`
- Added type hints to the public API (`cars`, `tracks`, `udp`) and a
  `py.typed` marker so IDEs get proper autocomplete for `import gt7telem`
- Added issue templates, a PR template, and `CODEOWNERS`

## [0.1.3] - 2026-08-21
- Synced `__version__` in `src/gt7telem/__init__.py` with `pyproject.toml`
  (had drifted to 0.1.2 while the package version moved to 0.1.3)
- Added PyPI classifiers, keywords, and author metadata to `pyproject.toml`
- Added `CONTRIBUTING.md` and this changelog

## [0.1.2] - 2026-08-20
- Documented the car database refresh cadence (refreshed every time 10+ new
  cars have been added since the last update)
- Credited Bornhall and ddm999 by name in the README

## [0.1.1] - 2026-08-20
- Added `Repository` link to project URLs in `pyproject.toml`

## [0.1.0] - 2026-08-20
- Initial PyPI release: pip-installable TRACE suite (`gt7tracetelem` on
  PyPI, `gt7telem` module)
- Restructured into a proper package (`src/gt7telem/`) with a unified
  `gt7telem` CLI launcher for Live Dashboard, Lap Analyst, and Race Analyst
- Added `add_car.py` tool
- Fleshed out README with full feature list, install options, quick start,
  public API, and credits
