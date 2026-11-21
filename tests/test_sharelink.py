"""Lap-sharing links: payload build/compress/size cap, ids, error paths, and the confirmation gate (nothing is sent without
it). No network: the HTTP call is injected. The Kotlin twin is compared with this module's output in Golden4Test."""
import base64
import json
import re
from pathlib import Path

import pytest
from _fixtures import full_lap_data

from gt7telem import labels as L
from gt7telem import sharelink as sl
from gt7telem.core import actions

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gt7telem"
GOOD = "0123456789abcdef0123"


def lap(**kw):
    d = full_lap_data()
    d.update(recorded_at="20260901_120000")
    d.update(kw)
    return d


class Http:
    """Records every call; answers with a canned (status, text) or raises."""

    def __init__(self, status=200, text=json.dumps(GOOD), exc=None):
        self.calls, self.status, self.text, self.exc = [], status, text, exc

    def __call__(self, method, path, body, bearer):
        self.calls.append((method, path, json.loads(body) if body else None, bearer))
        if self.exc:
            raise self.exc
        return self.status, self.text


# ── payload ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_payload_is_columnar_whole_numbers_in_a_fixed_order():
    p = sl.build_payload(lap())
    assert list(p) == ["v", "car", "track", "lap_time_ms", "n", "t", "x", "z", "s", "th", "br"]
    assert p["v"] == 1 and p["car"] == "Test Car" and p["track"] == "Test Ring" and p["lap_time_ms"] == 77298
    assert p["n"] == 720 == len(p["t"]) == len(p["x"]) == len(p["z"]) == len(p["s"]) == len(p["th"]) == len(p["br"])
    assert p["t"][0] == 0 and p["t"] == sorted(p["t"])
    assert all(isinstance(v, int) for k in ("t", "x", "z", "s", "th", "br") for v in p[k])
    assert all(0 <= v <= 1000 for v in p["th"] + p["br"]) and max(p["s"]) <= 20000
    raw = sl.payload_json(p)
    assert raw == sl.payload_json(sl.build_payload(lap())) and b" " not in raw.replace(b"Test Car", b"").replace(b"Test Ring", b"")
    raw.decode("ascii")


def test_quantiser_rounds_half_up_the_same_way_everywhere():
    assert sl._q(0.0005, 1000) == 1 and sl._q(12.25, 10) == 123 and sl._q(-0.5, 1) == 0 and sl._q(-0.51, 1) == -1


def test_laps_without_position_have_empty_map_columns_and_big_laps_are_thinned_evenly():
    samples = [{"t": i * 0.01, "speed_kmh": 100.0, "throttle": 1.0, "brake": 0.0} for i in range(9001)]
    p = sl.build_payload({"car": "C", "track": "T", "lap_time_s": 90.0, "samples": samples})
    assert p["x"] == [] and p["z"] == [] and p["n"] == sl.MAX_POINTS == len(p["t"])
    assert p["t"][0] == 0 and p["t"][-1] == 90000                       # first and last sample are always kept
    assert sl.pick_indices(10) == list(range(10)) and sl.pick_indices(9001)[-1] == 9000


def test_non_finite_and_missing_values_become_zero_and_names_are_capped_and_escaped():
    samples = [{"t": 0, "speed_kmh": float("nan"), "throttle": None}, {"t": 1, "speed_kmh": "x"}]
    p = sl.build_payload({"car": 'Pörsche "911"', "track_display": "N" * 300, "lap_time_s": 60, "samples": samples})
    assert p["s"] == [0, 0] and p["th"] == [0, 0] and len(p["track"]) == 120
    assert b'P\\u00f6rsche \\"911\\"' in sl.payload_json(p)


@pytest.mark.parametrize("change, text", [
    ({"incomplete": True}, L.SHARE_INCOMPLETE), ({"samples": []}, L.SHARE_NO_SAMPLES), ({"samples": [{"t": 0}]}, L.SHARE_NO_SAMPLES),
    ({"lap_time_s": 9.9}, L.SHARE_BAD_TIME), ({"lap_time_s": 3601}, L.SHARE_BAD_TIME), ({"lap_time_s": None}, L.SHARE_BAD_TIME),
])
def test_laps_that_cannot_be_shared_say_why(change, text):
    d = lap(**change)
    assert sl.sharing_problem(d) == text
    with pytest.raises(sl.ShareError):
        sl.build_payload(d)
    assert sl.sharing_problem(lap()) is None


# ── compression and the size cap ─────────────────────────────────────────────────────────────────────────────────
def test_compress_round_trips_and_is_small_for_a_real_lap():
    p = sl.build_payload(lap())
    blob = sl.compress(p)
    assert blob[:2] == b"\x1f\x8b" and len(blob) < 40_000 and len(blob) < sl.MAX_GZIP_BYTES
    assert sl.decompress(blob) == p
    assert blob == sl.compress(p)                                         # mtime=0: the same bytes every time


def test_size_cap_and_decompression_bomb_guard(monkeypatch):
    p = sl.build_payload(lap())
    monkeypatch.setattr(sl, "MAX_GZIP_BYTES", 100)
    with pytest.raises(sl.ShareError, match="too large"):
        sl.compress(p)
    import gzip
    with pytest.raises(sl.ShareError):
        sl.decompress(gzip.compress(b"a" * 5_000_000))


def test_the_server_cap_and_the_client_cap_agree():
    assert sl.MAX_GZIP_BYTES == 200 * 1024 and sl.MAX_POINTS <= 20000
    sql = (ROOT / "docs" / "supabase-shared-laps.sql").read_text(encoding="utf-8")
    assert "204800" in sql and "20000" in sql and sl.MIN_LAP_MS == 10_000 and "10000" in sql and "3600000" in sql


# ── ids and links ────────────────────────────────────────────────────────────────────────────────────────────────
def test_ids_and_links():
    assert sl.valid_id(GOOD) and not sl.valid_id(GOOD.upper()) and not sl.valid_id(GOOD + "0") and not sl.valid_id(None)
    assert sl.link_for(GOOD) == f"https://gt7trace.netlify.app/lap?id={GOOD}"
    with pytest.raises(ValueError):
        sl.link_for("../etc/passwd")
    assert sl.id_from(sl.link_for(GOOD)) == GOOD == sl.id_from(f"  {GOOD.upper()} ") == sl.id_from(f"x?a=1&id={GOOD}&b=2")
    assert sl.id_from(f"?id={GOOD}0") is None and sl.id_from("") is None and sl.id_from(None) is None


# ── the confirmation gate ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("flag", [False, None, 0, 1, "yes"])
def test_nothing_is_built_or_sent_without_confirmation(flag, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the lap was prepared before the user confirmed")
    monkeypatch.setattr(sl, "build_payload", boom)
    http = Http()
    res = sl.share(lap(), "tok", confirmed=flag, http=http)
    assert not res.ok and res.reason == "unconfirmed" and http.calls == []
    assert sl.reason_text("unconfirmed") == L.SHARE_FAIL_UNCONFIRMED


def test_the_confirmation_text_is_the_one_the_spec_requires():
    assert sl.CONFIRM_TEXT == ("This uploads this lap's telemetry so anyone with the link can view it. "
                               "It is deleted after 90 days or when you delete it.")


def test_share_sends_one_rpc_with_the_gzip_payload():
    http = Http()
    res = sl.share(lap(), "my-token", confirmed=True, http=http)
    assert res.ok and res.id == GOOD and res.link == sl.link_for(GOOD) and res.reason == ""
    [(method, path, body, bearer)] = http.calls
    assert (method, path, bearer) == ("POST", "/rest/v1/rpc/share_lap", "my-token")
    assert set(body) == {"p_car", "p_track", "p_lap_time_ms", "p_samples", "p_payload"}
    assert body["p_car"] == "Test Car" and body["p_lap_time_ms"] == 77298 and body["p_samples"] == 720
    assert sl.decompress(base64.b64decode(body["p_payload"]))["n"] == 720


@pytest.mark.parametrize("status, text, reason", [
    (401, "JWT expired", "auth"), (403, "", "auth"), (400, '{"code":"28000","message":"sign-in required"}', "auth"),
    (400, '{"code":"54000","message":"rate limit: 20 shared laps per day"}', "limit"), (429, "", "limit"),
    (400, '{"code":"22023","message":"payload too large"}', "toobig"), (400, '{"code":"22023","message":"lap time out of range"}', "data"),
    (500, "boom", "server"), (404, "", "server"),
])
def test_server_errors_map_to_clear_reasons(status, text, reason):
    res = sl.share(lap(), "tok", confirmed=True, http=Http(status, text))
    assert not res.ok and res.reason == reason and res.id == "" and res.link == ""
    assert sl.reason_text(reason)


def test_network_failure_bad_response_and_bad_lap():
    assert sl.share(lap(), "t", confirmed=True, http=Http(exc=OSError("down"))).reason == "network"
    assert sl.share(lap(), "t", confirmed=True, http=Http(200, "not json")).reason == "server"
    assert sl.share(lap(), "t", confirmed=True, http=Http(200, '"../../x"')).reason == "server"      # an id that isn't one
    http = Http()
    assert sl.share(lap(incomplete=True), "t", confirmed=True, http=http).reason == "data" and http.calls == []


def test_delete():
    http = Http(200, "true")
    res = sl.delete(GOOD, "tok", http=http)
    assert res.ok and http.calls == [("POST", "/rest/v1/rpc/delete_shared_lap", {"p_id": GOOD}, "tok")]
    bad = Http()
    assert sl.delete("nope", "tok", http=bad).reason == "data" and bad.calls == []
    assert sl.delete(GOOD, "tok", http=Http(401, "jwt")).reason == "auth"
    assert sl.delete(GOOD, "tok", http=Http(exc=OSError())).reason == "network"


def test_every_failure_reason_has_wording_and_the_default_http_is_not_used_in_tests():
    for r in sl.REASONS:
        assert sl.reason_text(r)
    assert sl.reason_text("whatever") == L.SHARE_FAIL_SERVER


# ── the app-level flow (core/actions.py) ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def signed_in(monkeypatch, tmp_path):
    rc = actions.runtime_config
    monkeypatch.setattr(rc, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(rc, "SUPABASE_ACCESS_TOKEN", "acc")
    monkeypatch.setattr(rc, "SUPABASE_REFRESH_TOKEN", "ref")
    monkeypatch.setattr(rc, "save", lambda **k: None)
    return tmp_path


def test_actions_refuse_without_confirmation_and_without_an_account(signed_in, monkeypatch):
    http = Http()
    res = actions.share_lap_link(lap(), confirmed=False, http=http)
    assert res.reason == "unconfirmed" and http.calls == [] and not (signed_in / "shared_laps.json").exists()
    monkeypatch.setattr(actions.runtime_config, "SUPABASE_ACCESS_TOKEN", "")
    res = actions.share_lap_link(lap(), confirmed=True, http=http)
    assert res.reason == "auth" and http.calls == []


def test_share_remembers_the_link_and_delete_uses_it(signed_in):
    d = lap()
    assert actions.shared_id(d) is None and actions.delete_shared_lap(d, http=Http()).reason == "data"
    res = actions.share_lap_link(d, confirmed=True, http=Http())
    assert res.ok and actions.shared_id(d) == GOOD
    http = Http(200, "true")
    assert actions.delete_shared_lap(d, http=http).ok and http.calls[0][2] == {"p_id": GOOD} and http.calls[0][3] == "acc"
    assert actions.shared_id(d) is None
    assert actions.shared_id(lap(lap_time_s=70.0)) is None                  # a different lap has no link


def test_a_failed_share_remembers_nothing(signed_in):
    d = lap()
    assert not actions.share_lap_link(d, confirmed=True, http=Http(500, "x")).ok and actions.shared_id(d) is None


def test_an_expired_session_is_refreshed_once(signed_in, monkeypatch):
    seen = []

    def http(method, path, body, bearer):
        seen.append(bearer)
        return (401, "jwt expired") if bearer == "acc" else (200, json.dumps(GOOD))
    monkeypatch.setattr(actions.auth, "refresh_session", lambda t: {"access_token": "new", "refresh_token": "r2", "user_id": "u"})
    assert actions.share_lap_link(lap(), confirmed=True, http=http).ok and seen == ["acc", "new"]
    monkeypatch.setattr(actions.auth, "refresh_session", lambda t: None)
    monkeypatch.setattr(actions.auth, "last_refresh_error", "invalid", raising=False)
    assert actions.share_lap_link(lap(), confirmed=True, http=lambda *a: (401, "jwt")).reason == "auth"


def test_share_store_survives_damage(tmp_path):
    p = tmp_path / "s.json"
    st = sl.ShareStore(p)
    assert st.get("k") is None
    p.write_text("{not json", encoding="utf-8")
    assert st.get("k") is None
    st.put("k", GOOD)
    assert st.get("k") == GOOD
    p.write_text(json.dumps({"k": "evil", "j": GOOD}), encoding="utf-8")
    assert st.get("k") is None and st.get("j") == GOOD                     # a value that isn't an id is ignored
    st.drop("j")
    assert st.get("j") is None


# ── every front end asks first (source-order checks: the Tk window, the terminal UI and the phone) ───────────────────
def _body(text, start, until):
    i = text.index(start)
    return text[i:text.index(until, i + 1)]


def test_front_ends_confirm_before_they_upload():
    tk = _body((SRC / "lap_analyst.py").read_text(encoding="utf-8"), "def _share_link(self):", "def _after_share")
    assert tk.index("askyesno") < tk.index("share_lap_link") and "sharelink.CONFIRM_TEXT" in tk
    tui = _body((SRC / "tui" / "app.py").read_text(encoding="utf-8"), "async def _share(self):", "async def _unshare")
    assert tui.index("CONFIRM_TEXT") < tui.index('!= "YES"') < tui.index("share_lap_link")
    kt = (ROOT / "android" / "app" / "src" / "main" / "kotlin" / "app" / "trace" / "LeaderboardUi.kt").read_text(encoding="utf-8")
    dialog = _body(kt, '"share_confirm" -> Dialog', '"share_account"')
    assert "R.string.share_confirm" in dialog and "ctl.shareLink" in dialog
    assert kt.count("ctl.shareLink(") == 2 and kt.index('"share_confirm" -> Dialog') < kt.index("ctl.shareLink(")     # only reachable from the dialog
    client = (ROOT / "android" / "app" / "src" / "main" / "kotlin" / "app" / "trace" / "ShareClient.kt").read_text(encoding="utf-8")
    assert client.count("if (!confirmed) return") == 2


def test_the_share_actions_are_registered_once_for_every_front_end():
    from gt7telem.core import registry
    kinds = [k for k, _ in registry.LAP_ACTIONS]
    assert kinds.count("share") == 1 and kinds.count("unshare") == 1
    assert ("share", L.SHARE_LAP_LINK) in registry.LAP_ACTIONS and ("unshare", L.SHARE_DELETE) in registry.LAP_ACTIONS


# ── the SQL file (never run by the tests or by TRACE) ─────────────────────────────────────────────────────────────
def test_supabase_sql_has_the_rpcs_rls_rate_limit_and_cleanup():
    sql = (ROOT / "docs" / "supabase-shared-laps.sql").read_text(encoding="utf-8")
    for needle in ("create table if not exists public.shared_laps", "enable row level security", "function public.share_lap(",
                   "function public.get_shared_lap(", "function public.delete_shared_lap(", "cron.schedule", "interval '90 days'",
                   ">= 20", "revoke all on public.shared_laps", "grant execute on function public.get_shared_lap(text)"):
        assert needle in sql, needle
    assert not re.search(r"create policy", sql, re.I), "clients must have no direct table access"
    assert "to anon, authenticated" in sql and "share_lap(text, text, integer, integer, text) to authenticated" in sql
