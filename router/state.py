"""Playback state shared by the router and its clients.

``SpotifyState`` is the now-playing snapshot of Spotify Connect; ``apply_message``
folds Soloist's WebSocket events into it. Both the router (which talks to
Soloist) and nts-radio (which draws it) use this module, so it has no
dependencies beyond the standard library.
"""

import time
from dataclasses import asdict, dataclass, fields, replace
from typing import Optional

# Soloist cover sizes, preferred first (the display shows 120x120 artwork)
_COVER_SIZE_PREFERENCE = ("default", "large", "small", "xlarge")


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


def spotify_to_wire(state: SpotifyState, now: Optional[float] = None) -> dict:
    """JSON-safe dict for the router API.

    ``position_ts`` is a process-local monotonic time, so it is not sent:
    ``position_ms`` is the interpolated position at send time and the receiver
    re-anchors it on its own clock.
    """
    data = asdict(state)
    data.pop("position_ts")
    data["position_ms"] = state.current_position_ms(now)
    return data


def spotify_from_wire(data: Optional[dict], now: Optional[float] = None) -> SpotifyState:
    """Inverse of ``spotify_to_wire``; unknown keys are ignored."""
    if not isinstance(data, dict):
        return SpotifyState()
    known = {f.name for f in fields(SpotifyState)} - {"position_ts"}
    values = {k: v for k, v in data.items() if k in known}
    now = time.monotonic() if now is None else now
    return replace(SpotifyState(), **values, position_ts=now)
