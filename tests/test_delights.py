"""The desktop touches: the drawing is checked by eye; here, just that the module and its public pieces exist."""
import pytest

pytest.importorskip("tkinter")

from gt7telem import delights  # noqa: E402


def test_public_helpers_exist():
    for name in ("tooltip", "lights_out", "click_counter", "checker_flag"):
        assert callable(getattr(delights, name))


def test_removed_pieces_stay_removed():
    # the shift-light strip and the credits roll were taken out on purpose
    for name in ("ShiftLights", "credits_roll", "on_konami", "CREDITS"):
        assert not hasattr(delights, name)
