"""MoTeC .ld export. The file is read back with a small reference parser written here from the documented layout
(not from motec.py), so a wrong offset in the writer cannot hide behind a matching wrong offset in the reader.

Nothing in this file proves MoTeC i2 itself opens the output; that has to be checked by a person with i2."""
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from _fixtures import full_lap_data

from gt7telem import cli, motec
from gt7telem.core import actions, registry

HEAD = "<I4xII20xI24xHII8sHHI4x16s16x16s16x64s64s64x64s64x1024xI66x64s126x"
CHAN = "<IIIIHHHHhhhh32s8s12s40x"


def _s(b):
    return b.split(b"\0", 1)[0].decode("ascii")


def parse(blob: bytes) -> dict:
    """The header, event and every channel (with decoded samples) of an .ld file."""
    assert struct.calcsize(HEAD) == 1762 and struct.calcsize(CHAN) == 124
    (marker, meta_ptr, data_ptr, event_ptr, _a, _b, serial, dev, ver, _c, n, date, time, driver, vehicle, venue, _pro,
     comment) = struct.unpack_from(HEAD, blob, 0)
    ev = struct.unpack_from("<64s64s1024sH", blob, event_ptr)
    out = {"marker": marker, "meta_ptr": meta_ptr, "data_ptr": data_ptr, "event_ptr": event_ptr, "serial": serial,
           "device": _s(dev), "version": ver, "n": n, "date": _s(date), "time": _s(time), "driver": _s(driver),
           "vehicle": _s(vehicle), "venue": _s(venue), "comment": _s(comment),
           "event": {"name": _s(ev[0]), "session": _s(ev[1]), "comment": _s(ev[2]), "venue_ptr": ev[3]}, "channels": []}
    ptr = meta_ptr
    seen = []
    while ptr:
        seen.append(ptr)
        prev_p, next_p, dptr, count, _id, dtype_a, size, hz, shift, mul, scale, dec, name, short, unit = \
            struct.unpack_from(CHAN, blob, ptr)
        code = {(0x07, 4): "<f4", (0x03, 2): "<i2"}[(dtype_a, size)]
        raw = np.frombuffer(blob, dtype=code, count=count, offset=dptr).astype(float)
        vals = (raw / scale * 10.0 ** (-dec) + shift) * mul
        out["channels"].append({"name": _s(name), "short": _s(short), "unit": _s(unit), "hz": hz, "count": count,
                                "prev": prev_p, "next": next_p, "ptr": ptr, "data_ptr": dptr, "size": size, "values": vals})
        ptr = next_p
    out["meta_ptrs"] = seen
    return out


def _lap_frame():
    d = full_lap_data()
    df = pd.DataFrame(d["samples"])
    return df, {"car": "Test Car", "track": "Test Ring", "recorded_at": "20260901_120530", "lap_time_s": d["lap_time_s"]}


def test_layout_constants_are_the_documented_ones():
    assert (motec.HEADER_SIZE, motec.EVENT_SIZE, motec.CHANNEL_SIZE) == (1762, 1154, 124)
    assert motec.FIRST_CHANNEL_POS == 1762 + 1154


def test_roundtrip_header_channels_and_samples(tmp_path):
    df, meta = _lap_frame()
    rep = motec.write(tmp_path / "lap.ld", df, meta, driver="Tester")
    blob = (tmp_path / "lap.ld").read_bytes()
    f = parse(blob)
    assert (f["marker"], f["event_ptr"], f["meta_ptr"]) == (64, 1762, 2916)
    assert f["device"] == "ADL" and f["n"] == len(f["channels"]) == len(rep.channels)
    assert f["data_ptr"] == 2916 + 124 * f["n"]
    assert (f["date"], f["time"]) == ("01/09/2026", "12:05:30")
    assert (f["driver"], f["vehicle"], f["venue"]) == ("Tester", "Test Car", "Test Ring")
    assert f["event"]["name"] == "Test Ring" and f["event"]["venue_ptr"] == 0 and "lap" in f["comment"] + f["event"]["comment"]
    names = [c["name"] for c in f["channels"]]
    assert names == [c.name for c in motec.CHANNELS] == rep.channels and rep.skipped == []
    # one rate for every channel: the lap's own, the median sample spacing (the fixture's spacing varies with speed)
    t = df["t"].to_numpy(float)
    dt = np.diff(t)
    assert {c["hz"] for c in f["channels"]} == {rep.hz} == {int(round(1 / np.median(dt[dt > 1e-4])))}
    assert {c["count"] for c in f["channels"]} == {rep.samples}
    # pointers chain and the data sits back to back, ending exactly at the end of the file
    for i, c in enumerate(f["channels"]):
        assert c["ptr"] == 2916 + 124 * i
        assert c["prev"] == (0 if i == 0 else 2916 + 124 * (i - 1))
        assert c["next"] == (0 if i == len(f["channels"]) - 1 else 2916 + 124 * (i + 1))
    pos = f["data_ptr"]
    for c in f["channels"]:
        assert c["data_ptr"] == pos
        pos += c["count"] * c["size"]
    assert pos == len(blob)
    # sample values: units, scaling and the time base
    by = {c["name"]: c for c in f["channels"]}
    grid = t[0] + np.arange(rep.samples) / rep.hz
    assert by["Speed"]["unit"] == "km/h" and by["Throttle"]["unit"] == "%"
    np.testing.assert_allclose(by["Speed"]["values"], np.interp(grid, t, df["speed_kmh"]), rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(by["Throttle"]["values"], np.interp(grid, t, df["throttle"]) * 100, rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(by["Brake"]["values"], np.interp(grid, t, df["brake"]) * 100, rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(by["Track Position"]["values"], np.interp(grid, t, df["track_position"]), rtol=1e-5, atol=1e-2)
    np.testing.assert_allclose(by["Tyre Temp FL"]["values"], np.interp(grid, t, df["tyre_temp_fl"]), rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(by["Lap Time"]["values"], grid - grid[0], atol=1e-5)
    assert by["Lap Time"]["values"][0] == 0.0 and by["Lap Time"]["unit"] == "s"
    gear = by["Gear"]["values"]
    assert set(np.unique(gear)) <= set(range(0, 9)) and by["Gear"]["size"] == 2     # whole numbers only: no blending
    assert len(by["G Lat"]["values"]) == rep.samples and np.abs(by["G Lat"]["values"]).max() > 0


def test_names_fit_their_fields():
    for c in motec.CHANNELS:
        assert len(c.name) <= 32 and len(c.short) <= 8 and len(c.unit) <= 12, c


def test_a_lap_with_missing_columns_skips_them_and_says_so(tmp_path):
    df, meta = _lap_frame()
    df = df.drop(columns=["tyre_temp_fl", "tyre_temp_fr", "rpm", "world_x"])
    rep = motec.write(tmp_path / "a.ld", df, meta)
    f = parse((tmp_path / "a.ld").read_bytes())
    names = [c["name"] for c in f["channels"]]
    assert "Tyre Temp FL" not in names and "Engine RPM" not in names and "World X" not in names and "Speed" in names
    assert {n for n, _ in rep.skipped} == {"Tyre Temp FL", "Tyre Temp FR", "Engine RPM", "World X"}
    assert all(why == "not in this lap" for _n, why in rep.skipped)
    assert f["n"] == len(motec.CHANNELS) - 4
    assert "skipped" in rep.text() and "Engine RPM" in rep.text()


def test_nans_are_filled_and_an_all_nan_channel_is_skipped(tmp_path):
    df, meta = _lap_frame()
    df.loc[100:140, "speed_kmh"] = np.nan
    df["rpm"] = np.nan
    df.loc[10, "throttle"] = np.inf
    df["fuel_remaining"] = "n/a"                      # not numeric at all
    rep = motec.write(tmp_path / "n.ld", df, meta)
    by = {c["name"]: c for c in parse((tmp_path / "n.ld").read_bytes())["channels"]}
    assert np.isfinite(by["Speed"]["values"]).all() and np.isfinite(by["Throttle"]["values"]).all()
    assert (by["Speed"]["values"] > 0).all()           # gaps were bridged, not zeroed
    assert {n: why for n, why in rep.skipped} == {"Engine RPM": "no valid values", "Fuel": "no valid values"}


def test_a_lap_with_no_timestamps_still_exports_at_a_default_rate(tmp_path):
    df, meta = _lap_frame()
    df = df.drop(columns=["t"])
    rep = motec.write(tmp_path / "t.ld", df, meta)
    f = parse((tmp_path / "t.ld").read_bytes())
    assert rep.hz == motec.FALLBACK_HZ and f["channels"][0]["count"] == len(df)


def test_duplicate_and_backwards_timestamps_do_not_crash(tmp_path):
    df, meta = _lap_frame()
    df.loc[50:60, "t"] = df.loc[50, "t"]
    df.loc[300, "t"] = df.loc[200, "t"]
    rep = motec.write(tmp_path / "d.ld", df, meta)
    by = {c["name"]: c for c in parse((tmp_path / "d.ld").read_bytes())["channels"]}
    assert (np.diff(by["Lap Time"]["values"]) >= 0).all() and rep.samples > 100


def test_very_short_laps(tmp_path):
    df, meta = _lap_frame()
    with pytest.raises(ValueError, match="too few"):
        motec.write(tmp_path / "x.ld", df.iloc[:1], meta)
    with pytest.raises(ValueError):
        motec.write(tmp_path / "x.ld", None, meta)
    assert not (tmp_path / "x.ld").exists() and not list(tmp_path.glob("*.tmp"))
    # two samples a hair apart: too little time for a grid, so it falls back to one sample per row
    tiny = df.iloc[:2].copy()
    tiny["t"] = [0.0, 0.01]
    rep = motec.write(tmp_path / "tiny.ld", tiny, meta)
    f = parse((tmp_path / "tiny.ld").read_bytes())
    assert f["channels"][0]["count"] == 2 == rep.samples


def test_header_text_is_ascii_and_truncated(tmp_path):
    df, meta = _lap_frame()
    meta = {**meta, "car": "Ünïcode car " + "x" * 200, "track": "日本 Ring", "recorded_at": "garbage"}
    motec.write(tmp_path / "h.ld", df, meta, driver="D" * 100)
    f = parse((tmp_path / "h.ld").read_bytes())
    assert len(f["vehicle"]) == 64 and f["vehicle"].startswith("?n?code car") and len(f["driver"]) == 64
    assert f["venue"] == "?? Ring" and len(f["date"]) == 10      # an unreadable stamp falls back to now


def test_g_channels_are_derived_when_the_frame_has_none(tmp_path):
    df, meta = _lap_frame()
    assert "lat_g" not in df.columns and "long_g" not in df.columns
    rep = motec.write(tmp_path / "g.ld", df, meta)
    assert rep.skipped == []
    by = {c["name"]: c for c in parse((tmp_path / "g.ld").read_bytes())["channels"]}
    assert np.abs(by["G Long"]["values"]).max() > 0 and np.abs(by["G Lat"]["values"]).max() <= 4.0


def test_write_creates_folders_and_replaces_atomically(tmp_path):
    df, meta = _lap_frame()
    p = tmp_path / "deep" / "er" / "lap.ld"
    motec.write(p, df, meta)
    first = p.read_bytes()
    motec.write(p, df, meta)
    assert p.read_bytes() == first and list(p.parent.glob("*.tmp")) == []


def test_shared_action_and_cli(tmp_path, capsys):
    df, meta = _lap_frame()
    rep = actions.export_motec(df, meta, tmp_path / "act.ld")
    assert rep.path == tmp_path / "act.ld" and (tmp_path / "act.ld").stat().st_size > 2916
    lap = tmp_path / "lap.json"
    lap.write_text(json.dumps({**meta, "samples": df.to_dict("records")}), encoding="utf-8")
    assert cli.main(["motec", str(lap)]) == 0
    assert (tmp_path / "lap.ld").exists() and "Wrote" in capsys.readouterr().out
    out = tmp_path / "custom.ld"
    assert cli.main(["motec", str(lap), str(out)]) == 0 and out.exists()
    bad = tmp_path / "short.json"
    bad.write_text(json.dumps({**meta, "samples": df.to_dict("records")[:1]}), encoding="utf-8")
    assert cli.main(["motec", str(bad)]) == 2
    assert parse(out.read_bytes())["n"] == len(motec.CHANNELS)


def test_the_export_is_offered_in_the_tk_window_and_the_terminal_ui():
    assert ("motec", registry.MOTEC_EXPORT_LABEL) in registry.LAP_ACTIONS
    root = Path(__file__).resolve().parents[1] / "src" / "gt7telem"
    assert "registry.MOTEC_EXPORT_LABEL" in (root / "lap_analyst.py").read_text(encoding="utf-8")
    assert 'kind == "motec"' in (root / "tui" / "app.py").read_text(encoding="utf-8")
    assert "export_motec" in (root / "lap_analyst.py").read_text(encoding="utf-8")


def test_reads_back_with_ldparser_when_it_is_installed(tmp_path):
    ldparser = pytest.importorskip("ldparser")
    df, meta = _lap_frame()
    motec.write(tmp_path / "ref.ld", df, meta)
    head, chans = ldparser.read_ldfile(str(tmp_path / "ref.ld"))
    assert [c.name for c in chans] == [c.name for c in motec.CHANNELS]
    speed = next(c for c in chans if c.name == "Speed")
    assert len(speed.data) > 100 and speed.freq == motec.write(tmp_path / "again.ld", df, meta).hz
