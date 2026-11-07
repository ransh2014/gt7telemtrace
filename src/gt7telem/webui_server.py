"""webui_server.py -- optional LAN web dashboard for the GT7 Live Dashboard.

Off by default (see config.WEBUI_ENABLED / WEBUI_PORT). When enabled it serves
one HTML page (webui_page.PAGE) and a read-only WebSocket feed on the SAME
port: a plain GET returns the page, ``/ws`` upgrades to a WebSocket that pushes
compact JSON telemetry frames. Phones, tablets and PCs on the same network open
``http://<this PC's LAN IP>:<port>`` and see speedo / tacho / tyres / timing.

Design rules (mirrors metrics_server.py):
  * localhost-only unless addr="0.0.0.0" is passed (the "ALLOW REMOTE" switch);
  * runs in its own thread + asyncio loop and never blocks the UDP loop or Tk;
  * optional: a busy port or a missing ``websockets`` install makes start()
    return False instead of raising;
  * read-only: anything a client sends is ignored;
  * nothing is sent to the internet.

Testable without a console: start(), then call update() with dicts shaped like
udp.get_snapshot() (or samples from a saved lap file).
"""
import asyncio
import json
import math
import socket
import threading
import time
from collections import deque
from urllib.parse import urlsplit

__all__ = ["start", "stop", "update", "is_running", "lan_ip", "build_frame",
           "DEFAULT_PORT", "MIN_HZ", "MAX_HZ", "MAX_CLIENTS"]

DEFAULT_PORT = 8080
MIN_HZ, MAX_HZ = 5, 60
MAX_CLIENTS = 16
_TRACK_MAX_POINTS = 3000     # world x/z points kept for the mini map
_TRACK_SEND_POINTS = 700     # downsampled count sent to clients
_TRACK_REFRESH_S = 2.0

_lock = threading.Lock()
_state = {"thread": None, "loop": None, "stop": None, "running": False}

# latest frame, shared between the Tk thread (writer) and the server loop (reader)
_frame = {"seq": 0, "json": "", "t": 0.0}
_track = {"pts": deque(maxlen=_TRACK_MAX_POINTS), "version": 0, "key": None}
_laps = []                   # [(lap_number, ms)] of completed laps this session
_last_lap_ms = [None]


def is_running() -> bool:
    return bool(_state["running"])


def lan_ip() -> str | None:
    """This PC's LAN address, or None. A UDP connect() to a private address
    only picks the outgoing interface; no packet is ever sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


def _num(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _clean_text(v, limit=80) -> str:
    """Car/track names are user-typed: strip control characters and cap the
    length. (The page also inserts them with textContent, never innerHTML.)"""
    s = "" if v is None else str(v)
    return "".join(ch for ch in s if ch.isprintable())[:limit]


def build_frame(d: dict, extras: dict | None = None) -> dict:
    """Reduce a telemetry snapshot (+ the Dashboard's computed extras) to the
    compact dict the page needs. extras keys (all optional): connected,
    cur_ms, delta, vs_top, strategy, car, track, packet_loss."""
    ex = extras or {}
    spd = _num(d.get("speed_kmh"))
    ang_y = _num(d.get("ang_y"))
    lat_g = max(-4.0, min(4.0, ang_y * spd / 3.6 / 9.81))
    prev = _frame.get("_spd")
    now = time.monotonic()
    long_g = 0.0
    if prev is not None and now > prev[1]:
        long_g = max(-4.0, min(4.0, (spd - prev[0]) / 3.6 / (now - prev[1]) / 9.81))
        long_g = 0.6 * long_g + 0.4 * _frame.get("_lg", 0.0)   # smooth the 10 Hz diff
    _frame["_spd"] = (spd, now)
    _frame["_lg"] = long_g
    sug = int(_num(d.get("suggested_gear")))
    st = ex.get("strategy") or {}

    def r(v, n=2):
        return None if v is None else round(_num(v), n)

    return {
        "type": "f",
        "ok": bool(ex.get("connected", True)),
        "spd": round(spd, 1),
        "rpm": int(_num(d.get("rpm"))),
        "maxrpm": int(_num(d.get("max_rpm") or d.get("rpm_limiter"))),
        "warnrpm": int(_num(d.get("rpm_warning"))),
        "gear": int(_num(d.get("gear"))),
        "sug": 0 if sug == 15 else sug,    # GT7 sends 15 for "no suggestion"
        "thr": round(_num(d.get("throttle")), 3),
        "brk": round(_num(d.get("brake")), 3),
        "str": round(_num(d.get("steering")), 3),
        "latg": round(lat_g, 2),
        "long": round(long_g, 2),
        "tt": [round(_num(d.get(k)), 1) for k in ("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr")],
        "ts": [round(_num(d.get(k)), 2) for k in ("tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr")],
        "fuel": round(_num(d.get("fuel_remaining")), 2),
        "cap": round(_num(d.get("fuel_capacity")), 2),
        "ev": bool(d.get("is_ev")),
        "lap": int(_num(d.get("lap_number"))),
        "laps": int(_num(d.get("total_laps"))),
        "cur": ex.get("cur_ms"),
        "last": d.get("last_lap_ms"),
        "best": d.get("best_lap_ms"),
        "delta": r(ex.get("delta")),
        "top": r(ex.get("vs_top")),
        "fl": r(st.get("laps_of_fuel"), 1),
        "fpl": r(st.get("fuel_per_lap"), 2),
        "pit": st.get("pit_by_lap"),
        "spare": r(st.get("spare_laps"), 1),
        "stop": st.get("stop_needed"),
        "stint": st.get("stint_age"),
        "loss": r(st.get("pace_loss_s")),
        "trend": r(st.get("trend_s_per_lap")),
        "tyre": st.get("tyre_state"),
        "car": _clean_text(ex.get("car")),
        "track": _clean_text(ex.get("track")),
        "pos": round(_num(d.get("track_position")), 1),
        "x": round(_num(d.get("world_x")), 1),
        "z": round(_num(d.get("world_z")), 1),
        "hdg": round(_num(d.get("heading")), 3),
        "tcs": bool(d.get("tcs_active")),
        "asm": bool(d.get("asm_active")),
        "lim": bool(d.get("rev_limit_alert")),
        "loss_pkt": r(ex.get("packet_loss"), 1),
        "hist": list(_laps[-8:]),
    }


def _note_lap_and_track(d: dict, ex: dict) -> None:
    """Keep the lap history and the mini-map outline (server side, so a page
    that connects mid-session still gets the whole track)."""
    last = d.get("last_lap_ms")
    if last and last > 0 and last != _last_lap_ms[0]:
        _laps.append([int(_num(d.get("lap_number"))) - 1, int(last)])
        del _laps[:-50]
    _last_lap_ms[0] = last
    key = (ex.get("car"), ex.get("track"))
    if key != _track["key"]:
        _track["pts"].clear()
        _laps.clear()
        _track["key"] = key
        _track["version"] += 1
    x, z = _num(d.get("world_x")), _num(d.get("world_z"))
    if (x or z) and _num(d.get("speed_kmh")) > 1:
        pts = _track["pts"]
        if pts and (abs(x - pts[-1][0]) > 80 or abs(z - pts[-1][1]) > 80):
            pts.clear()      # teleport (pit / restart): drop the stale trail
            _track["version"] += 1
        if not pts or abs(x - pts[-1][0]) > 1.0 or abs(z - pts[-1][1]) > 1.0:
            pts.append((round(x, 1), round(z, 1)))


def update(d: dict, extras: dict | None = None) -> None:
    """Push one telemetry snapshot. No-op unless start() succeeded, so call
    sites needn't check is_running(). Cheap enough for the Tk poll thread."""
    if not _state["running"]:
        return
    try:
        ex = extras or {}
        _note_lap_and_track(d, ex)
        js = json.dumps(build_frame(d, ex), separators=(",", ":"))
        _frame["json"] = js
        _frame["t"] = time.monotonic()
        _frame["seq"] += 1
    except Exception:
        pass   # a bad frame must never reach the Dashboard


def _track_message() -> str:
    pts = list(_track["pts"])
    step = max(1, len(pts) // _TRACK_SEND_POINTS)
    return json.dumps({"type": "track", "v": _track["version"], "pts": pts[::step]}, separators=(",", ":"))


def _clamp_hz(hz) -> int:
    try:
        hz = int(hz)
    except (TypeError, ValueError):
        hz = 30
    return max(MIN_HZ, min(MAX_HZ, hz))


async def _serve(port, addr, hz, ready, result):
    import http

    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    from .webui_page import PAGE

    page = PAGE.encode("utf-8")
    interval = 1.0 / _clamp_hz(hz)
    clients = set()

    def respond(status, body, ctype="text/plain; charset=utf-8"):
        h = Headers([
            ("Content-Type", ctype), ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy",
             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
             "connect-src 'self' ws: wss:; font-src data:; img-src 'self' data:; manifest-src 'self'"),
        ])
        return Response(status.value, status.phrase, h, body)

    async def process_request(connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            origin = request.headers.get("Origin")
            host = request.headers.get("Host", "")
            # a web page on another site must not be able to read the feed
            if origin and urlsplit(origin).netloc.lower() != host.lower():
                return respond(http.HTTPStatus.FORBIDDEN, b"Forbidden\n")
            if len(clients) >= MAX_CLIENTS:
                return respond(http.HTTPStatus.SERVICE_UNAVAILABLE, b"Too many viewers\n")
            return None
        if path in ("/", "/index.html"):
            return respond(http.HTTPStatus.OK, page, "text/html; charset=utf-8")
        return respond(http.HTTPStatus.NOT_FOUND, b"Not found\n")

    async def handler(ws):
        clients.add(ws)
        seq, tkey, tsent, idle = -1, None, 0.0, False
        try:
            await ws.send(json.dumps({"type": "hello", "hz": _clamp_hz(hz)}))

            async def drain():          # read-only: discard anything the client sends
                async for _ in ws:
                    pass
            reader = asyncio.ensure_future(drain())
            try:
                while not reader.done():
                    now = time.monotonic()
                    key = (_track["version"], len(_track["pts"]) // 20)
                    if key != tkey and (tkey is None or key[0] != tkey[0] or now - tsent > _TRACK_REFRESH_S):
                        await ws.send(_track_message())
                        tkey, tsent = key, now
                    if _frame["seq"] != seq and _frame["json"]:
                        seq, idle = _frame["seq"], False
                        # a slow client just skips frames: we always send the newest one
                        await asyncio.wait_for(ws.send(_frame["json"]), timeout=2.0)
                    elif not idle and now - _frame["t"] > 1.5:
                        idle = True
                        await ws.send('{"type":"idle"}')
                    await asyncio.sleep(interval)
            finally:
                reader.cancel()
        except Exception:
            pass
        finally:
            clients.discard(ws)

    try:
        server = await serve(handler, addr, port, process_request=process_request,
                             max_size=1024, ping_interval=20, ping_timeout=20)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        ready.set()
        return
    stop = asyncio.Event()
    _state["stop"] = stop
    result["ok"] = True
    ready.set()
    await stop.wait()
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=3)
    except Exception:
        pass


def start(port: int = DEFAULT_PORT, addr: str = "127.0.0.1", hz: int = 30) -> bool:
    """Start the web UI on `port`. Safe to call repeatedly (no-op once running).
    Returns True if the server is up afterwards, False if it could not start
    (port busy, ``websockets`` missing/too old, anything else). Binds to
    localhost by default; pass addr="0.0.0.0" to let other devices connect --
    there is no password, so anyone on that network could then read live
    telemetry."""
    with _lock:
        if _state["running"]:
            return True
        try:
            import websockets.asyncio.server  # noqa: F401  (websockets >= 13)
        except Exception:
            return False
        ready = threading.Event()
        result: dict = {}

        def run():
            loop = asyncio.new_event_loop()
            _state["loop"] = loop
            try:
                loop.run_until_complete(_serve(port, addr, hz, ready, result))
            except Exception as e:
                result.setdefault("error", f"{type(e).__name__}: {e}")
            finally:
                ready.set()
                _state["running"] = False
                try:
                    loop.close()
                except Exception:
                    pass

        t = threading.Thread(target=run, name="webui", daemon=True)
        _state["thread"] = t
        t.start()
        ready.wait(timeout=5)
        if not result.get("ok"):
            _state.update(thread=None, loop=None, stop=None, running=False)
            last_error[0] = result.get("error")
            return False
        last_error[0] = None
        _state["running"] = True
        _frame.update(seq=0, json="", t=0.0)
        return True


last_error = [None]


def stop() -> None:
    """Stop the server and free the port (so toggling off mid-session works)."""
    with _lock:
        loop, ev, t = _state["loop"], _state["stop"], _state["thread"]
        _state["running"] = False
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass
        if t is not None:
            t.join(timeout=5)
        _state.update(thread=None, loop=None, stop=None)
        _track["pts"].clear()
        _track["key"] = None
        _laps.clear()
        _last_lap_ms[0] = None
        _frame.update(seq=0, json="", t=0.0)
