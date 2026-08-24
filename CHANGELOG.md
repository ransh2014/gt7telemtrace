# Changelog

All notable changes to TRACE are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.5] - 2026-08-24
- Added anonymous usage analytics (Phase 1 of a Supabase-backed feature set):
  a launch ping (`event`, `tool`, `version`, `os`, `created_at` -- exactly
  those 5 fields, nothing else) sent once per tool launch via a new
  `analytics.py` module. **On by default**, with a `SHARE USAGE DATA`
  checkbox in the Live Dashboard header (next to `DEBUG LOG`) to opt out --
  the setting is shared across all three tools via `settings.json`
- Added a `privacy.html` page to the site disclosing exactly what's
  collected and how to turn it off, linked from every page's nav/footer
  and added to `sitemap.xml`
- Regenerated `gt7telem-source.zip` to match the current package (it had
  drifted badly out of sync -- was still the pre-refactor flat-file layout
  from before the `src/gt7telem/` restructure). Now correctly nested
  (`gt7telem/` folder at the zip root, matching `python -m gt7telem.launcher`
  usage) with an updated `requirements.txt` and `README.txt`

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
