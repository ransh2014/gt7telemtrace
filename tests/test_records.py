"""Records table: header-only lap scanning and best-per-car-and-track grouping."""
import json
import os

from gt7telem import records


def _write_lap(folder, track, name, car, t, recorded="20261002_141500", incomplete=False, **extra):
    """A lap file laid out the way the Dashboard writes it: summary first, samples last."""
    d = {"recorded_at": recorded, "track": track, "car": car, "track_display": track.replace("_", " ").title(),
         "car_display": car, "lap_time_s": t, "lap_distance_m": 3000.0, "total_samples": 3,
         "incomplete": incomplete, "physics_era": "1.71", "gear_ratios": [3.1, 2.2]}
    d.update(extra)
    d["samples"] = [{"t": 0.0, "speed_kmh": 1.0}] * 3
    p = folder / track
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"{name}.json"
    f.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    return f


def test_header_is_read_without_the_samples(tmp_path):
    f = _write_lap(tmp_path, "spa", "a", "Car A", 140.2)
    h = records.lap_header(f)
    assert h["lap_time_s"] == 140.2 and "samples" not in h


def test_header_reads_a_big_file_from_its_first_bytes_only(tmp_path):
    f = _write_lap(tmp_path, "spa", "a", "Car A", 140.2)
    d = json.loads(f.read_text())
    d["samples"] = [{"t": i * 0.1, "speed_kmh": 100.0, "pad": "x" * 60} for i in range(40000)]   # ~4 MB
    f.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    assert records.lap_header(f)["lap_time_s"] == 140.2


def test_header_falls_back_to_a_full_read_for_other_key_orders(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"samples": [{"t": 1}], "lap_time_s": 99.5, "car": "C"}), encoding="utf-8")
    assert records.lap_header(f)["lap_time_s"] == 99.5
    # indented (the analyst re-saves lap files with notes this way) works too
    f.write_text(json.dumps({"car": "C", "lap_time_s": 98.0, "samples": [{"t": 1}], "notes": "hi"}, indent=2),
                 encoding="utf-8")
    assert records.lap_header(f)["lap_time_s"] == 98.0


def test_non_lap_json_is_ignored(tmp_path):
    for name, content in {"a.json": "[1,2]", "b.json": "{not json", "c.json": '{"hello": 1}',
                          "d.json": '{"samples": [], "lap_time_s": 5}'}.items():
        (tmp_path / name).write_text(content)
    assert records.lap_header(tmp_path / "a.json") is None
    assert records.lap_header(tmp_path / "b.json") is None
    assert records.lap_header(tmp_path / "c.json") is None
    assert records.lap_header(tmp_path / "missing.json") is None


def test_best_lap_per_car_and_track(tmp_path):
    _write_lap(tmp_path, "spa", "a1", "Car A", 142.0)
    _write_lap(tmp_path, "spa", "a2", "Car A", 140.5, recorded="20261003_100000")
    _write_lap(tmp_path, "spa", "b1", "Car B", 145.0)
    _write_lap(tmp_path, "monza", "a3", "Car A", 85.25)
    recs = records.build_records(tmp_path)
    assert [(r["track"], r["car"], r["time"], r["laps"]) for r in recs] == [
        ("Monza", "Car A", "1:25.250", 1),
        ("Spa", "Car A", "2:20.500", 2),
        ("Spa", "Car B", "2:25.000", 1),
    ]
    spa_a = recs[1]
    assert spa_a["date"] == "2026-10-03" and spa_a["path"].endswith("a2.json")


def test_incomplete_laps_and_other_files_never_count(tmp_path):
    _write_lap(tmp_path, "spa", "ok", "Car A", 150.0)
    _write_lap(tmp_path, "spa", "partial", "Car A", 60.0, incomplete=True)
    _write_lap(tmp_path, "spa", "reference_car_a", "Car A", 100.0)       # a copy of a lap that is also saved
    _write_lap(tmp_path / "spa", "races", "race_car_a", "Car A", 50.0)     # race recordings live in races/
    (tmp_path / "spa" / "personal_bests.json").write_text('{"car_a": {"lap_time_s": 1}}')
    (tmp_path / "_session").mkdir()
    (tmp_path / "_session" / "session_1.json").write_text('{"races": []}')
    recs = records.build_records(tmp_path)
    assert len(recs) == 1 and recs[0]["time"] == "2:30.000" and recs[0]["laps"] == 1


def test_laps_from_before_the_physics_change_are_optional(tmp_path):
    _write_lap(tmp_path, "spa", "old", "Car A", 130.0, recorded="20260101_100000", physics_era="pre-1.71")
    _write_lap(tmp_path, "spa", "new", "Car A", 140.0)
    assert records.build_records(tmp_path)[0]["time"] == "2:20.000"
    assert records.build_records(tmp_path, include_old=True)[0]["time"] == "2:10.000"
    only_old = tmp_path / "x"
    only_old.mkdir()
    _write_lap(only_old, "t", "o", "C", 99.0, recorded="20260101_100000", physics_era="pre-1.71")
    assert records.build_records(only_old) == []


def test_same_car_and_track_with_different_capitalisation_is_one_record(tmp_path):
    _write_lap(tmp_path, "spa", "a", "car a", 150.0)
    _write_lap(tmp_path, "spa", "b", "Car A", 149.0)
    assert len(records.build_records(tmp_path)) == 1


def test_missing_or_empty_folder_gives_no_records(tmp_path):
    assert records.build_records(tmp_path / "nope") == []
    assert records.build_records(tmp_path) == []


def test_filter_matches_car_or_track():
    rows = [{"car": "Mazda RX-7", "track": "Suzuka"}, {"car": "Audi R8", "track": "Spa"}]
    assert records.filter_records(rows, "") == rows
    assert records.filter_records(rows, "mazda") == rows[:1]
    assert records.filter_records(rows, "SPA") == rows[1:]
    assert records.filter_records(rows, "nothing") == []


def test_rescanning_uses_the_cache_until_a_file_changes(tmp_path, monkeypatch):
    f = _write_lap(tmp_path, "spa", "a", "Car A", 150.0)
    records.build_records(tmp_path)
    calls = []
    real = records.lap_header
    monkeypatch.setattr(records, "lap_header", lambda p: calls.append(p) or real(p))
    records.build_records(tmp_path)
    assert calls == []
    d = json.loads(f.read_text())
    d["lap_time_s"] = 149.0
    f.write_text(json.dumps(d))
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # a real edit moves the mtime on
    assert records.build_records(tmp_path)[0]["time"] == "2:29.000" and len(calls) == 1
