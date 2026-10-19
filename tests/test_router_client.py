"""Tests for nts.router_client — router state messages → RouterState."""

import json
from unittest.mock import MagicMock

from nts.router_client import RouterClient, RouterState, parse_state


def test_parse_state_message():
    st = parse_state({
        "type": "state",
        "active": "spotify",
        "playing": ["spotify"],
        "sources": {"spotify": {"status": "playing", "title": "Song", "position_ms": 5000, "speed": 1.0}},
    })
    assert st.active == "spotify"
    assert st.playing == frozenset({"spotify"})
    assert st.spotify.title == "Song"
    assert st.spotify.is_playing
    assert st.connected


def test_parse_ignores_other_messages():
    assert parse_state({"type": "command_result", "ok": True}) is None
    assert parse_state("nope") is None


def test_missing_sources_gives_empty_spotify():
    st = parse_state({"type": "state", "active": "nts"})
    assert st.active == "nts"
    assert st.spotify.title == ""


def test_client_reports_state_messages_only():
    on_state = MagicMock()
    client = RouterClient(on_state)
    client._handle_text(json.dumps({"type": "state", "active": None}))
    client._handle_text(json.dumps({"type": "command_result"}))
    client._handle_text("not json")
    assert on_state.call_count == 1
    assert isinstance(on_state.call_args[0][0], RouterState)


def test_send_command_payload():
    client = RouterClient(MagicMock())
    assert client.send_command("spotify", "pause") is False  # not connected
    client._ws = MagicMock()
    assert client.send_command("spotify", "skip_next") is True
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent == {"type": "command", "source": "spotify", "command": "skip_next"}
