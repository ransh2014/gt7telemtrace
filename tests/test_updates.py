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
