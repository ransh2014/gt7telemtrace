"""WEB UI passcode (webui_auth + webui_server with ALLOW REMOTE on).

Unit tests drive webui_auth.Auth with a fake clock; end-to-end tests run the real server on 0.0.0.0 and talk to it
from 127.0.0.1 with the localhost bypass switched off, so every request takes the "other device" path.
"""
import datetime as dt
import http.client
import json
import socket

import pytest

pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402

from gt7telem import webui_auth, webui_server  # noqa: E402
from gt7telem.webui_auth import DEVICE_COOKIE, SESSION_COOKIE, Auth  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── unit: Auth ────────────────────────────────────────────────────────────────
@pytest.fixture
def auth(tmp_path):
    clock = Clock()
    a = Auth(devices_path=tmp_path / "devices.json", clock=clock)
    a.clock = clock
    a.rotate()
    return a


def test_code_is_six_random_digits_and_rotates(auth):
    seen = set()
    for _ in range(20):
        auth.rotate()
        assert len(auth.code) == 6 and auth.code.isdigit()
        seen.add(auth.code)
    assert len(seen) > 1


def test_correct_and_wrong_code(auth):
    wrong = f"{(int(auth.code) + 1) % 1_000_000:06d}"
    assert auth.check_code("10.0.0.2", wrong) is False
    assert auth.check_code("10.0.0.2", "") is False
    assert auth.check_code("10.0.0.2", None) is False
    assert auth.check_code("10.0.0.2", auth.code) is True
    auth.end()
    assert auth.check_code("10.0.0.2", "") is False     # no code at all while off


def test_lockout_backoff_and_cap(auth):
    ip, wrong = "10.0.0.9", "x"
    for _ in range(4):
        assert not auth.check_code(ip, wrong)
    assert not auth.locked(ip)
    assert not auth.check_code(ip, wrong)                # 5th wrong try
    assert auth.locked(ip)
    assert not auth.check_code(ip, auth.code)            # even the right code is refused while locked
    auth.clock.t += 59
    assert auth.locked(ip)
    auth.clock.t += 1.5
    assert not auth.locked(ip)
    durations = []
    for _ in range(6):                                    # each further lockout doubles, capped at 10 minutes
        for _ in range(5):
            auth.check_code(ip, wrong)
        start = auth.clock.t
        while auth.locked(ip):
            auth.clock.t += 1
        durations.append(round(auth.clock.t - start))
    assert durations == [120, 240, 480, 600, 600, 600]
    assert not auth.locked("10.0.0.10")                   # per IP
    assert auth.check_code("10.0.0.10", auth.code)
    assert auth.check_code(ip, auth.code)                 # success clears the IP's record
    assert ip not in auth._fails


def test_qr_token_single_use_and_expiry(auth):
    t1 = auth.qr_token()
    assert len(t1) >= 43 and auth.qr_token() == t1        # stable until used
    assert not auth.consume_qr("nope")
    assert auth.consume_qr(t1)
    assert not auth.consume_qr(t1)                        # single use
    t2 = auth.qr_token()
    assert t2 != t1
    auth.clock.t += webui_auth.QR_TTL_S
    assert not auth.consume_qr(t2)                        # 5 minutes
    t3 = auth.qr_token()
    assert t3 not in (t1, t2) and auth.consume_qr(t3)
    auth.end()
    assert auth.qr_token() == "" and not auth.consume_qr(t3)


def test_sessions_end_on_rotate_end_and_logout(auth):
    s = auth.new_session()
    assert auth.is_authed({SESSION_COOKIE: s})
    assert not auth.is_authed({SESSION_COOKIE: s + "x"})
    auth.rotate()
    assert not auth.is_authed({SESSION_COOKIE: s})
    s = auth.new_session()
    auth.end()
    auth.rotate()
    assert not auth.is_authed({SESSION_COOKIE: s})


def test_remembered_device_survives_rotation_and_restart(auth, tmp_path):
    d = auth.remember_device()
    auth.rotate()
    assert auth.code and auth.is_authed({DEVICE_COOKIE: d})
    fresh = Auth(devices_path=tmp_path / "devices.json")   # an app restart
    fresh.rotate()
    assert fresh.is_authed({DEVICE_COOKIE: d})
    stored = (tmp_path / "devices.json").read_text(encoding="utf-8")
    assert d not in stored                                 # only the SHA-256 hash is kept
    row = json.loads(stored)["devices"][0]
    assert set(row) == {"hash", "created"} and len(row["hash"]) == 64


def test_remembered_device_expires_after_30_days(tmp_path):
    day = [dt.date(2026, 11, 9)]
    a = Auth(devices_path=tmp_path / "devices.json", today=lambda: day[0])
    a.rotate()
    d = a.remember_device()
    day[0] += dt.timedelta(days=29)
    assert a.is_authed({DEVICE_COOKIE: d})
    day[0] += dt.timedelta(days=1)
    assert not a.is_authed({DEVICE_COOKIE: d})


def test_logout_all_wipes_devices_and_sessions(auth, tmp_path):
    d, s = auth.remember_device(), auth.new_session()
    auth.logout_all()
    assert not auth.is_authed({DEVICE_COOKIE: d}) and not auth.is_authed({SESSION_COOKIE: s})
    assert json.loads((tmp_path / "devices.json").read_text(encoding="utf-8")) == {"devices": []}


def test_damaged_devices_file_counts_as_empty(tmp_path):
    p = tmp_path / "devices.json"
    p.write_text("{not json", encoding="utf-8")
    a = Auth(devices_path=p)
    a.rotate()
    assert not a.is_authed({DEVICE_COOKIE: "anything"})
    a.remember_device()                                    # and it recovers
    assert len(json.loads(p.read_text(encoding="utf-8"))["devices"]) == 1


def test_loopback_and_cookie_parsing():
    for h in ("127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1"):
        assert webui_auth.is_loopback(h)
    for h in ("192.168.1.5", "0.0.0.0", "::ffff:10.0.0.1", "", "junk"):
        assert not webui_auth.is_loopback(h)
    assert webui_auth.parse_cookies("a=1; trace_s=xyz; b") == {"a": "1", "trace_s": "xyz"}


# ── end to end: the real server with ALLOW REMOTE on ─────────────────────────
@pytest.fixture
def remote(tmp_path, monkeypatch):
    """WEB UI on 0.0.0.0, treating 127.0.0.1 as another device (bypass off)."""
    monkeypatch.setattr(webui_server, "_LOOPBACK_BYPASS", False)
    monkeypatch.setattr(webui_server, "_AUTH_RECHECK_S", 0.1)
    monkeypatch.setattr(webui_server._auth, "_devices_path", tmp_path / "devices.json")
    monkeypatch.setattr(webui_server._auth, "_fails", {})
    port = _free_port()
    assert webui_server.start(port, addr="0.0.0.0")
    assert webui_server.auth_required()
    yield port
    webui_server.stop()


def _req(port, path, cookies=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    c.request("GET", path, headers=h)
    r = c.getresponse()
    body = r.read()
    out = (r.status, r.getheaders(), body)
    c.close()
    return out


def _hdr(headers, name):
    return [v for k, v in headers if k.lower() == name.lower()]


def _set_cookies(headers):
    out = {}
    for v in _hdr(headers, "Set-Cookie"):
        name, _, rest = v.partition("=")
        out[name] = (rest.split(";", 1)[0], v)
    return out


def _login(port, code, remember=False, ip_headers=None):
    return _req(port, "/login", headers={"X-Trace-Code": code, "X-Trace-Remember": "1" if remember else "0",
                                         **(ip_headers or {})})


def _assert_security_headers(headers):
    assert _hdr(headers, "X-Content-Type-Options") == ["nosniff"]
    assert _hdr(headers, "X-Frame-Options") == ["DENY"]
    assert _hdr(headers, "Referrer-Policy") == ["no-referrer"]
    assert _hdr(headers, "Cache-Control") == ["no-store"]


ROUTES = ["/", "/index.html", "/session", "/ws", "/nope", "/favicon.ico", "/index.html?x=1"]


def test_every_route_and_the_stream_reject_unauthenticated(remote):
    for path in ROUTES:
        st, h, body = _req(remote, path)
        assert st == 401, path
        assert b"Enter <em>code</em>" in body and b'"type":"f"' not in body, path   # the login page, no data
        _assert_security_headers(h)
    webui_server.update({"speed_kmh": 99}, {"connected": True})
    with pytest.raises(Exception):
        with connect(f"ws://127.0.0.1:{remote}/ws", open_timeout=5):
            pass
    st, _, _ = _req(remote, "/", cookies={SESSION_COOKIE: "forged", DEVICE_COOKIE: "forged"})
    assert st == 401


def test_login_page_style():
    from gt7telem.webui_login import LOGIN_PAGE
    for needle in ("#08080B", "font-family:'Anton'", "font-family:'Sora'", "font-family:'Manrope'",
                   "font-family:'JetBrains Mono'", 'id="code"', 'id="remember"', "Remember this device",
                   "X-Trace-Code"):
        assert needle in LOGIN_PAGE, needle
    assert "src=\"http" not in LOGIN_PAGE and "href=\"http" not in LOGIN_PAGE


def test_code_login_sets_session_cookie_and_unlocks_everything(remote):
    st, h, _ = _login(remote, webui_server.code())
    assert st == 204
    _assert_security_headers(h)
    ck = _set_cookies(h)
    assert set(ck) == {SESSION_COOKIE}
    raw = ck[SESSION_COOKIE][1]
    assert "HttpOnly" in raw and "SameSite=Strict" in raw and "Path=/" in raw
    assert "Max-Age" not in raw and "Secure" not in raw and "Expires" not in raw
    jar = {SESSION_COOKIE: ck[SESSION_COOKIE][0]}
    st, h, body = _req(remote, "/", cookies=jar)
    assert st == 200 and b"TRACE Live" in body
    _assert_security_headers(h)
    assert _req(remote, "/session", cookies=jar)[0] == 204
    assert _req(remote, "/nope", cookies=jar)[0] == 404
    with connect(f"ws://127.0.0.1:{remote}/ws", open_timeout=5,
                 additional_headers={"Cookie": f"{SESSION_COOKIE}={jar[SESSION_COOKIE]}"}) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"


def test_wrong_code_and_lockout_give_the_same_answer(remote):
    wrong = f"{(int(webui_server.code()) + 1) % 1_000_000:06d}"
    first = _login(remote, wrong)
    assert first[0] == 401 and not _set_cookies(first[1])
    for _ in range(4):
        _login(remote, wrong)
    locked = _login(remote, webui_server.code())          # right code, but this IP is locked out
    assert (locked[0], locked[2]) == (first[0], first[2])
    assert not _set_cookies(locked[1])


def test_login_rejects_cross_site_origin(remote):
    st, h, _ = _login(remote, webui_server.code(), ip_headers={"Origin": "http://evil.example"})
    assert st == 403 and not _set_cookies(h)


def test_qr_link_logs_in_once_and_redirect_strips_the_token(remote):
    url = webui_server.qr_url("192.168.1.20", remote)
    assert url.startswith(f"http://192.168.1.20:{remote}/?t=")
    tok = url.split("?t=", 1)[1]
    st, h, _ = _req(remote, f"/?t={tok}&theme=light")
    assert st == 302
    _assert_security_headers(h)
    loc = _hdr(h, "Location")[0]
    assert loc == "/?theme=light" and tok not in loc
    ck = _set_cookies(h)
    assert SESSION_COOKIE in ck and "HttpOnly" in ck[SESSION_COOKIE][1]
    assert _req(remote, loc, cookies={SESSION_COOKIE: ck[SESSION_COOKIE][0]})[0] == 200
    st, h, body = _req(remote, f"/?t={tok}")                # single use
    assert st == 401 and b"Enter <em>code</em>" in body and not _set_cookies(h)
    assert webui_server.qr_url("192.168.1.20", remote) != url    # the Dashboard's QR moves on to a new token
    st, h, _ = _req(remote, "/index.html?t=junk")
    assert st == 401


def test_qr_link_expires(remote, monkeypatch):
    tok = webui_server.qr_url("h", remote).split("?t=", 1)[1]
    real = webui_server._auth._clock
    monkeypatch.setattr(webui_server._auth, "_clock", lambda: real() + webui_auth.QR_TTL_S + 1)
    assert _req(remote, f"/?t={tok}")[0] == 401


def test_remembered_device_survives_a_code_rotation(remote):
    st, h, _ = _login(remote, webui_server.code(), remember=True)
    ck = _set_cookies(h)
    assert st == 204 and set(ck) == {SESSION_COOKIE, DEVICE_COOKIE}
    raw = ck[DEVICE_COOKIE][1]
    assert "Max-Age=2592000" in raw and "HttpOnly" in raw and "SameSite=Strict" in raw and "Secure" not in raw
    old_code = webui_server.code()
    webui_server.stop()                                     # WEB UI off ...
    assert webui_server.start(remote, addr="0.0.0.0")       # ... and on: new code, sessions gone
    assert webui_server.code() != "" and webui_server.code().isdigit()
    if webui_server.code() == old_code:                     # 1 in a million: rotate again
        webui_server.stop()
        assert webui_server.start(remote, addr="0.0.0.0")
    assert _req(remote, "/", cookies={SESSION_COOKIE: ck[SESSION_COOKIE][0]})[0] == 401
    assert _req(remote, "/", cookies={DEVICE_COOKIE: ck[DEVICE_COOKIE][0]})[0] == 200


def test_log_out_all_devices_invalidates_everything(remote, tmp_path):
    _, h1, _ = _login(remote, webui_server.code(), remember=True)
    _, h2, _ = _login(remote, webui_server.code())
    dev = _set_cookies(h1)[DEVICE_COOKIE][0]
    ses = _set_cookies(h2)[SESSION_COOKIE][0]
    assert _req(remote, "/", cookies={DEVICE_COOKIE: dev})[0] == 200
    ws = connect(f"ws://127.0.0.1:{remote}/ws", open_timeout=5,
                 additional_headers={"Cookie": f"{SESSION_COOKIE}={ses}"})
    assert json.loads(ws.recv(timeout=5))["type"] == "hello"
    webui_server.logout_all()
    assert json.loads((tmp_path / "devices.json").read_text(encoding="utf-8")) == {"devices": []}
    assert _req(remote, "/", cookies={DEVICE_COOKIE: dev})[0] == 401
    assert _req(remote, "/", cookies={SESSION_COOKIE: ses})[0] == 401
    with pytest.raises(Exception):                          # the open live feed is closed too
        for _ in range(100):
            ws.recv(timeout=5)
    ws.close()


def test_turning_web_ui_off_invalidates_sessions(remote):
    _, h, _ = _login(remote, webui_server.code())
    ses = _set_cookies(h)[SESSION_COOKIE][0]
    webui_server.stop()
    assert webui_server.code() == "" and webui_server.qr_url("h", remote) == ""
    assert webui_server.start(remote, addr="0.0.0.0")
    assert _req(remote, "/", cookies={SESSION_COOKIE: ses})[0] == 401


def test_localhost_bypass_when_remote_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(webui_server._auth, "_devices_path", tmp_path / "devices.json")
    port = _free_port()
    assert webui_server.start(port, addr="0.0.0.0")
    try:
        st, h, body = _req(port, "/")
        assert st == 200 and b"TRACE Live" in body
        _assert_security_headers(h)
        assert _req(port, "/session")[0] == 204
        with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
            assert json.loads(ws.recv(timeout=5))["type"] == "hello"
    finally:
        webui_server.stop()


def test_local_only_mode_needs_no_code(tmp_path, monkeypatch):
    monkeypatch.setattr(webui_server, "_LOOPBACK_BYPASS", False)   # irrelevant when bound to localhost
    port = _free_port()
    assert webui_server.start(port, addr="127.0.0.1")
    try:
        assert not webui_server.auth_required()
        assert _req(port, "/")[0] == 200
        st, h, _ = _req(port, "/?t=whatever")                # still strips a token from the address bar
        assert st == 302 and _hdr(h, "Location") == ["/"] and not _set_cookies(h)
    finally:
        webui_server.stop()


def test_code_is_never_logged_or_saved(remote, tmp_path, caplog):
    code = webui_server.code()
    _login(remote, code, remember=True)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert code not in p.read_text(encoding="utf-8", errors="ignore")
    assert code not in caplog.text
