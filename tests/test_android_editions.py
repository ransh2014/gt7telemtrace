"""TRACE Lite and TRACE AI: two Android APKs from one codebase. The Gradle build and the CI workflow only run on GitHub, so
these checks pin down what must stay true about them (names the website depends on, the flavors, that the AI part is
isolated from Lite, that nothing can run while telemetry arrives) and run the model-fetch script against a local server."""
import hashlib
import http.server
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"
APP = ANDROID / "app"


def _read(p):
    return Path(p).read_text(encoding="utf-8")


def test_gradle_defines_the_two_editions():
    g = _read(APP / "build.gradle.kts")
    assert 'applicationId = "app.trace.gt7"' in g                           # Lite keeps its id: updates still work
    assert 'create("lite")' in g and 'create("ai")' in g
    assert 'applicationIdSuffix = ".ai"' in g and g.count("applicationIdSuffix") == 1   # only AI is renamed
    assert 'abiFilters += "arm64-v8a"' in g
    assert 'noCompress += "gguf"' in g                                      # the model is stored, not compressed
    assert "useLegacyPackaging = true" in g                                 # llama-server runs from the native library folder
    assert "TRACE_KEYSTORE" in g and g.count("signingConfig = ") == 1       # one signing config for both editions
    assert "trace-signing" not in g


def test_app_name_per_edition():
    assert 'android:label="@string/app_name"' in _read(APP / "src" / "main" / "AndroidManifest.xml")
    assert '>"TRACE"<' in _read(APP / "src" / "main" / "res" / "values" / "strings.xml")     # generated, Lite's name
    assert ">TRACE AI<" in _read(APP / "src" / "ai" / "res" / "values" / "ai_strings.xml")


def test_workflow_builds_both_apks_with_the_names_the_website_uses():
    w = _read(ROOT / ".github" / "workflows" / "build-android.yml")
    assert "assembleLiteRelease" in w and "assembleAiRelease" in w
    assert "gt7telem-android.apk" in w and "gt7telem-android-ai.apk" in w
    assert w.count("gt7telem-android.apk") >= 3                              # copied, uploaded and attached
    assert "needs: lite" in w                                                # an AI failure can never block the Lite release
    for secret in ("ANDROID_KEYSTORE_B64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS", "ANDROID_KEY_PASSWORD"):
        assert w.count(secret) == 2, secret                                  # same signing secrets in both jobs
    assert w.count("mapping.txt") == 2 and "liteRelease" in w and "aiRelease" in w    # R8 mapping kept for both
    assert "trace-signing" not in w
    assert "fetch_ai_assets.py" in w and "libllama_server.so" in w and "llm.SERVER_TAG" in w   # same llama.cpp tag as the desktop
    assert "aarch64" in w                                                    # the built server is checked to be arm64
    assert "./gradlew :telemetry:test" in w                                  # the golden tests still gate the release


def test_the_ai_code_lives_only_in_the_ai_edition():
    lite = _read(APP / "src" / "lite" / "kotlin" / "app" / "trace" / "AiEdition.kt")
    ai = _read(APP / "src" / "ai" / "kotlin" / "app" / "trace" / "AiEdition.kt")
    for api in ("object AiWording", "fun ready(ctx: Context)", "fun <T> withRewriter(", "fun AiGate(", "fun AiAbout("):
        assert api in lite and api in ai, api                                # same API in both, so main code never branches
    assert "EDITION = false" in lite and "EDITION = true" in ai
    for banned in ("gguf", "ProcessBuilder", "llama", "LocalHttp", "assets"):
        assert banned not in lite.replace("AiWording", ""), f"Lite must not carry {banned}"
    main = "\n".join(_read(p) for p in (APP / "src" / "main").rglob("*.kt"))
    assert "ProcessBuilder" not in main and "gguf" not in main               # nothing AI in the shared code
    assert not (APP / "src" / "lite" / "jniLibs").exists() and not (APP / "src" / "lite" / "assets").exists()


def test_ai_never_runs_while_telemetry_arrives():
    ai = _read(APP / "src" / "ai" / "kotlin" / "app" / "trace" / "AiEdition.kt")
    assert ai.count("TelemetryGate.receiving") >= 3                          # before starting, inside each reword, in ready()
    assert "TelemetryGate.receiving = ok" in _read(APP / "src" / "main" / "kotlin" / "app" / "trace" / "LiveController.kt")
    live = _read(APP / "src" / "main" / "kotlin" / "app" / "trace" / "LiveController.kt")
    assert "AiWording" not in live and "LlmGuard" not in live                # the live path never touches the model


def test_every_reworded_tip_goes_through_the_shared_guard():
    ai = _read(APP / "src" / "ai" / "kotlin" / "app" / "trace" / "AiEdition.kt")
    assert "LlmGuard.Rewriter" in ai and "LlmGuard.messagesJson" in ai
    tools = _read(ANDROID / "telemetry" / "src" / "main" / "kotlin" / "app" / "trace" / "telemetry" / "AnalysisTools.kt")
    assert "LlmGuard.describeWithAi" in tools and "Coach.describe(Coach.analyse" not in tools.replace("describeWithAi(Coach.analyse", "")


def test_no_ai_switch_on_android():
    """TRACE AI has the model, TRACE Lite doesn't: there is no setting for it."""
    live = _read(APP / "src" / "main" / "kotlin" / "app" / "trace" / "LiveScreen.kt")
    assert "Check(stringResource(R.string.pref_local_ai)" not in live and "LLM_ENABLED" not in live and "AiWording" not in live


def test_local_http_is_loopback_only():
    h = _read(ANDROID / "telemetry" / "src" / "main" / "kotlin" / "app" / "trace" / "telemetry" / "LocalHttp.kt")
    assert 'getByName("127.0.0.1")' in h
    assert "import java.net.URL" not in h and "openConnection" not in h and "HttpURLConnection(" not in h


def test_the_build_inputs_are_not_committed():
    ig = _read(ROOT / ".gitignore")
    assert "android/app/src/ai/assets/" in ig and "android/app/src/ai/jniLibs/" in ig
    assert not (APP / "src" / "ai" / "assets").exists() and not (APP / "src" / "ai" / "jniLibs").exists()


def test_the_warning_screen_has_the_phone_check_and_the_llama_notice():
    ai = _read(APP / "src" / "ai" / "kotlin" / "app" / "trace" / "AiEdition.kt")
    assert "LlmGuard.ratePhone" in ai and "RECOMMENDED_PHONES" in ai and "LLAMA_NOTICE" in ai and "ai_warning_ack" in ai
    assert "rating.level == \"red\"" in ai                                  # on red it says so (and the text points to Lite)
    launcher = _read(APP / "src" / "main" / "kotlin" / "app" / "trace" / "Launcher.kt")
    assert "AiAbout(a)" in launcher                                          # "Built with Llama" in the AI app's About line


# ---- tools/fetch_ai_assets.py, against a local server ------------------------------------------------------------
@pytest.fixture()
def fetch_mod():
    sys.path.insert(0, str(ROOT / "tools"))
    import fetch_ai_assets
    yield fetch_ai_assets
    sys.path.remove(str(ROOT / "tools"))


class _H(http.server.BaseHTTPRequestHandler):
    payload = b""
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        _H.hits += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(_H.payload)))
        self.end_headers()
        self.wfile.write(_H.payload)


@pytest.fixture()
def srv():
    _H.hits = 0
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def test_fetch_downloads_verifies_and_caches(fetch_mod, srv, tmp_path):
    data = b"model bytes" * 1000
    _H.payload = data
    url = f"http://127.0.0.1:{srv.server_address[1]}/m.gguf"
    sha = hashlib.sha256(data).hexdigest()
    quiet = lambda *a: None  # noqa: E731
    p = fetch_mod.fetch(tmp_path, url, sha, "m.gguf", allow_local=True, say=quiet)
    assert p.read_bytes() == data and _H.hits == 1
    fetch_mod.fetch(tmp_path, url, sha, "m.gguf", allow_local=True, say=quiet)
    assert _H.hits == 1                                                      # the cached, verified file is kept
    p.write_bytes(b"corrupt")                                                # a damaged cache entry is replaced
    fetch_mod.fetch(tmp_path, url, sha, "m.gguf", allow_local=True, say=quiet)
    assert p.read_bytes() == data and _H.hits == 2


def test_fetch_fails_the_build_on_a_wrong_checksum(fetch_mod, srv, tmp_path):
    from gt7telem import llm
    _H.payload = b"not the model"
    url = f"http://127.0.0.1:{srv.server_address[1]}/m.gguf"
    with pytest.raises(llm.DownloadError, match="checksum"):
        fetch_mod.fetch(tmp_path, url, "0" * 64, "m.gguf", allow_local=True, say=lambda *a: None)
    assert not list(tmp_path.glob("m.gguf*"))


def test_fetch_defaults_are_the_pinned_model(fetch_mod):
    import inspect

    from gt7telem import llm
    src = inspect.getsource(fetch_mod.fetch)
    assert "llm.MODEL_URL" in src and "llm.MODEL_SHA256" in src and re.fullmatch(r"[0-9a-f]{64}", llm.MODEL_SHA256)
    assert fetch_mod.main([]) == 2
