"""Spotify Connect adapter: talks to Soloist's local WebSocket API.

Soloist (the spotify container) runs with ``--ws 127.0.0.1:4318``. The adapter
keeps a connection open, folds events into a ``NowPlaying`` for the
now-playing feed, and sends commands. Knowing *whether* Spotify is playing
comes from PipeWire; this is only for metadata and control.
"""

import asyncio
import json
import logging
from dataclasses import replace
from typing import Awaitable, Callable, Optional

from router.state import NowPlaying, apply_message

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:4318"
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0

# Commands clients may send for this source
COMMANDS = {"play", "pause", "skip_next", "skip_prev", "stop"}


class SoloistAdapter:
    name = "spotify"

    def __init__(self, on_change: Callable[[], Awaitable[None]], url: str = DEFAULT_URL):
        self._on_change = on_change
        self._url = url
        self.state = NowPlaying()
        self._ws = None

    async def handle_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        new_state = apply_message(self.state, msg)
        if new_state != self.state:
            if new_state.status != self.state.status:
                logger.info("Spotify status: %s -> %s", self.state.status, new_state.status)
            self.state = new_state
            await self._on_change()

    async def command(self, command: str) -> bool:
        """Send a control command; ``stop`` releases the Connect device."""
        if command not in COMMANDS:
            return False
        if command == "stop":
            # Pause, then stop being the active device so the phone doesn't
            # resume into this speaker by surprise
            ok = await self._send("pause")
            return await self._send("deactivate") and ok
        return await self._send(command)

    async def _send(self, command: str) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps({"type": "command", "command": command}))
            return True
        except Exception:
            logger.warning("Soloist command %r failed", command)
            return False

    async def run(self) -> None:
        from websockets.asyncio.client import connect

        delay = RECONNECT_MIN
        connected_once = False
        while True:
            try:
                async with connect(self._url, open_timeout=10, ping_interval=20) as ws:
                    self._ws = ws
                    logger.info("Connected to Soloist")
                    connected_once = True
                    delay = RECONNECT_MIN
                    async for text in ws:
                        if isinstance(text, str):
                            await self.handle_text(text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if connected_once:
                    logger.info("Soloist connection lost: %s", e)
            finally:
                self._ws = None
            if self.state.status != "idle":
                self.state = replace(self.state, status="idle", speed=0.0)
                await self._on_change()
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)
