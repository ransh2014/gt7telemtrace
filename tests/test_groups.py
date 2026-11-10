import datetime

from gt7telem import groups as g


def board():
    ch = g.make_challenge("Monza", "GT-R", "2026-W46", "Monza weekly")
    assert g.add_result(ch, "Ransh", 91_500, [30_000, 31_000, 30_500])
    assert g.add_result(ch, "Sam", 90_800, [30_400, 30_300, 30_100])
    return ch


def test_week_id_is_iso():
    assert g.week_id(datetime.date(2026, 11, 10)) == "2026-W46"
    assert g.week_id(datetime.date(2026, 1, 1)) == "2026-W01"


def test_standings_gaps_and_best_kept():
    ch = board()
    assert g.add_result(ch, "ransh", 90_900)          # same player, faster: replaces
    assert g.add_result(ch, "Ransh", 99_000)          # slower: ignored
    rows = g.standings(ch)
    assert [r["player"] for r in rows] == ["Sam", "ransh"] or [r["player"] for r in rows] == ["Sam", "Ransh"]
    assert rows[0]["gap_ms"] == 0 and rows[1]["gap_ms"] == 100


def test_sector_board_and_ideal_lap():
    ch = board()
    sb = g.sector_board(ch)
    assert [(b["sector"], b["player"]) for b in sb] == [(1, "Ransh"), (2, "Sam"), (3, "Sam")]
    assert g.ideal_lap(ch) == 30_000 + 30_300 + 30_100
    assert g.fmt(90_800) == "1:30.800"


def test_rejects_nonsense():
    ch = g.make_challenge("T", "C", "2026-W46")
    assert not g.add_result(ch, "A", 5)
    assert not g.add_result(ch, "A", "x")
    assert not g.add_result(ch, "<>", 90_000) or ch["results"][0]["player"] == ""


def test_result_code_roundtrip_between_two_people():
    mine = board()
    code = g.export_result(mine, "Sam")
    theirs = g.make_challenge("Monza", "GT-R", "2026-W46")
    merged, msg = g.import_code(code, theirs)
    assert "Added" in msg and merged["results"][0]["player"] == "Sam"
    assert g.import_code(code, None)[0]["track"] == "Monza"


def test_challenge_code_merges_and_checks_week():
    mine = board()
    other = g.make_challenge("Monza", "GT-R", "2026-W46")
    g.add_result(other, "Lee", 92_000)
    merged, msg = g.import_code(g.export_challenge(mine), other)
    assert "Merged 2" in msg and {r["player"] for r in merged["results"]} == {"Lee", "Ransh", "Sam"}
    wrong = g.make_challenge("Monza", "GT-R", "2026-W47")
    assert "different" in g.import_code(g.export_challenge(mine), wrong)[1]


def test_tampered_or_garbage_codes_rejected():
    code = g.export_challenge(board())
    bad = code[:-3] + ("AAA" if not code.endswith("AAA") else "BBB")
    assert "isn't valid" in g.import_code(bad, None)[1]
    for junk in ("", "hello", "TRC1-abcdef-@@@", "TRC1-000000-"):
        assert "isn't valid" in g.import_code(junk, None)[1]


def test_storage(tmp_path):
    p = tmp_path / "g.json"
    assert g.load(p) == []
    assert g.save([board()], p)
    assert g.load(p)[0]["track"] == "Monza"
