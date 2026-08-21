# Contributing to TRACE

Thanks for taking a look — bug reports, small fixes, and car/track database
updates are all welcome.

## Dev setup

```bash
git clone https://github.com/ransh2014/gt7telemtrace.git
cd gt7telemtrace
pip install -e ".[dev]"
gt7telem
```

Run the test suite and linter before opening a PR (CI runs both on every push):

```bash
pytest tests/
ruff check .
```

The tests cover car/track DB lookups, settings persistence, and a Salsa20
decrypt+parse round-trip — they don't need a live GT7 session. For anything
touching the GUI apps themselves, the fastest check is still running the
affected tool (`dashboard`, `lap_analyst`, or `race_analyst`) against a live
session or a previously recorded lap.

## Where things live

- `src/gt7telem/udp.py` — heartbeat, packet receive/decrypt, the public
  `get_snapshot()` / `get_diagnostics()` API. Protocol changes belong here.
- `src/gt7telem/dashboard.py`, `lap_analyst.py`, `race_analyst.py` — the
  three Tkinter GUI apps. Each is a single self-contained module by design
  (no shared UI framework) to keep the whole project pip-installable with
  zero extra runtime dependencies.
- `src/gt7telem/cars.py` / `car_ids.csv` and `src/gt7telem/tracks.py` /
  `course_ids.csv` — ID → name lookups, sourced from
  [ddm999/gt7info](https://ddm999.github.io/gt7info/).
- `add_car.py` / `add_track.py` — CLI helpers for adding a missing ID to the
  local database without hand-editing the CSVs.

## Reporting a bug

Please include:
- What you were doing (Dashboard / Lap Analyst / Race Analyst, and which
  action)
- The output of `gt7telem.get_diagnostics()` if it's a connection issue
- Your OS and how you installed TRACE (pip, from source, or the standalone
  binary)

## Updating the car/track database

If a car or track shows up with a blank name, it's likely missing from
`car_ids.csv` / `course_ids.csv`. Run `add_car.py` / `add_track.py` locally
to add it, or open an issue with the ID and the in-game name so it can be
added to the next scheduled refresh.

## Pull requests

Keep PRs focused — one fix or one feature per PR is easier to review than a
batch of unrelated changes. Please bump `version` in `pyproject.toml` **and**
`__version__` in `src/gt7telem/__init__.py` together when a PR is meant to
ship as a release; the two have drifted out of sync before.

CI runs `pytest` and `ruff check .` on every PR — make sure both pass
locally first. Pushing a `vX.Y.Z` tag on `main` (after merging a version
bump) triggers an automatic build + PyPI publish via GitHub Actions.
