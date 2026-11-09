"""The launcher's update check: numeric version comparison and the current / available / nothing decision."""
import pytest

from gt7telem import updates


def test_numeric_not_text_comparison():
    assert updates.is_newer("1.0.10", "1.0.9")
    assert not updates.is_newer("1.0.9", "1.0.10")
    assert updates.is_newer("2.0", "1.9.9")


def test_leading_v_suffix_and_padding_are_ignored():
    assert not updates.is_newer("v1.0.3", "1.0.3")
    assert not updates.is_newer("1.0", "1.0.0")
    assert not updates.is_newer("1.0.3-rc1", "1.0.3")
    assert updates.is_newer("V1.0.4+b7", "1.0.3")


def test_evaluate_current_available_and_skips():
    ev = updates.evaluate
    assert ev({"tag_name": "v1.0.3", "html_url": "u"}, "1.0.3")[0] == updates.CURRENT
    assert ev({"tag_name": "v1.0.4", "html_url": "u"}, "1.0.3") == (updates.AVAILABLE, "1.0.4", "u")
    assert ev({"tag_name": "v1.0.4", "draft": True}, "1.0.3") is None
    assert ev({"tag_name": "v1.0.4", "prerelease": True}, "1.0.3") is None
    assert ev({"tag_name": "nightly"}, "1.0.3") is None
    assert ev("garbage", "1.0.3") is None


def test_check_is_silent_on_error_and_cached():
    updates._cache.clear()
    got = []
    done = __import__("threading").Event()

    def cb(r):
        got.append(r)
        done.set()

    def boom():
        raise OSError("offline")

    updates.check("9.9.9", cb, fetch=boom)
    assert done.wait(3) and got == [None]
    # cached: a second call must not fetch again
    done.clear()
    updates.check("9.9.9", cb, fetch=lambda: pytest.fail("fetched twice"))
    assert done.wait(3) and got == [None, None]


def _mock_rc(monkeypatch, rc):
    import ctypes
    import types
    monkeypatch.setattr(updates.sys, "platform", "win32")
    fake = types.SimpleNamespace(kernel32=types.SimpleNamespace(GetCurrentPackageFullName=lambda *a: rc))
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)


@pytest.mark.parametrize("rc,expected", [(15700, False), (0, True), (122, True)])
def test_is_store_install_return_codes(monkeypatch, rc, expected):
    _mock_rc(monkeypatch, rc)
    assert updates.is_store_install() is expected


def test_is_store_install_false_off_windows_and_on_failure(monkeypatch):
    monkeypatch.setattr(updates.sys, "platform", "linux")
    assert updates.is_store_install() is False
    import ctypes
    monkeypatch.setattr(updates.sys, "platform", "win32")

    class Boom:
        @property
        def kernel32(self):
            raise OSError("no kernel32")
    monkeypatch.setattr(ctypes, "windll", Boom(), raising=False)
    assert updates.is_store_install() is False


def test_check_makes_no_request_on_store_install(monkeypatch):
    updates._cache.clear()
    _mock_rc(monkeypatch, 0)
    called = []
    updates.check("9.9.9", called.append, fetch=lambda: pytest.fail("hit the network"))
    import time
    time.sleep(0.2)
    assert called == []
