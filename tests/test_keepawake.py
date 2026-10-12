import os

from gt7telem import keepawake


def test_macos_caffeinate_is_tied_to_this_process(monkeypatch):
    calls = []

    class FakeProc:
        def terminate(self):
            calls.append("terminate")

    def fake_popen(cmd):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(keepawake.sys, "platform", "darwin")
    monkeypatch.setattr(keepawake.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(keepawake, "_on", False)
    monkeypatch.setattr(keepawake, "_proc", None)

    keepawake.set_awake(True)
    # -w <pid>: caffeinate exits with TRACE even if TRACE crashes or is killed
    assert calls[0] == ["caffeinate", "-i", "-w", str(os.getpid())]
    keepawake.set_awake(True)          # repeat call is a no-op
    assert len(calls) == 1
    keepawake.set_awake(False)
    assert calls[-1] == "terminate"
