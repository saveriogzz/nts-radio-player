"""Client for the source router (core/router).

The router decides which source owns the speaker and publishes a now-playing
feed. ``RouterClient`` keeps a WebSocket connection to it on a daemon thread,
reports every state change through a callback (the app turns it into a loop
event) and sends commands for other sources (e.g. Spotify skip/pause).
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from router.state import NowPlaying, now_playing_from_wire

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:4319"
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0


@dataclass(frozen=True)
class RouterState:
    active: Optional[str] = None  # "nts", "spotify", ... or None
    playing: frozenset = field(default_factory=frozenset)
    sources: dict = field(default_factory=dict)  # name -> NowPlaying

    def now_playing(self, source: str) -> NowPlaying:
        return self.sources.get(source) or NowPlaying()


def parse_state(msg: dict) -> Optional[RouterState]:
    """RouterState from a router ``state`` message, or None for other messages."""
    if not isinstance(msg, dict) or msg.get("type") != "state":
        return None
    sources = msg.get("sources") or {}
    return RouterState(
        active=msg.get("active"),
        playing=frozenset(msg.get("playing") or ()),
        sources={name: now_playing_from_wire(data) for name, data in sources.items()
                 if isinstance(data, dict)},
    )


class RouterClient:
    def __init__(self, on_state: Callable[[RouterState], None], url: str = DEFAULT_URL):
        self._on_state = on_state
        self._url = url
        self._ws = None
        self._ws_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="router-ws")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._ws_lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass

    def send_command(self, source: str, command: str) -> bool:
        with self._ws_lock:
            if self._ws is None:
                return False
            try:
                self._ws.send(json.dumps({"type": "command", "source": source, "command": command}))
                return True
            except Exception:
                logger.warning("Router command %s/%s failed", source, command)
                return False

    def _handle_text(self, text: str):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        state = parse_state(msg)
        if state is not None:
            self._on_state(state)

    def _run(self):
        import websocket  # websocket-client

        delay = RECONNECT_MIN
        connected_once = False
        while not self._stop.is_set():
            try:
                ws = websocket.create_connection(self._url, timeout=10)
                ws.settimeout(None)
                with self._ws_lock:
                    self._ws = ws
                logger.info("Connected to source router")
                connected_once = True
                delay = RECONNECT_MIN
                while not self._stop.is_set():
                    self._handle_text(ws.recv())
            except Exception as e:
                if connected_once:
                    logger.info("Source router disconnected: %s", e)
            finally:
                with self._ws_lock:
                    if self._ws is not None:
                        try:
                            self._ws.close()
                        except Exception:
                            pass
                    self._ws = None
            # Without the router nobody arbitrates; report "no owner"
            self._on_state(RouterState())
            self._stop.wait(delay)
            delay = min(delay * 2, RECONNECT_MAX)
