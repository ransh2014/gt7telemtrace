"""Spoken alerts: which command each OS runs, the fallback, and when the announcer speaks."""
import threading
import time
from types import SimpleNamespace

from gt7telem import config, dashboard, voice


def _which(*present):
    return lambda name: f"/bin/{name}" if name in present else None


def test_windows_uses_sapi_through_powershell_and_keeps_text_out_of_the_command():
    cmd = voice.voice_command("win32", "Fuel low; Remove-Item *", _which("powershell"))
    assert cmd[0] == "powershell" and "System.Speech" in cmd[-1]
    assert "Remove-Item" not in " ".join(cmd)          # the text travels in an env var, never in the script
    assert voice.voice_command("win32", "x", _which("pwsh"))[0] == "pwsh"
    assert voice.voice_command("win32", "x", _which()) is None


def test_macos_uses_say():
    assert voice.voice_command("darwin", "Fuel low", _which("say")) == ["say", "Fuel low"]
    assert voice.voice_command("darwin", "Fuel low", _which()) is None


def test_linux_prefers_spd_say_then_espeak():
    assert voice.voice_command("linux", "hi", _which("spd-say", "espeak"))[0] == "spd-say"
    assert voice.voice_command("linux", "hi", _which("espeak-ng", "espeak"))[0] == "espeak-ng"
    assert voice.voice_command("linux", "hi", _which("espeak")) == ["espeak", "hi"]
    assert voice.voice_command("linux", "hi", _which()) is None
    assert voice.available("linux", _which("espeak")) and not voice.available("linux", _which())


def test_speak_runs_the_voice_command_when_there_is_one():
    ran, beeped = [], []
    voice.speak("Tyres cold", platform="darwin", which=_which("say"),
                run=lambda cmd, text: ran.append((cmd, text)), fallback=lambda: beeped.append(1), threaded=False)
    assert ran == [(["say", "Tyres cold"], "Tyres cold")] and not beeped


def test_speak_falls_back_to_the_alert_sound_without_a_voice():
    ran, beeped = [], []
    voice.speak("Fuel low", platform="linux", which=_which(),
                run=lambda *a: ran.append(a), fallback=lambda: beeped.append(1), threaded=False)
    assert beeped == [1] and not ran


def test_speak_falls_back_when_the_voice_fails_to_start():
    beeped = []

    def boom(cmd, text):
        raise OSError("no such file")

    voice.speak("Fuel low", platform="darwin", which=_which("say"), run=boom,
                fallback=lambda: beeped.append(1), threaded=False)
    assert beeped == [1]


def test_speak_never_blocks_the_caller():
    release, started, done = threading.Event(), threading.Event(), threading.Event()

    def slow_run(cmd, text):
        started.set()
        release.wait(5)
        done.set()

    t0 = time.monotonic()
    voice.speak("Fuel low", platform="darwin", which=_which("say"), run=slow_run, fallback=lambda: None)
    assert time.monotonic() - t0 < 0.5            # returned while the voice was still "talking"
    assert started.wait(2) and not done.is_set()
    release.set()
    assert done.wait(2)


def test_speak_swallows_a_failing_fallback():
    def bad():
        raise RuntimeError("no audio")

    voice.speak("x", platform="linux", which=_which(), fallback=bad, threaded=False)   # must not raise


def test_announcer_speaks_once_when_an_alert_turns_on():
    a = voice.AlertAnnouncer()
    assert a.update({"fuel"}, 0.0, fuel_laps=1.4) == ["Fuel low. About 1.4 laps left"]
    assert a.update({"fuel"}, 1.0, fuel_laps=1.3) == []
    assert a.update(set(), 2.0) == []


def test_announcer_repeats_fuel_and_hot_but_not_cold():
    a = voice.AlertAnnouncer(repeat_s=60)
    assert a.update({"fuel", "hot", "cold"}, 0.0) == ["Fuel low", "Tyres overheating", "Tyres cold"]
    assert a.update({"fuel", "hot", "cold"}, 30.0) == []
    assert a.update({"fuel", "hot", "cold"}, 61.0) == ["Fuel low", "Tyres overheating"]


def test_announcer_needs_a_quiet_spell_before_it_speaks_again():
    a = voice.AlertAnnouncer(repeat_s=1000, clear_s=5)
    assert a.update({"hot"}, 0.0) == ["Tyres overheating"]
    assert a.update(set(), 1.0) == []
    assert a.update({"hot"}, 2.0) == []                 # flickered off for 1 s: still the same alert
    assert a.update(set(), 3.0) == []
    assert a.update(set(), 9.0) == []                   # off for 6 s -> cleared
    assert a.update({"hot"}, 10.0) == ["Tyres overheating"]


def test_announcer_reset_and_far_away_fuel_laps():
    a = voice.AlertAnnouncer()
    assert a.update({"fuel"}, 0.0, fuel_laps=999) == ["Fuel low"]       # 999 = "unknown" in the Dashboard
    a.reset()
    assert a.update({"fuel"}, 1.0) == ["Fuel low"]


def test_voice_toggle_persists_and_mutes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_KEY_FILE", tmp_path / ".settings.key")
    monkeypatch.setattr(config, "VOICE_ENABLED", True)
    spoken = []
    monkeypatch.setattr(voice, "speak", lambda text, **kw: spoken.append(text))
    logged = []
    fake = SimpleNamespace(voice_var=SimpleNamespace(get=lambda: False), log_msg=logged.append,
                           _announcer=voice.AlertAnnouncer())
    fake._speak_alerts = lambda *a: dashboard.App._speak_alerts(fake, *a)

    dashboard.App._on_voice_toggle(fake)
    assert config.VOICE_ENABLED is False and config.load()["VOICE_ENABLED"] is False
    assert "muted" in logged[-1]
    fake._speak_alerts(True, False, False, 1.0)
    assert spoken == []                                   # muted: nothing is said

    fake.voice_var = SimpleNamespace(get=lambda: True)
    dashboard.App._on_voice_toggle(fake)
    assert config.VOICE_ENABLED is True and config.load()["VOICE_ENABLED"] is True
    fake._speak_alerts(True, False, False, 1.0)
    assert spoken == ["Fuel low. About 1.0 laps left"]
