"""Safety net for the whole suite: no test may talk to the internet.

TRACE has real network writes (leaderboard submit, usage analytics, track-name submissions). A test that reaches one
would post to the live service. urllib requests to anything but this machine are refused here; a test that needs one
must monkeypatch the function that makes it."""
import urllib.parse
import urllib.request

import pytest

_LOCAL = {"127.0.0.1", "localhost", "::1", "[::1]", ""}
_real_urlopen = urllib.request.urlopen


def _guarded_urlopen(url, *args, **kwargs):
    target = url.full_url if hasattr(url, "full_url") else str(url)
    host = urllib.parse.urlsplit(target).hostname or ""
    if host not in _LOCAL:
        raise RuntimeError(f"tests must not use the network: blocked request to {target!r}")
    return _real_urlopen(url, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_internet(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _guarded_urlopen)
