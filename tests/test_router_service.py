"""Tests for router.service and router.soloist."""

import asyncio
import json

from router.service import Router
from router.soloist import SoloistAdapter
from router.state import SpotifyState


class FakeAdapter:
    def __init__(self):
        self.state = SpotifyState(status="playing", title="Song")
        self.commands = []

    async def command(self, command):
        self.commands.append(command)
        return True


def run(coro):
    return asyncio.run(coro)


def test_newest_wins_stops_spotify_when_nts_starts():
    spotify = FakeAdapter()
    router = Router({"spotify": spotify})

    async def scenario():
        await router.on_playing_changes({"spotify": True})
        await router.on_playing_changes({"nts": True})

    run(scenario())
    assert router.arbiter.active == "nts"
    assert spotify.commands == ["stop"]


def test_spotify_taking_over_needs_no_command_for_nts():
    spotify = FakeAdapter()
    router = Router({"spotify": spotify})

    async def scenario():
        await router.on_playing_changes({"nts": True})
        await router.on_playing_changes({"spotify": True})

    run(scenario())
    # nts has no adapter: nts-radio sees active=spotify and stops mpv itself
    assert router.arbiter.active == "spotify"
    assert spotify.commands == []


def test_state_message_contains_spotify_metadata():
    router = Router({"spotify": FakeAdapter()})
    run(router.on_playing_changes({"spotify": True}))
    msg = router.state_message()
    assert msg["type"] == "state"
    assert msg["active"] == "spotify"
    assert msg["playing"] == ["spotify"]
    assert msg["sources"]["spotify"]["title"] == "Song"
    assert "position_ts" not in msg["sources"]["spotify"]


def test_commands_routed_and_validated():
    spotify = FakeAdapter()
    router = Router({"spotify": spotify})
    ok = run(router.on_command({"type": "command", "source": "spotify", "command": "skip_next"}))
    assert ok["type"] == "command_result" and ok["ok"]
    assert spotify.commands == ["skip_next"]
    assert run(router.on_command({"type": "command", "source": "nope", "command": "pause"}))["type"] == "error"
    assert run(router.on_command({"type": "hello"}))["type"] == "error"


def test_soloist_adapter_stop_pauses_and_deactivates():
    changes = []

    async def on_change():
        changes.append(1)

    adapter = SoloistAdapter(on_change)
    sent = []

    class WS:
        async def send(self, text):
            sent.append(json.loads(text)["command"])

    adapter._ws = WS()
    assert run(adapter.command("stop"))
    assert sent == ["pause", "deactivate"]
    assert not run(adapter.command("format_disk"))


def test_soloist_adapter_folds_events():
    changes = []

    async def on_change():
        changes.append(1)

    adapter = SoloistAdapter(on_change)
    run(adapter.handle_text(json.dumps({"type": "playback_changed", "status": "playing"})))
    run(adapter.handle_text("not json"))
    assert adapter.state.status == "playing"
    assert changes == [1]


def test_api_end_to_end():
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    spotify = FakeAdapter()
    router = Router({"spotify": spotify})

    async def scenario():
        async with serve(router.handle_client, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                first = json.loads(await ws.recv())
                assert first["type"] == "state" and first["active"] is None
                await router.on_playing_changes({"spotify": True})
                update = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert update["active"] == "spotify"
                await ws.send(json.dumps({"type": "command", "source": "spotify", "command": "pause"}))
                result = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert result == {"type": "command_result", "source": "spotify", "command": "pause", "ok": True}

    run(scenario())
    assert spotify.commands == ["pause"]
