# Changelog

All notable changes to TRACE are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
