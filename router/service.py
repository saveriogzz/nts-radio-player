"""Source router: who owns the speaker, plus a now-playing feed.

- Learns which sources play from PipeWire (``router.pipewire``)
- Decides ownership with ``router.policy.Arbiter`` (newest wins) and asks the
  previous owner to stop through its adapter
- Serves the state to clients (nts-radio's screen/buttons) over a local
  WebSocket API and forwards their commands to the right adapter

API (JSON text frames, ``ws://127.0.0.1:4319``):
  server → client  {"type": "state", "active": "spotify"|"nts"|null,
                    "playing": [...], "sources": {"spotify": {...}}}
  client → server  {"type": "command", "source": "spotify", "command": "pause"}
"""

import asyncio
import json
import logging
from typing import Optional

from router.policy import Arbiter
from router.state import spotify_to_wire

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4319


class Router:
    def __init__(self, adapters: Optional[dict] = None):
        # Sources that the router can control; sources without one (nts)
        # react to the "active" field themselves
        self.adapters: dict = adapters or {}
        self.arbiter = Arbiter()
        self._clients: set = set()

    # ── State ────────────────────────────────────────────────

    def state_message(self) -> dict:
        # Every adapter exposes its now-playing ``state`` in the same shape
        sources = {
            name: spotify_to_wire(adapter.state)
            for name, adapter in self.adapters.items()
            if getattr(adapter, "state", None) is not None
        }
        return {
            "type": "state",
            "active": self.arbiter.active,
            "playing": sorted(self.arbiter.playing),
            "sources": sources,
        }

    async def broadcast(self) -> None:
        if not self._clients:
            return
        text = json.dumps(self.state_message())
        for ws in list(self._clients):
            try:
                await ws.send(text)
            except Exception:
                self._clients.discard(ws)

    # ── Inputs ───────────────────────────────────────────────

    async def on_playing_changes(self, changes: dict[str, bool]) -> None:
        """Apply per-source playing changes from PipeWire."""
        for source, playing in sorted(changes.items(), key=lambda kv: not kv[1]):
            decision = self.arbiter.update(source, playing)
            if decision is not None:
                logger.info("Active source: %s (stopping %s)", decision.active, decision.stop)
                if decision.stop and decision.stop in self.adapters:
                    await self.adapters[decision.stop].command("stop")
        await self.broadcast()

    async def on_command(self, msg: dict) -> dict:
        if not isinstance(msg, dict) or msg.get("type") != "command":
            return {"type": "error", "message": "expected a command"}
        adapter = self.adapters.get(msg.get("source"))
        if adapter is None:
            return {"type": "error", "message": f"unknown source {msg.get('source')!r}"}
        ok = await adapter.command(str(msg.get("command")))
        return {"type": "command_result", "source": msg["source"], "command": msg.get("command"), "ok": ok}

    # ── API server ───────────────────────────────────────────

    async def handle_client(self, ws) -> None:
        self._clients.add(ws)
        try:
            await ws.send(json.dumps(self.state_message()))
            async for text in ws:
                try:
                    msg = json.loads(text)
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "message": "invalid JSON"}))
                    continue
                await ws.send(json.dumps(await self.on_command(msg)))
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
