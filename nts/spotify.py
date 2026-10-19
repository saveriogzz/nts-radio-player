"""Spotify Connect "now playing" via Soloist's local WebSocket API.

Soloist (the spotify container) runs with ``--ws 127.0.0.1:4318`` and pushes
JSON events about playback. ``apply_message`` folds those events into an
immutable ``SpotifyState``; ``SpotifyClient`` runs the connection on a
daemon thread and reports every state change through a callback, so the app
loop only ever sees plain events.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:4318"

# Soloist cover sizes, preferred first (the display shows 120x120 artwork)
_COVER_SIZE_PREFERENCE = ("default", "large", "small", "xlarge")

RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0


@dataclass(frozen=True)
class SpotifyState:
    status: str = "idle"  # idle, playing, paused, buffering
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: Optional[str] = None
    duration_ms: int = 0
    position_ms: int = 0
    position_ts: float = 0.0  # time.monotonic() when position_ms was valid
    speed: float = 0.0

    @property
    def is_playing(self) -> bool:
        return self.status in ("playing", "buffering")

    def current_position_ms(self, now: Optional[float] = None) -> int:
        """Position interpolated from the last sync, clamped to the duration."""
        now = time.monotonic() if now is None else now
        pos = self.position_ms + (now - self.position_ts) * 1000 * self.speed
        if self.duration_ms:
            pos = min(pos, self.duration_ms)
        return max(0, int(pos))


def _name(entity: dict) -> str:
    return (((entity or {}).get("decorations") or {}).get("identity") or {}).get("name") or ""


def _cover_url(decorations: dict) -> Optional[str]:
    covers = (decorations.get("visual_identity") or {}).get("cover") or []
    by_size = {c.get("size"): c.get("url") for c in covers if c.get("url")}
    for size in _COVER_SIZE_PREFERENCE:
        if by_size.get(size):
            return by_size[size]
    return next(iter(by_size.values()), None)


def _track_fields(item: dict) -> dict:
    """Extract display fields from a Soloist entity envelope."""
    deco = (item or {}).get("decorations") or {}
    creators = [_name(c.get("entity")) for c in deco.get("creators") or []]
    parent = (deco.get("parent") or {}).get("entity")
    return {
        "title": _name(item),
        "artist": ", ".join(n for n in creators if n),
        "album": _name(parent),
        "artwork_url": _cover_url(deco),
        "duration_ms": int((deco.get("playback") or {}).get("duration_ms") or 0),
    }


def _position_fields(position: dict, now: float) -> dict:
    return {
        "position_ms": int(position.get("position_ms") or 0),
        "speed": float(position.get("speed") or 0.0),
        "position_ts": now,
    }


def apply_message(state: SpotifyState, msg: dict, now: Optional[float] = None) -> SpotifyState:
    """Return the state after one Soloist event; unknown events change nothing."""
    now = time.monotonic() if now is None else now
    if not isinstance(msg, dict):
        return state
    kind = msg.get("type")

    if kind == "playback_state":
        fields = {"status": msg.get("status") or "idle"}
        fields.update(_track_fields(msg.get("item")) if msg.get("item") else
                      {"title": "", "artist": "", "album": "", "artwork_url": None, "duration_ms": 0})
        fields.update(_position_fields(msg.get("position") or {}, now))
        if msg.get("is_active") is False:
            fields["status"] = "idle"
        return replace(state, **fields)

    if kind == "track_changed" and msg.get("item"):
        return replace(state, **_track_fields(msg["item"]), position_ms=0, position_ts=now)

    if kind == "playback_changed" and msg.get("status"):
        # Freeze the interpolated position when playback stops
        pos = state.current_position_ms(now)
        speed = (state.speed or 1.0) if msg["status"] == "playing" else 0.0
        return replace(state, status=msg["status"], position_ms=pos, position_ts=now, speed=speed)

    if kind == "position_sync" and isinstance(msg.get("position"), dict):
        return replace(state, **_position_fields(msg["position"], now))

    if kind == "device_changed" and msg.get("is_active") is False:
        return replace(state, status="idle", speed=0.0)

    return state


class SpotifyClient:
    """Keeps a WebSocket connection to Soloist and reports state changes."""

    def __init__(self, on_state: Callable[[SpotifyState], None], url: str = DEFAULT_URL):
        self._on_state = on_state
        self._url = url
        self._state = SpotifyState()
        self._ws = None
        self._ws_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="spotify-ws")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._ws_lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass

    def send_command(self, command: str) -> bool:
        """Send a Soloist control command (play, pause, skip_next, ...)."""
        with self._ws_lock:
            if self._ws is None:
                return False
            try:
                self._ws.send(json.dumps({"type": "command", "command": command}))
                return True
            except Exception:
                logger.warning("Spotify command %r failed", command)
                return False

    def _handle_text(self, text: str):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        new_state = apply_message(self._state, msg)
        if new_state != self._state:
            self._state = new_state
            self._on_state(new_state)

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
                logger.info("Spotify WebSocket connected")
                connected_once = True
                delay = RECONNECT_MIN
                while not self._stop.is_set():
                    self._handle_text(ws.recv())
            except Exception as e:
                if connected_once:
                    logger.info("Spotify WebSocket disconnected: %s", e)
            finally:
                with self._ws_lock:
                    if self._ws is not None:
                        try:
                            self._ws.close()
                        except Exception:
                            pass
                    self._ws = None
            # Soloist gone: whatever was playing has stopped
            if self._state.status != "idle":
                self._state = replace(self._state, status="idle", speed=0.0)
                self._on_state(self._state)
            self._stop.wait(delay)
            delay = min(delay * 2, RECONNECT_MAX)
