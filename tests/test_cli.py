import json

import numpy as np
from test_coach import _costly
from test_corners import _lap

from gt7telem import cli


def _save(path, df, t=None):
    n = len(df)
    th = np.linspace(0, 2 * np.pi, n)
    samples = df.to_dict("records")
    for s, a in zip(samples, th):
        s.update(world_x=300 * np.cos(a), world_y=0.0, world_z=200 * np.sin(a), ang_y=s["lat_g"] * 9.81 / 55.0)
    path.write_text(json.dumps({"lap_time_s": t or float(df["t"].iloc[-1]), "car": "GT-R", "track": "Test",
                                "samples": samples}), encoding="utf-8")
    return path


def test_coach_validate_setup_plan(tmp_path, capsys):
    ref = _save(tmp_path / "ref.json", _lap())
    lap = _save(tmp_path / "lap.json", _costly(_lap(throttle_shift=40), 20, 60))
    assert cli.main(["coach", str(lap), str(ref)]) == 0
    assert "throttle" in capsys.readouterr().out.lower()
    cli.main(["setup", str(lap)])
    assert capsys.readouterr().out.strip()
    cli.main(["validate", str(ref)])
    assert "clean" in capsys.readouterr().out
    folder = tmp_path / "session"
    folder.mkdir()
    for i, df in enumerate([_lap(), _costly(_lap(), 20, 80, 0.8), _costly(_lap(), 20, 80, 0.85)]):
        _save(folder / f"{i}.json", df)
    cli.main(["plan", str(folder)])
    assert "corner" in capsys.readouterr().out.lower()


def test_replay_and_dna(tmp_path, capsys):
    lap = _save(tmp_path / "lap.json", _lap())
    out = tmp_path / "r.html"
    assert cli.main(["replay", str(lap), str(out), "--gif", str(tmp_path / "r.gif")]) == 0
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>") and (tmp_path / "r.gif").exists()
    capsys.readouterr()
    assert cli.main(["dna", str(lap), "--code", "--name", "Me"]) == 0
    code = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("DNA1:")][0]
    assert cli.main(["dna", str(lap), "--vs", code]) == 0


def test_bad_file_is_a_clean_error(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.main(["setup", str(bad)]) == 2
    assert "not a TRACE lap file" in capsys.readouterr().err


def test_challenge_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    lap = _save(tmp_path / "lap.json", _lap())
    base = ["challenge", "--track", "Monza", "--car", "GT-R", "--week", "2026-W46"]
    assert cli.main([base[0], "new", *base[1:]]) == 0
    assert cli.main([base[0], "add", *base[1:], "--player", "Ransh", "--lap", str(lap)]) in (0, None)
    out = capsys.readouterr().out
    assert "Ransh" in out
    cli.main([base[0], "export", *base[1:], "--player", "Ransh"])
    code = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("TRC1-")][0]
    assert cli.main([base[0], "import", *base[1:], "--code", code]) == 0
