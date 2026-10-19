#!/usr/bin/env python3
"""NTS Radio main application.

Single-threaded event loop that ties together the API client, mpv player,
ST7789 display, and GPIO buttons.

Architecture: all application state is owned by the event loop thread and
is only ever mutated there. Other threads (GPIO callbacks, network fetch
workers) communicate exclusively by putting events on a queue. Network
I/O runs in short-lived worker threads that post their results back as
events, so the loop never blocks on the network.
"""

import logging
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from nts.api import NTSClient
from nts.player import MPVPlayer
from nts.display import Display
from nts.spotify import SpotifyClient, SpotifyState
from nts.buttons import (
    ButtonHandler,
    BUTTON_A,
    BUTTON_B,
    BUTTON_X,
    BUTTON_Y,
)

logger = logging.getLogger(__name__)

# Default config values — overridden by environment variables (Balena device vars)
# NTS_DEFAULT_CHANNEL, NTS_DISPLAY_BRIGHTNESS, NTS_BUTTON_DEBOUNCE_MS
DEFAULT_CONFIG = {
    "default_channel": 1,
    "display_brightness": 80,
    "button_debounce_ms": 200,
}

# Refresh intervals (seconds)
LIVE_REFRESH_INTERVAL = 15
DISPLAY_REFRESH_INTERVAL = 60  # For progress bar updates

# Event loop tick (seconds) — max latency for timers when no events arrive
TICK_INTERVAL = 0.1

LIVE_CHANNELS = (1, 2)

# Return to the NTS screen after Spotify has been paused/idle this long (seconds)
SPOTIFY_RETURN_DELAY = 30
# Redraw interval while Spotify plays, for its progress bar (seconds)
SPOTIFY_REFRESH_INTERVAL = 1


class AppState(Enum):
    """Application screen states."""
    LIVE = auto()
    MIXTAPE = auto()
    MENU = auto()
    SPOTIFY = auto()


# ── Events ───────────────────────────────────────────────────
# Everything that happens off the loop thread arrives as one of these.

@dataclass(frozen=True)
class ButtonPressed:
    button: int


@dataclass(frozen=True)
class LiveInfoFetched:
    """Per-channel info from a live-data fetch; empty dict on failure."""
    infos: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MixtapesFetched:
    items: list = field(default_factory=list)


@dataclass(frozen=True)
class SpotifyStateChanged:
    state: SpotifyState


@dataclass(frozen=True)
class ArtworkFetched:
    url: str
    image: Optional[object] = None


class NTSRadioApp:
    """Main application class for NTS Radio on Pirate Audio."""

    # Menu items
    MENU_ITEMS = ["Live Radio", "Mixtapes"]

    def __init__(self):
        self._config = self._load_config()
        self._state = AppState.LIVE
        self._state_before_menu = AppState.LIVE
        self._running = False

        # Event queue — the only channel into the loop thread
        self._events: "queue.Queue" = queue.Queue()

        # Current selections
        self._current_channel = self._config.get("default_channel", 1)
        self._current_mixtape_idx = 0
        self._menu_selection = 0

        # Cached data (owned by the loop thread)
        self._channel_info: Optional[dict] = None
        self._live_infos: dict[int, dict] = {}
        self._mixtapes: list = []
        self._artwork_cache: dict[str, "Image.Image"] = {}

        # In-flight fetch guards (loop thread only)
        self._live_fetch_pending = False
        self._mixtapes_fetch_pending = False
        self._artwork_pending: set[str] = set()

        # Components (initialized in start())
        self._api: Optional[NTSClient] = None
        self._player: Optional[MPVPlayer] = None
        self._display: Optional[Display] = None
        self._buttons: Optional[ButtonHandler] = None
        self._spotify: Optional[SpotifyClient] = None

        # Spotify Connect (loop thread only)
        self._spotify_state = SpotifyState()
        self._state_before_spotify = AppState.LIVE
        self._spotify_idle_since: Optional[float] = None

        # Track what changed for display updates
        self._display_dirty = True

        self._button_actions = {
            BUTTON_A: self._on_button_a,
            BUTTON_B: self._on_button_b,
            BUTTON_X: self._on_button_x,
            BUTTON_Y: self._on_button_y,
        }

    def _load_config(self) -> dict:
        """Load configuration from environment variables (balena device vars).

        Environment variables:
            NTS_DEFAULT_CHANNEL: 1 or 2
            NTS_DISPLAY_BRIGHTNESS: 0-100
            NTS_BUTTON_DEBOUNCE_MS: debounce time in ms
        """
        config = DEFAULT_CONFIG.copy()

        env_keys = {
            "NTS_DEFAULT_CHANNEL": "default_channel",
            "NTS_DISPLAY_BRIGHTNESS": "display_brightness",
            "NTS_BUTTON_DEBOUNCE_MS": "button_debounce_ms",
        }
        for env_key, config_key in env_keys.items():
            value = os.environ.get(env_key)
            if value:
                try:
                    config[config_key] = int(value)
                except ValueError:
                    logger.warning("Ignoring invalid %s=%r", env_key, value)

        if config["default_channel"] not in LIVE_CHANNELS:
            logger.warning(
                "Invalid default channel %r, using 1", config["default_channel"]
            )
            config["default_channel"] = 1

        return config

    # ── State machine ────────────────────────────────────────

    def _set_state(self, new_state: AppState):
        """State transition (loop thread only)."""
        old_state = self._state
        self._state = new_state
        self._display_dirty = True
        logger.info("State: %s -> %s", old_state.name, new_state.name)

    def _get_state(self) -> AppState:
        return self._state

    # ── Event handling ───────────────────────────────────────

    def _handle_event(self, event):
        """Dispatch a single event (loop thread only)."""
        if isinstance(event, ButtonPressed):
            action = self._button_actions.get(event.button)
            if action:
                action()
        elif isinstance(event, LiveInfoFetched):
            self._on_live_info(event.infos)
        elif isinstance(event, MixtapesFetched):
            self._on_mixtapes(event.items)
        elif isinstance(event, ArtworkFetched):
            self._on_artwork(event.url, event.image)
        elif isinstance(event, SpotifyStateChanged):
            self._on_spotify_state(event.state)
        else:
            logger.warning("Unknown event: %r", event)

    # ── Button handlers ──────────────────────────────────────
    # These contain the actual logic and run on the loop thread;
    # the GPIO thread only enqueues ButtonPressed events.

    def _on_button_a(self):
        """Button A (top-left): Previous channel/item or scroll up."""
        state = self._get_state()

        if state == AppState.SPOTIFY:
            self._spotify_command("skip_prev")

        elif state == AppState.LIVE:
            self._toggle_live_channel()

        elif state == AppState.MIXTAPE:
            # Previous mixtape
            if self._mixtapes:
                self._play_mixtape(
                    (self._current_mixtape_idx - 1) % len(self._mixtapes)
                )

        elif state == AppState.MENU:
            # Scroll up in menu
            self._menu_selection = (self._menu_selection - 1) % len(self._menu_items())
            self._display_dirty = True

    def _on_button_b(self):
        """Button B (bottom-left): Next channel/item or scroll down."""
        state = self._get_state()

        if state == AppState.SPOTIFY:
            self._spotify_command("skip_next")

        elif state == AppState.LIVE:
            self._toggle_live_channel()

        elif state == AppState.MIXTAPE:
            if self._mixtapes:
                self._play_mixtape(
                    (self._current_mixtape_idx + 1) % len(self._mixtapes)
                )

        elif state == AppState.MENU:
            self._menu_selection = (self._menu_selection + 1) % len(self._menu_items())
            self._display_dirty = True

    def _on_button_x(self):
        """Button X (top-right): Play/Pause or select in menu."""
        state = self._get_state()

        if state == AppState.SPOTIFY:
            self._spotify_command("pause" if self._spotify_state.is_playing else "play")

        elif state == AppState.LIVE or state == AppState.MIXTAPE:
            self._player.toggle_pause()
            self._display_dirty = True

        elif state == AppState.MENU:
            self._select_menu_item()

    def _on_button_y(self):
        """Button Y (bottom-right): Open/close menu."""
        state = self._get_state()

        if state == AppState.MENU:
            self._set_state(self._state_before_menu)
        else:
            self._state_before_menu = state
            self._set_state(AppState.MENU)
            self._menu_selection = 0

    # ── Menu actions ─────────────────────────────────────────

    def _select_menu_item(self):
        """Handle menu item selection."""
        items = self._menu_items()
        item = items[min(self._menu_selection, len(items) - 1)]

        # Switching to an NTS source stops Spotify so the two don't overlap
        if item in ("Live Radio", "Mixtapes") and self._spotify_state.is_playing:
            self._spotify_command("pause")

        if item == "Live Radio":
            self._set_state(AppState.LIVE)
            self._play_current()
            self._request_live_refresh()

        elif item == "Spotify":
            self._set_state(AppState.SPOTIFY)

        elif item == "Mixtapes":
            self._set_state(AppState.MIXTAPE)
            if self._mixtapes:
                self._play_mixtape(self._current_mixtape_idx)
            else:
                # Display shows "Loading..."; playback starts on MixtapesFetched
                self._request_mixtapes()

    def _toggle_live_channel(self):
        """Switch between live channels 1 and 2."""
        self._current_channel = 1 if self._current_channel == 2 else 2

        # Show whatever we already know about the new channel immediately,
        # before playback (which may block briefly starting mpv)
        self._channel_info = self._live_infos.get(self._current_channel)
        self._display_dirty = True
        self._update_display()

        self._play_current()
        self._request_live_refresh(force=True)

    # ── Playback ─────────────────────────────────────────────

    def _play_current(self):
        """Play the current live channel."""
        url = self._api.get_stream_url(self._current_channel)
        self._player.play(url)
        self._display_dirty = True

    def _play_mixtape(self, idx: int):
        """Play a specific mixtape by index."""
        if not (0 <= idx < len(self._mixtapes)):
            return
        self._current_mixtape_idx = idx
        self._display_dirty = True
        self._update_display()  # render selection before playback starts

        mixtape = self._mixtapes[idx]
        url = self._api.get_mixtape_stream_url(mixtape)
        if url:
            self._player.play(url)
            self._display_dirty = True

    # ── Fetch requests (loop thread) ─────────────────────────
    # Each spawns a worker that does the network I/O and posts the
    # result back as an event. Guards prevent overlapping fetches.

    def _spawn_worker(self, target, name: str):
        threading.Thread(target=target, daemon=True, name=name).start()

    def _request_live_refresh(self, force: bool = False):
        if self._live_fetch_pending:
            return
        self._live_fetch_pending = True

        def _worker():
            infos = {}
            try:
                self._api.get_live(force_refresh=force)
                for ch in LIVE_CHANNELS:
                    info = self._api.get_channel_info(ch)
                    if info:
                        infos[ch] = info
            except Exception:
                logger.exception("Live data fetch failed")
            finally:
                self._events.put(LiveInfoFetched(infos))

        self._spawn_worker(_worker, "live-fetch")

    def _request_mixtapes(self):
        if self._mixtapes_fetch_pending:
            return
        self._mixtapes_fetch_pending = True

        def _worker():
            items = []
            try:
                items = self._api.get_mixtapes()
            except Exception:
                logger.exception("Mixtapes fetch failed")
            finally:
                self._events.put(MixtapesFetched(items))

        self._spawn_worker(_worker, "mixtapes-fetch")

    def _request_artwork(self, url: str):
        if url in self._artwork_pending or url in self._artwork_cache:
            return
        self._artwork_pending.add(url)

        def _worker():
            image = None
            try:
                image = self._api.download_artwork(url)
            except Exception:
                logger.exception("Artwork fetch failed: %s", url)
            finally:
                self._events.put(ArtworkFetched(url, image))

        self._spawn_worker(_worker, "artwork-fetch")

    # ── Fetch results (loop thread) ──────────────────────────

    def _on_live_info(self, infos: dict):
        self._live_fetch_pending = False
        self._live_infos.update(infos)

        info = self._live_infos.get(self._current_channel)
        if info and info != self._channel_info:
            self._channel_info = info
            self._display_dirty = True

        if self._channel_info:
            artwork_url = self._channel_info.get("artwork_url")
            if artwork_url:
                self._request_artwork(artwork_url)

    def _on_mixtapes(self, items: list):
        self._mixtapes_fetch_pending = False
        if items:
            self._mixtapes = items
            self._display_dirty = True
            if self._get_state() == AppState.MIXTAPE:
                self._play_mixtape(self._current_mixtape_idx)

    def _on_artwork(self, url: str, image):
        self._artwork_pending.discard(url)
        if image is None:
            return

        # Limit cache size to save memory on Pi Zero
        if len(self._artwork_cache) > 10:
            for key in list(self._artwork_cache.keys())[:5]:
                del self._artwork_cache[key]
        self._artwork_cache[url] = image

        if self._channel_info and self._channel_info.get("artwork_url") == url:
            self._display_dirty = True
        if self._spotify_state.artwork_url == url:
            self._display_dirty = True

    # ── Spotify Connect ──────────────────────────────────────

    def _menu_items(self) -> list[str]:
        """Menu entries; "Spotify" only while a track is loaded."""
        if self._spotify_state.title:
            return self.MENU_ITEMS + ["Spotify"]
        return self.MENU_ITEMS

    def _spotify_command(self, command: str):
        if self._spotify is not None:
            self._spotify.send_command(command)

    def _on_spotify_state(self, new: SpotifyState):
        """Take over the screen while Spotify plays; NTS pauses."""
        old = self._spotify_state
        self._spotify_state = new

        if new.is_playing:
            self._spotify_idle_since = None
            if self._get_state() != AppState.SPOTIFY:
                current = self._get_state()
                self._state_before_spotify = (
                    self._state_before_menu if current == AppState.MENU else current
                )
                self._player.pause()
                self._set_state(AppState.SPOTIFY)
        elif self._spotify_idle_since is None:
            self._spotify_idle_since = time.time()

        if new.artwork_url and new.artwork_url != old.artwork_url:
            self._request_artwork(new.artwork_url)

        if self._get_state() == AppState.SPOTIFY:
            self._display_dirty = True

    def _check_spotify_idle(self, now: float):
        """Back to the previous NTS screen once Spotify has been idle a while.

        NTS stays paused; X resumes it.
        """
        if (
            self._get_state() == AppState.SPOTIFY
            and self._spotify_idle_since is not None
            and now - self._spotify_idle_since >= SPOTIFY_RETURN_DELAY
        ):
            self._spotify_idle_since = None
            self._set_state(self._state_before_spotify)

    # ── Display update ───────────────────────────────────────

    def _update_display(self):
        """Render the current state to the display."""
        if not self._display_dirty:
            return

        state = self._get_state()

        try:
            if state == AppState.LIVE:
                if self._channel_info:
                    artwork_url = self._channel_info.get("artwork_url")
                    artwork = self._artwork_cache.get(artwork_url) if artwork_url else None
                    self._display.render_live(
                        self._channel_info,
                        self._player.is_playing(),
                        artwork=artwork,
                    )
                else:
                    self._display.render_message(
                        "NTS RADIO", "Loading..."
                    )

            elif state == AppState.MIXTAPE:
                if self._mixtapes:
                    mixtape = self._mixtapes[self._current_mixtape_idx]
                    self._display.render_mixtape(
                        mixtape,
                        self._player.is_playing(),
                    )
                else:
                    self._display.render_message("MIXTAPES", "Loading...")

            elif state == AppState.MENU:
                self._display.render_menu(self._menu_items(), self._menu_selection)

            elif state == AppState.SPOTIFY:
                artwork_url = self._spotify_state.artwork_url
                artwork = self._artwork_cache.get(artwork_url) if artwork_url else None
                self._display.render_spotify(
                    self._spotify_state,
                    self._spotify_state.is_playing,
                    artwork=artwork,
                )

            self._display_dirty = False
        except Exception:
            logger.exception("Display update failed")

    def _cleanup(self):
        """Clean up all resources."""
        self._running = False

        if self._spotify:
            self._spotify.stop()
        if self._buttons:
            self._buttons.cleanup()
        if self._player:
            self._player.shutdown()
        if self._display:
            self._display.shutdown()

    # ── Main loop ────────────────────────────────────────────

    def start(self):
        """Initialize components and start the main loop."""
        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        logger.info("NTS Radio starting up")

        # Register signal handlers — they only flip the running flag;
        # cleanup happens on the loop thread after the loop exits
        signal.signal(signal.SIGTERM, lambda *_: self._signal_handler())
        signal.signal(signal.SIGINT, lambda *_: self._signal_handler())

        # Initialize components
        try:
            self._api = NTSClient()
            self._player = MPVPlayer()
            self._display = Display(
                brightness=self._config.get("display_brightness", 80)
            )
            self._buttons = ButtonHandler(
                debounce_ms=self._config.get("button_debounce_ms", 200)
            )
        except Exception:
            logger.exception("Failed to initialize components")
            self._cleanup()
            sys.exit(1)

        # Show loading screen
        self._display.render_message("NTS RADIO", "Starting up...")

        # Button presses just enqueue events; all logic runs on the loop
        for pin in (BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y):
            self._buttons.on_press(
                pin, lambda p=pin: self._events.put(ButtonPressed(p))
            )
        self._buttons.start()

        self._running = True

        # Start audio first — the stream URL is a constant, no need to
        # wait on the API before making sound
        self._play_current()

        # Fetch metadata in the background
        self._request_live_refresh()
        self._request_mixtapes()

        # Spotify Connect now-playing; harmless if Soloist never comes up
        self._spotify = SpotifyClient(
            lambda st: self._events.put(SpotifyStateChanged(st))
        )
        self._spotify.start()

        try:
            self._run_loop()
        finally:
            self._cleanup()

    def _run_loop(self):
        """Main event loop: drain events, check timers, render."""
        last_live_refresh = time.time()
        last_display_refresh = time.time()
        last_spotify_refresh = time.time()

        while self._running:
            try:
                event = self._events.get(timeout=TICK_INTERVAL)
            except queue.Empty:
                event = None

            try:
                if event is not None:
                    self._handle_event(event)

                now = time.time()

                # Periodic live data refresh
                if now - last_live_refresh >= LIVE_REFRESH_INTERVAL:
                    if self._get_state() == AppState.LIVE:
                        self._request_live_refresh(force=True)
                    last_live_refresh = now

                # Periodic display refresh (for progress bar)
                if now - last_display_refresh >= DISPLAY_REFRESH_INTERVAL:
                    if self._get_state() == AppState.LIVE:
                        self._display_dirty = True
                    last_display_refresh = now

                # Spotify: progress bar while playing, hand back when idle
                self._check_spotify_idle(now)
                if (
                    self._get_state() == AppState.SPOTIFY
                    and self._spotify_state.is_playing
                    and now - last_spotify_refresh >= SPOTIFY_REFRESH_INTERVAL
                ):
                    self._display_dirty = True
                    last_spotify_refresh = now

                # Update display if needed
                self._update_display()

            except Exception:
                logger.exception("Error in main loop")
                time.sleep(1)  # Back off on error

    def _signal_handler(self):
        """Handle SIGTERM/SIGINT: request loop exit, nothing more."""
        logger.info("Signal received, shutting down")
        self._running = False


def main():
    """Entry point."""
    app = NTSRadioApp()
    app.start()


if __name__ == "__main__":
    main()
