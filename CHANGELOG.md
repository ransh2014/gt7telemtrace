# Changelog

All notable changes to TRACE are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
