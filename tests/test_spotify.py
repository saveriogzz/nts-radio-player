"""Tests for nts.spotify — Soloist WebSocket event handling."""

import json
from unittest.mock import MagicMock

from nts.spotify import SpotifyClient, SpotifyState, apply_message


def _track(name="My Song", artists=("Artist A", "Artist B"), album="An Album",
           covers=(("small", "https://i.scdn.co/s"), ("default", "https://i.scdn.co/d")),
           duration_ms=200000):
    return {
        "uri": "spotify:track:abc",
        "entity_type": "track",
        "decorations": {
            "identity": {"name": name},
            "creators": [{"entity": {"decorations": {"identity": {"name": a}}}} for a in artists],
            "parent": {"entity": {"decorations": {"identity": {"name": album}}}},
            "visual_identity": {"cover": [{"size": s, "url": u} for s, u in covers]},
            "playback": {"duration_ms": duration_ms},
        },
    }


PLAYBACK_STATE = {
    "type": "playback_state",
    "status": "playing",
    "item": _track(),
    "position": {"position_ms": 45000, "timestamp_ms": 1747654321000, "speed": 1.0},
    "volume": 65,
    "is_active": True,
}


class TestApplyMessage:
    def test_playback_state_snapshot(self):
        st = apply_message(SpotifyState(), PLAYBACK_STATE, now=100.0)
        assert st.status == "playing"
        assert st.is_playing
        assert st.title == "My Song"
        assert st.artist == "Artist A, Artist B"
        assert st.album == "An Album"
        assert st.artwork_url == "https://i.scdn.co/d"
        assert st.duration_ms == 200000
        assert st.position_ms == 45000
        assert st.speed == 1.0

    def test_inactive_snapshot_is_idle(self):
        st = apply_message(SpotifyState(), dict(PLAYBACK_STATE, is_active=False), now=0.0)
        assert st.status == "idle"
        assert not st.is_playing

    def test_snapshot_without_item_clears_track(self):
        st = apply_message(SpotifyState(title="Old"), {"type": "playback_state", "status": "idle"})
        assert st.title == ""
        assert st.artwork_url is None

    def test_track_changed_resets_position(self):
        st = apply_message(SpotifyState(), PLAYBACK_STATE, now=100.0)
        st = apply_message(st, {"type": "track_changed", "item": _track(name="Next")}, now=110.0)
        assert st.title == "Next"
        assert st.position_ms == 0
        assert st.status == "playing"

    def test_playback_changed_freezes_position_on_pause(self):
        st = apply_message(SpotifyState(), PLAYBACK_STATE, now=100.0)
        st = apply_message(st, {"type": "playback_changed", "status": "paused"}, now=110.0)
        assert st.status == "paused"
        assert st.speed == 0.0
        assert st.position_ms == 55000
        assert st.current_position_ms(now=500.0) == 55000

    def test_playback_changed_resume_restores_speed(self):
        st = SpotifyState(status="paused", position_ms=1000, speed=0.0)
        st = apply_message(st, {"type": "playback_changed", "status": "playing"}, now=10.0)
        assert st.is_playing
        assert st.speed == 1.0
        assert st.current_position_ms(now=12.0) == 3000

    def test_position_sync(self):
        st = apply_message(SpotifyState(status="playing"),
                           {"type": "position_sync", "position": {"position_ms": 9000, "speed": 1.0}},
                           now=50.0)
        assert st.current_position_ms(now=51.5) == 10500

    def test_position_clamped_to_duration(self):
        st = SpotifyState(duration_ms=10000, position_ms=9000, position_ts=0.0, speed=1.0)
        assert st.current_position_ms(now=60.0) == 10000

    def test_device_deactivated_is_idle(self):
        st = apply_message(SpotifyState(), PLAYBACK_STATE, now=0.0)
        st = apply_message(st, {"type": "device_changed", "is_active": False})
        assert st.status == "idle"

    def test_cover_falls_back_to_any_size(self):
        st = apply_message(SpotifyState(), dict(PLAYBACK_STATE, item=_track(covers=(("xlarge", "https://x"),))))
        assert st.artwork_url == "https://x"

    def test_unknown_and_malformed_messages_ignored(self):
        st = SpotifyState(status="playing", title="Keep")
        for msg in ({"type": "volume_changed", "volume": 3}, {"type": "track_changed"},
                    {"foo": 1}, "not a dict", None):
            assert apply_message(st, msg) == st


class TestSpotifyClient:
    def test_reports_only_changes(self):
        on_state = MagicMock()
        client = SpotifyClient(on_state)
        client._handle_text(json.dumps(PLAYBACK_STATE))
        client._handle_text(json.dumps({"type": "volume_changed", "volume": 10}))
        client._handle_text("not json")
        assert on_state.call_count == 1
        assert on_state.call_args[0][0].title == "My Song"

    def test_send_command_without_connection(self):
        assert SpotifyClient(MagicMock()).send_command("pause") is False

    def test_send_command_payload(self):
        client = SpotifyClient(MagicMock())
        client._ws = MagicMock()
        assert client.send_command("skip_next") is True
        sent = json.loads(client._ws.send.call_args[0][0])
        assert sent == {"type": "command", "command": "skip_next"}
