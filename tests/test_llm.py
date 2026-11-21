"""Local AI wording. No real model is ever loaded or downloaded: a fake backend returns canned text, downloads come from
a local HTTP server on 127.0.0.1 (the suite refuses any other network access), and processes are never started."""
import asyncio
import hashlib
import http.server
import io
import json
import re
import threading
import zipfile
from pathlib import Path

import pytest
from test_coach import _costly
from test_corners import _lap

from gt7telem import analysis_tools, coach, config, labels, llm
from gt7telem.core import registry

TIP = "Brake 14 m later at T4."


# ---- settings and pinned downloads -------------------------------------------------------------------------------
def test_local_ai_is_off_by_default_and_a_shared_setting():
    assert config._DEFAULTS["LLM_ENABLED"] is False and config.LLM_ENABLED is False
    assert ("LLM_ENABLED", labels.PREF_LOCAL_AI) in registry.SETTINGS
    src = Path(config.__file__).parent
    assert "L.PREF_LOCAL_AI" in (src / "dashboard.py").read_text(encoding="utf-8")


def test_pinned_downloads_are_https_and_carry_a_sha256():
    for url in (llm.MODEL_URL, llm.SERVER_URL):
        assert url.startswith("https://")
    for sha in (llm.MODEL_SHA256, llm.SERVER_SHA256):
        assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert llm.MODEL_COMMIT in llm.MODEL_URL and llm.SERVER_TAG in llm.SERVER_URL      # pinned, never "latest"/"main"
    assert llm.MODEL_URL.endswith("Q4_K_M.gguf") and "1B-Instruct" in llm.MODEL_URL


# ---- the PC check -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cores,gb,level", [
    (2, 16, "red"), (3, 32, "red"), (8, 4, "red"), (8, 5.9, "red"),
    (4, 6, "amber"), (8, 7.9, "amber"), (6, None, "amber"),
    (4, 8, "green"), (8, 16, "green"), (16, 64, "green"),
])
def test_spec_thresholds(cores, gb, level):
    assert llm.rate_specs(cores, gb).level == level


def test_check_specs_uses_injected_values_and_unknown_ram():
    c, gb, r = llm.check_specs(cores=6, ram_bytes=16 * 1024 ** 3)
    assert (c, round(gb), r.level) == (6, 16, "green")
    assert llm.check_specs(cores=8, read_ram=lambda: None)[2].level == "amber"


def test_ram_readers():
    assert llm.read_ram_bytes("darwin", run=lambda *a, **k: "17179869184\n") == 17179869184
    assert llm.read_ram_bytes("darwin", run=lambda *a, **k: 1 / 0) is None          # a failing sysctl is just "unknown"
    assert llm.read_ram_bytes("plan9") is None
    assert (llm.read_ram_bytes() or 1) > 0


def test_free_disk_walks_up_to_an_existing_folder(tmp_path):
    assert llm.free_disk_gb(tmp_path / "not" / "yet" / "there") > 0


# ---- the guard ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tip,reworded", [
    (TIP, "Try braking 14 m later at T4."),
    (TIP, '"Brake 14 m later at T4, you can do it!"'),
    ("You're lifting 0.3 s early at T7: back on the throttle 12 m sooner.", "You lift 0.3 s early at T7, so get back on the throttle 12 m sooner."),
    ("Carry 6 km/h more through the middle of T2.", "Carry 6 km/h more through the middle of T2, nice and smooth."),
])
def test_guard_accepts_wording_that_keeps_every_fact(tip, reworded):
    out, why = llm.guard(tip, reworded)
    assert out and why == "" and "\"" not in out[:1]


@pytest.mark.parametrize("bad,reason", [
    ("Try braking 15 m later at T4.", "numbers"),               # a number changed
    ("Try braking 14 m later at T5.", "numbers"),               # a corner changed
    ("Try braking 14 m earlier at T4.", "numbers"),             # a direction flipped
    ("Try braking 14 m later at T4, around 20 km/h slower.", "numbers"),   # an extra number
    ("Try braking later at T4.", "numbers"),                    # a number dropped
    ("Brake fourteen metres later at T4.", "numbers"),          # spelled out
    ("", "empty"), ("   ", "empty"), (None, "no answer"),
    ("Try braking 14 m later at T4." + " Keep going." * 40, "too long"),
    ("Try braking 14 m later at T4.\nGood luck!", "more than one line"),
    ("Try braking 14 m later at T4. Then breathe.", "more than one sentence"),
    ("在T4处刺车晚14米", "not English"),
    ("Frenar 14 m mas tarde en T4", "numbers"),
])
def test_guard_rejects_everything_else(bad, reason):
    out, why = llm.guard(TIP, bad)
    assert out is None and why, bad
    if reason == "empty":
        assert why == "empty"


def test_facts_reads_numbers_corners_and_directions():
    n, c, d = llm.facts("Brake 14.0 m later at T4 and carry 3 km/h more, sooner at t10.")
    assert n == {"14", "3"} and c == {"T4", "T10"} and d == {"later", "more", "sooner"}


# ---- one tip at a time, with a fake backend ----------------------------------------------------------------------
class Fake:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.closed = False

    def reword(self, tip):
        self.calls.append(tip)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    def close(self):
        self.closed = True


def test_worder_uses_good_wording_and_falls_back_otherwise():
    fb = Fake(["Try braking 14 m later at T4.", "Try braking 99 m later at T4."])
    w = llm.TipWorder(fb)
    good, bad = w.reword(TIP), w.reword(TIP)
    assert good.ai and good.text == "Try braking 14 m later at T4."
    assert not bad.ai and bad.text == TIP and "changed" in bad.why


def test_worder_never_calls_the_model_when_off():
    fb = Fake([])
    w = llm.TipWorder(fb, enabled=lambda: False)
    r = w.reword(TIP)
    assert (r.text, r.ai, r.why) == (TIP, False, "off") and fb.calls == []


def test_a_crashing_backend_falls_back_and_is_not_retried():
    w = llm.TipWorder(Fake([RuntimeError("boom")]))
    r = w.reword(TIP)
    assert r.text == TIP and not r.ai and "boom" in r.why
    assert not w.ready()


def test_backend_is_created_lazily_once():
    made = []

    def factory():
        made.append(1)
        return Fake(["Try braking 14 m later at T4."] * 3)
    w = llm.TipWorder(backend_factory=factory)
    assert made == []
    w.reword(TIP), w.reword(TIP)
    assert made == [1]
    none_w = llm.TipWorder(backend_factory=lambda: None)
    assert none_w.reword(TIP).why == "no backend" and not none_w.ready()
    w.close()


def real_tips():
    return coach.analyse(_costly(_lap(brake_shift=-30), -140, -30), _lap())


def test_describe_with_ai_header_only_when_a_tip_passed():
    tips = real_tips()
    assert tips
    plain = coach.describe(tips)
    assert llm.describe_with_ai(tips, None) == (plain, False)
    nothing = llm.TipWorder(Fake(["totally different 5"] * len(tips)))
    assert llm.describe_with_ai(tips, nothing) == (plain, False)           # all rejected: exactly the rules text
    ok = llm.TipWorder(Fake([t.text.replace("Brake", "Try braking") for t in tips]))
    text, used = llm.describe_with_ai(tips, ok)
    assert used and text.startswith(llm.HEADER_AI) and "Try braking" in text and "(+" in text
    mixed = llm.TipWorder(Fake([tips[0].text.replace("Brake", "Try braking")] + ["nope 1"] * (len(tips) - 1)))
    text, used = llm.describe_with_ai(tips, mixed)
    assert used and (len(tips) == 1 or tips[-1].text in text)             # a failed tip keeps the rules wording


def test_reports_use_the_worder_only_when_given_one():
    slow = _costly(_lap(throttle_shift=40), 20, 60)
    base = analysis_tools.coach_report(slow, _lap())
    assert llm.HEADER_AI not in base
    tips = coach.analyse(slow, _lap(), max_tips=5)
    w = llm.TipWorder(Fake([t.text.replace("You're", "Heads up, you're") for t in tips]))
    assert llm.HEADER_AI in analysis_tools.coach_report(slow, _lap(), w)
    assert analysis_tools.lap_tool("coach", slow, _lap())[1] == base


def test_reword_async_logs_only_what_passed_and_never_without_a_pass():
    tips = real_tips()
    log = []
    w = llm.TipWorder(Fake([t.text.replace("Brake", "Try braking") for t in tips]))
    t = llm.reword_async(tips, log.append, w)
    t.join(5)
    assert len(log) == 1 and log[0].startswith(llm.HEADER_AI)
    log.clear()
    llm.reword_async(tips, log.append, llm.TipWorder(Fake(["bad 9"] * len(tips)))).join(5)
    assert log == []
    assert llm.reword_async([], log.append, w) is None
    off = llm.TipWorder(Fake([]), enabled=lambda: False)
    assert llm.reword_async(tips, log.append, off) is None                 # off: not even a thread


# ---- backends -----------------------------------------------------------------------------------------------------
class FakeLlama:
    def __init__(self, **kw):
        self.kw = kw

    def create_chat_completion(self, messages, temperature, max_tokens):
        self.last = (messages, temperature, max_tokens)
        return {"choices": [{"message": {"content": "Try braking 14 m later at T4."}}]}


def test_llama_cpp_backend_settings(tmp_path):
    b = llm.LlamaCppBackend(tmp_path / "m.gguf", threads=3, llama_cls=FakeLlama)
    assert b._llm.kw == {"model_path": str(tmp_path / "m.gguf"), "n_ctx": 1024, "n_threads": 3, "verbose": False}
    assert b.reword(TIP) == "Try braking 14 m later at T4."
    msgs, temp, mx = b._llm.last
    assert msgs[0]["role"] == "system" and "ONE short" in msgs[0]["content"] and msgs[1] == {"role": "user", "content": TIP}
    assert (temp, mx) == (0.2, 80)
    assert llm.n_threads(8) == 6 and llm.n_threads(4) == 3 and llm.n_threads(1) == 1 and llm.n_threads(2) == 1


def test_llama_cpp_backend_times_out():
    class Slow(FakeLlama):
        def create_chat_completion(self, *a, **k):
            threading.Event().wait(2)
            return {}
    b = llm.LlamaCppBackend(Path("x"), llama_cls=Slow, timeout=0.05)
    assert b.reword(TIP) is None


class Handler(http.server.BaseHTTPRequestHandler):
    payload = b""
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(Handler.payload)))
            self.end_headers()
            self.wfile.write(Handler.payload)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Handler.seen.append(json.loads(self.rfile.read(n)))
        body = json.dumps({"choices": [{"message": {"content": "Try braking 14 m later at T4."}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    Handler.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


class FakeProc:
    def __init__(self, args, **kw):
        self.args, self.terminated = args, False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_server_backend_talks_to_127_0_0_1_only(tmp_path, server):
    procs = []
    b = llm.ServerBackend(tmp_path / "llama-server.exe", tmp_path / "m.gguf", threads=4, port=server.server_address[1],
                          popen=lambda a, **k: procs.append(FakeProc(a, **k)) or procs[-1])
    assert procs and procs[0].args[:1] == [str(tmp_path / "llama-server.exe")]
    assert "--host" in b.argv and b.argv[b.argv.index("--host") + 1] == "127.0.0.1"
    assert b.argv[b.argv.index("-c") + 1] == "1024" and b.argv[b.argv.index("-t") + 1] == "4"
    assert b.reword(TIP) == "Try braking 14 m later at T4."
    sent = Handler.seen[0]
    assert sent["temperature"] == 0.2 and sent["max_tokens"] == 80 and sent["messages"][1]["content"] == TIP
    b.close()
    assert procs[0].terminated
    with pytest.raises(ValueError):
        llm._local("http://example.com/v1")


def test_server_that_never_starts_is_an_error_not_a_hang(tmp_path):
    class Dead(FakeProc):
        def poll(self):
            return 1
    with pytest.raises(RuntimeError):
        llm.ServerBackend(tmp_path / "s.exe", tmp_path / "m.gguf", port=9, popen=lambda a, **k: Dead(a), sleep=lambda s: None)


# ---- availability: three backends, Store build hides the download --------------------------------------------------
def test_backend_choice():
    assert llm.backend_for(llama_cpp=True, frozen=False, store=False).kind == "llama-cpp"
    assert llm.backend_for(llama_cpp=False, frozen=True, store=False, platform="win32").kind == "server"
    assert llm.backend_for(llama_cpp=False, frozen=False, store=False).kind == "none"
    mac = llm.backend_for(llama_cpp=False, frozen=True, store=False, platform="darwin")
    assert mac.kind == "none" and mac.reason


def test_store_build_hides_the_downloadable_runtime():
    be = llm.backend_for(llama_cpp=False, frozen=True, store=True, platform="win32")
    assert be.kind == "none" and "Microsoft Store" in be.reason
    st = llm.status(Path("nowhere-at-all"), be)
    assert not st.available and not st.ready
    w = llm.build_warning(st, 8, 16.0, llm.rate_specs(8, 16.0), 50.0)
    assert not w.can_enable and "Microsoft Store" in w.body


def test_pip_install_hint_when_nothing_is_available():
    be = llm.backend_for(llama_cpp=False, frozen=False, store=False)
    assert "gt7tracetelem[ai]" in be.reason


# ---- verified downloads --------------------------------------------------------------------------------------------
def serve(server, data):
    Handler.payload = data
    return f"http://127.0.0.1:{server.server_address[1]}/file.bin"


def test_download_verifies_the_checksum(tmp_path, server):
    data = b"x" * 3_000_000
    url = serve(server, data)
    seen = []
    p = llm.download_verified(url, tmp_path / "a" / "file.bin", hashlib.sha256(data).hexdigest(), allow_local=True,
                              progress=lambda d, t: seen.append((d, t)))
    assert p.read_bytes() == data and seen[-1] == (len(data), len(data)) and not list(p.parent.glob("*.part"))


def test_a_tampered_download_is_deleted_and_never_used(tmp_path, server):
    url = serve(server, b"tampered bytes")
    dest = tmp_path / "model.gguf"
    with pytest.raises(llm.DownloadError, match="checksum"):
        llm.download_verified(url, dest, hashlib.sha256(b"the real bytes").hexdigest(), allow_local=True)
    assert not dest.exists() and not list(tmp_path.glob("*.part"))


def test_downloads_must_be_https(tmp_path):
    with pytest.raises(llm.DownloadError, match="https"):
        llm.download_verified("http://example.com/m.gguf", tmp_path / "m", "0" * 64)
    with pytest.raises(llm.DownloadError, match="https"):
        llm.download_verified("http://127.0.0.1:9/m.gguf", tmp_path / "m", "0" * 64)       # local http only when allowed
    with pytest.raises(llm.DownloadError, match="https"):
        llm.download_verified("file:///etc/passwd", tmp_path / "m", "0" * 64, allow_local=True)


def test_download_can_be_cancelled(tmp_path, server):
    data = b"y" * 2_000_000
    url = serve(server, data)
    with pytest.raises(llm.DownloadError, match="cancelled"):
        llm.download_verified(url, tmp_path / "m", hashlib.sha256(data).hexdigest(), allow_local=True, chunk=1000, cancel=lambda: True)
    assert not (tmp_path / "m").exists() and not list(tmp_path.glob("*.part"))


def test_unreachable_server_is_a_download_error(tmp_path):
    with pytest.raises(llm.DownloadError, match="failed"):
        llm.download_verified("http://127.0.0.1:9/x", tmp_path / "m", "0" * 64, allow_local=True)


def test_zip_slip_is_refused(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../escaped.txt", "x")
    with pytest.raises(llm.DownloadError, match="unsafe"):
        llm.extract_zip_safely(z, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


# ---- the enable flow: nothing downloads before a yes ---------------------------------------------------------------
class Recorder:
    def __init__(self, write=True):
        self.calls, self.write = [], write

    def __call__(self, url, dest, sha, **kw):
        self.calls.append((url, Path(dest).name, sha))
        if self.write:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            if str(dest).endswith(".zip"):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    z.writestr("bin/llama-server.exe", "MZ")
                Path(dest).write_bytes(buf.getvalue())
            else:
                Path(dest).write_bytes(b"gguf")
        return Path(dest)


GOOD_SPECS = lambda: (8, 16.0, llm.rate_specs(8, 16.0))  # noqa: E731


def test_asking_to_enable_downloads_nothing(tmp_path):
    dl = Recorder()
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used before confirmation"))  # noqa: E731
    flow = llm.EnableFlow(tmp_path, llm.Backend("llama-cpp"), GOOD_SPECS, downloader=dl, opener=boom)
    w = flow.request()
    assert dl.calls == [] and not llm.model_path(tmp_path).exists()
    assert w.can_enable and w.rating.level == "green" and 700 < w.download_mb < 900
    for must in ("OFF by default", "modest PC", "GREEN", "Free disk space needed", "1.5 GB", "Recommended PC", "Built with Llama",
                 "4 cores, 6 GB RAM", "8 GB RAM", "16 GB RAM", "never runs while you drive"):
        assert must in w.body, must


def test_warning_shows_red_specs_and_blocks_on_no_disk(tmp_path):
    flow = llm.EnableFlow(tmp_path, llm.Backend("llama-cpp"), lambda: (2, 4.0, llm.rate_specs(2, 4.0)), downloader=Recorder())
    assert flow.request().rating.level == "red" and "RED" in flow.request().body
    st = llm.status(tmp_path, llm.Backend("llama-cpp"))
    w = llm.build_warning(st, 8, 16.0, llm.rate_specs(8, 16.0), 0.2)
    assert not w.can_enable and "Not enough free disk space" in w.body


def test_confirm_downloads_the_pinned_model_once_and_marks_it_ready(tmp_path):
    dl = Recorder()
    flow = llm.EnableFlow(tmp_path, llm.Backend("llama-cpp"), GOOD_SPECS, downloader=dl)
    st = flow.confirm()
    assert dl.calls == [(llm.MODEL_URL, llm.MODEL_FILE, llm.MODEL_SHA256)] and st.ready
    flow.confirm()                                                      # already there: no second download
    assert len(dl.calls) == 1
    assert llm.EnableFlow(tmp_path, llm.Backend("llama-cpp"), GOOD_SPECS, downloader=dl).request().download_mb == 0


def test_server_backend_downloads_model_and_runtime_then_extracts(tmp_path):
    dl = Recorder()
    flow = llm.EnableFlow(tmp_path, llm.Backend("server"), GOOD_SPECS, downloader=dl)
    st = flow.confirm()
    assert [c[0] for c in dl.calls] == [llm.MODEL_URL, llm.SERVER_URL] and dl.calls[1][2] == llm.SERVER_SHA256
    assert st.ready and llm.find_server_exe(tmp_path).name == "llama-server.exe"
    assert not (tmp_path / "runtime" / llm.SERVER_ZIP).exists()           # the zip is not kept


def test_a_runtime_without_the_server_is_rejected(tmp_path):
    def bad(url, dest, sha, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".zip"):
            with zipfile.ZipFile(dest, "w") as z:
                z.writestr("readme.txt", "nothing")
        else:
            Path(dest).write_bytes(b"gguf")
        return Path(dest)
    flow = llm.EnableFlow(tmp_path, llm.Backend("server"), GOOD_SPECS, downloader=bad)
    with pytest.raises(llm.DownloadError, match="llama-server"):
        flow.confirm()
    assert llm.find_server_exe(tmp_path) is None


def test_confirm_refuses_when_no_backend(tmp_path):
    dl = Recorder()
    flow = llm.EnableFlow(tmp_path, llm.Backend("none", "no thanks"), GOOD_SPECS, downloader=dl)
    with pytest.raises(llm.DownloadError, match="no thanks"):
        flow.confirm()
    assert dl.calls == []


def test_download_failure_leaves_rules_mode(tmp_path):
    def fail(*a, **k):
        raise llm.DownloadError("offline")
    flow = llm.EnableFlow(tmp_path, llm.Backend("llama-cpp"), GOOD_SPECS, downloader=fail)
    with pytest.raises(llm.DownloadError):
        flow.confirm()
    assert not llm.status(tmp_path, llm.Backend("llama-cpp")).ready and config.LLM_ENABLED is False


def test_remove_model_deletes_everything(tmp_path):
    base = tmp_path / "llm"
    llm.EnableFlow(base, llm.Backend("server"), GOOD_SPECS, downloader=Recorder()).confirm()
    assert base.exists()
    assert llm.remove_model(base) and not base.exists()
    assert llm.remove_model(base)                                       # nothing left: still fine


def test_create_backend_needs_the_files(tmp_path):
    assert llm.create_backend(tmp_path, llm.Backend("none")) is None
    assert llm.create_backend(tmp_path, llm.Backend("llama-cpp")) is None   # no model file yet
    assert llm.create_backend(tmp_path, llm.Backend("server")) is None


# ---- the terminal UI: switching it on asks first -------------------------------------------------------------------
def test_tui_switch_shows_the_warning_and_downloads_nothing(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from textual.widgets import Switch

    from gt7telem.tui import app as tui
    saved, downloads = [], []
    monkeypatch.setattr(tui.config, "save", lambda **kw: saved.append(kw) or True)
    monkeypatch.setattr(tui.udp, "get_snapshot", lambda: {})
    monkeypatch.setattr(tui.udp, "is_connected", lambda: False)
    monkeypatch.setattr("gt7telem.trackdetect.fetch_shared", lambda *a, **k: None)
    monkeypatch.setattr(llm, "download_verified", lambda *a, **k: downloads.append(a))
    monkeypatch.setattr(llm, "llm_dir", lambda: tmp_path / "llm")
    monkeypatch.setattr(llm, "backend_for", lambda **k: llm.Backend("llama-cpp"))

    async def go():
        app = tui.TraceApp()
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.press("5")
            await pilot.pause()
            sw = app.query_one("#sw-LLM_ENABLED", Switch)
            assert sw.value is False
            sw.value = True
            await pilot.pause(0.3)
            assert isinstance(app.screen, tui.ConfirmScreen)
            await pilot.click("#no")
            await pilot.pause(0.2)
            assert sw.value is False and downloads == []
            assert not any(k.get("LLM_ENABLED") for k in saved)
    asyncio.run(go())
