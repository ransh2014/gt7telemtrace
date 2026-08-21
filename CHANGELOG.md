# Changelog

All notable changes to TRACE are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
