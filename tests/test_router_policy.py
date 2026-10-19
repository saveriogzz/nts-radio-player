"""Tests for router.policy — newest-wins arbitration."""

from router.policy import Arbiter, Decision


def test_first_source_becomes_active():
    a = Arbiter()
    assert a.update("nts", True) == Decision(active="nts", stop=None)
    assert a.active == "nts"


def test_newer_source_takes_over_and_stops_previous():
    a = Arbiter()
    a.update("nts", True)
    assert a.update("spotify", True) == Decision(active="spotify", stop="nts")
    assert a.active == "spotify"


def test_back_to_previous_source():
    a = Arbiter()
    a.update("nts", True)
    a.update("spotify", True)
    a.update("nts", False)  # nts stopped as asked
    assert a.update("nts", True) == Decision(active="nts", stop="spotify")


def test_pause_keeps_ownership():
    a = Arbiter()
    a.update("spotify", True)
    assert a.update("spotify", False) is None
    assert a.active == "spotify"


def test_resume_of_active_source_is_not_a_change():
    a = Arbiter()
    a.update("spotify", True)
    a.update("spotify", False)
    assert a.update("spotify", True) is None


def test_takeover_from_paused_owner_stops_nothing():
    a = Arbiter()
    a.update("spotify", True)
    a.update("spotify", False)  # paused, still owner
    assert a.update("nts", True) == Decision(active="nts", stop=None)


def test_repeated_playing_reports_are_ignored():
    a = Arbiter()
    a.update("nts", True)
    a.update("spotify", True)
    # nts still running for a moment before it stops: not a new start
    assert a.update("nts", True) is None
    assert a.active == "spotify"


def test_forget_active_source():
    a = Arbiter()
    a.update("spotify", True)
    a.forget("spotify")
    assert a.active is None
    assert "spotify" not in a.playing
