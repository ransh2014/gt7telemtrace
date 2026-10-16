"""Junk-lap guards and duplicate-lap cleanup.

A lap file can occasionally be saved twice, or a snippet that isn't a real lap
(a few samples, a parked car, a lap restart) can slip through. junk_reason() lets
the Dashboard refuse those at save time; find_duplicates()/move_duplicates() let
the Lap Analyst tidy up copies that already exist. Nothing here ever deletes a
file: extras are moved into a ``_duplicates`` folder.
"""
import hashlib
import json
import shutil
from pathlib import Path

MIN_LAP_SAMPLES = 100
MIN_LAP_SECONDS = 10.0
MIN_LAP_METERS = 300.0
MIN_MOVING_KPH = 5.0
DUP_FOLDER = "_duplicates"
# Reference / personal-best files are intentional copies of a lap: never touch them.
_SKIP_PREFIXES = ("reference_", "personal_bests", "backup")


def junk_reason(samples, lap_seconds=None, distance_m=None):
    """Why this recording shouldn't be saved as a lap, or None when it looks real."""
    n = len(samples)
    if n < MIN_LAP_SAMPLES:
        return f"only {n} samples"
    if lap_seconds is not None and lap_seconds < MIN_LAP_SECONDS:
        return f"only {lap_seconds:.1f} s long"
    if max(float(s.get("speed_kmh") or 0.0) for s in samples) < MIN_MOVING_KPH:
        return "the car never moved"
    if distance_m is not None and distance_m < MIN_LAP_METERS:
        return f"only {distance_m:.0f} m driven"
    return None


def samples_signature(track, car, lap_seconds, samples):
    """Cheap identity for a lap: same lap saved twice gives the same value."""
    n = len(samples)
    if n == 0:
        return None
    probe = [samples[0], samples[n // 2], samples[-1]]
    digest = hashlib.sha1(json.dumps(probe, sort_keys=True, default=str).encode()).hexdigest()
    return (str(track), str(car), round(float(lap_seconds or 0.0), 3), n, digest)


def _is_lap_file(p):
    name = p.name.lower()
    return (p.suffix.lower() == ".json" and not name.startswith(_SKIP_PREFIXES)
            and DUP_FOLDER not in p.parts)


def _signature_of_file(p):
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        samples = d.get("samples")
        if not isinstance(samples, list) or d.get("lap_time_s") is None:
            return None, ""
        return (samples_signature(d.get("track"), d.get("car"), d.get("lap_time_s"), samples),
                str(d.get("recorded_at") or ""))
    except Exception:
        return None, ""


def find_duplicates(root):
    """[(keep, [extra copies])] for identical laps inside the same folder.
    The earliest recording is kept."""
    root = Path(root)
    if not root.exists():
        return []
    by_size = {}
    for p in root.rglob("*.json"):
        if _is_lap_file(p):
            try:
                by_size.setdefault((p.parent, p.stat().st_size), []).append(p)
            except OSError:
                pass
    groups = {}
    for paths in by_size.values():
        if len(paths) < 2:
            continue                      # a unique size can't be a duplicate
        for p in paths:
            sig, rec = _signature_of_file(p)
            if sig is not None:
                groups.setdefault((p.parent, sig), []).append((rec, p.name, p))
    out = []
    for items in groups.values():
        if len(items) > 1:
            items.sort()
            out.append((items[0][2], [i[2] for i in items[1:]]))
    return sorted(out, key=lambda g: str(g[0]))


def move_duplicates(groups, root):
    """Move the extra copies into <root>/_duplicates, keeping their sub-folder.
    Never overwrites. Returns (moved [(src, dst)], errors [(src, message)])."""
    root = Path(root)
    moved, errors = [], []
    for _keep, extras in groups:
        for src in extras:
            try:
                rel = src.parent.relative_to(root)
                dest_dir = root / DUP_FOLDER / rel
                dest_dir.mkdir(parents=True, exist_ok=True)
                dst = dest_dir / src.name
                i = 1
                while dst.exists():
                    dst = dest_dir / f"{src.stem}_{i}{src.suffix}"
                    i += 1
                shutil.move(str(src), str(dst))
                moved.append((src, dst))
            except Exception as e:
                errors.append((src, str(e)))
    return moved, errors
