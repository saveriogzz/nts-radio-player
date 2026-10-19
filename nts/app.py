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
from nts.router_client import RouterClient, RouterState
from nts.sources import SOURCES, source_by_label
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

# The loop sleeps until the next timer is due or an event arrives; these
# bound how long it may sleep (seconds)
MIN_WAIT = 0.05
MAX_WAIT = 5.0

# How often to check that mpv is still alive when it should be playing
PLAYER_WATCHDOG_INTERVAL = 5

LIVE_CHANNELS = (1, 2)

# Return to the NTS screen after a source has been paused/idle this long (seconds)
SOURCE_RETURN_DELAY = 30
# Redraw interval while a source plays, for its progress bar (seconds)
SOURCE_REFRESH_INTERVAL = 1


class AppState(Enum):
    """Application screen states.

    SOURCE is the shared "now playing" screen of a non-NTS source; which one
    is in ``NTSRadioApp._screen_source``.
    """
    LIVE = auto()
    MIXTAPE = auto()
    MENU = auto()
    SOURCE = auto()


# Screens where NTS is the thing on screen (and plays)
NTS_SCREENS = (AppState.LIVE, AppState.MIXTAPE)

# ── Screen state machine ─────────────────────────────────────
# (screen, event) -> handler method name. ``None`` as screen matches any
# screen and is only used when no screen-specific entry exists. Events are
# button names ("a", "b", "x", "y", "hold_y"). Source screens are handled
# generically from nts.sources.SOURCES, so adding a source adds no rows.
TRANSITIONS = {
    (AppState.LIVE, "a"): "_live_switch_channel",
    (AppState.LIVE, "b"): "_live_switch_channel",
    (AppState.LIVE, "x"): "_nts_toggle_pause",
    (AppState.MIXTAPE, "a"): "_mixtape_prev",
    (AppState.MIXTAPE, "b"): "_mixtape_next",
    (AppState.MIXTAPE, "x"): "_nts_toggle_pause",
    (AppState.MENU, "a"): "_menu_up",
    (AppState.MENU, "b"): "_menu_down",
    (AppState.MENU, "x"): "_select_menu_item",
    (AppState.MENU, "y"): "_close_menu",
    (AppState.MENU, "hold_y"): "_close_menu",
    (AppState.SOURCE, "a"): "_source_button",
    (AppState.SOURCE, "b"): "_source_button",
    (AppState.SOURCE, "x"): "_source_button",
    (AppState.SOURCE, "hold_y"): "_back_to_nts",
    (None, "y"): "_open_menu",
    (None, "hold_y"): "_open_menu",  # elsewhere a hold is just a (slow) Y
}

BUTTON_EVENTS = {BUTTON_A: "a", BUTTON_B: "b", BUTTON_X: "x", BUTTON_Y: "y"}


# ── Events ───────────────────────────────────────────────────
# Everything that happens off the loop thread arrives as one of these.

@dataclass(frozen=True)
class ButtonPressed:
    button: int


@dataclass(frozen=True)
class ButtonLongPressed:
    button: int


@dataclass(frozen=True)
class LiveInfoFetched:
    """Per-channel info from a live-data fetch; empty dict on failure."""
    infos: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MixtapesFetched:
    items: list = field(default_factory=list)


@dataclass(frozen=True)
class RouterStateChanged:
    state: RouterState


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
        self._router: Optional[RouterClient] = None

        # Other sources via the router (loop thread only)
        self._router_state = RouterState()
        self._screen_source: Optional[str] = None  # source shown on SOURCE
        self._state_before_source = AppState.LIVE  # NTS screen to return to
        self._source_idle_since: Optional[float] = None
        self._menu_source: Optional[str] = None  # source screen the menu was opened from

        # Track what changed for display updates
        self._display_dirty = True


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

    def _set_state(self, new_state: AppState, source: Optional[str] = None):
        """State transition (loop thread only)."""
        old = self._describe_state()
        self._state = new_state
        self._screen_source = source if new_state == AppState.SOURCE else None
        self._display_dirty = True
        logger.info("State: %s -> %s", old, self._describe_state())

    def _get_state(self) -> AppState:
        return self._state

    def _describe_state(self) -> str:
        if self._state == AppState.SOURCE and self._screen_source:
            return f"SOURCE({self._screen_source})"
        return self._state.name

    def _dispatch(self, event: str):
        """Run the transition for ``event`` on the current screen."""
        handler = TRANSITIONS.get((self._state, event)) or TRANSITIONS.get((None, event))
        if handler is not None:
            getattr(self, handler)(event)

    # ── Event handling ───────────────────────────────────────

    def _handle_event(self, event):
        """Dispatch a single event (loop thread only)."""
        if isinstance(event, ButtonPressed):
            name = BUTTON_EVENTS.get(event.button)
            if name:
                self._dispatch(name)
        elif isinstance(event, ButtonLongPressed):
            if event.button == BUTTON_Y:
                self._dispatch("hold_y")
        elif isinstance(event, LiveInfoFetched):
            self._on_live_info(event.infos)
        elif isinstance(event, MixtapesFetched):
            self._on_mixtapes(event.items)
        elif isinstance(event, ArtworkFetched):
            self._on_artwork(event.url, event.image)
        elif isinstance(event, RouterStateChanged):
            self._on_router_state(event.state)
        else:
            logger.warning("Unknown event: %r", event)

    # ── Transition handlers: NTS screens ─────────────────────

    def _live_switch_channel(self, _event=None):
        self._toggle_live_channel()

    def _nts_toggle_pause(self, _event=None):
        self._player.toggle_pause()
        self._display_dirty = True

    def _mixtape_prev(self, _event=None):
        if self._mixtapes:
            self._play_mixtape((self._current_mixtape_idx - 1) % len(self._mixtapes))

    def _mixtape_next(self, _event=None):
        if self._mixtapes:
            self._play_mixtape((self._current_mixtape_idx + 1) % len(self._mixtapes))

    # ── Transition handlers: menu ────────────────────────────

    def _open_menu(self, _event=None):
        self._state_before_menu = self._state
        self._menu_source = self._screen_source
        self._set_state(AppState.MENU)
        self._menu_selection = 0

    def _close_menu(self, _event=None):
        self._set_state(self._state_before_menu, self._menu_source)

    def _menu_up(self, _event=None):
        self._menu_selection = (self._menu_selection - 1) % len(self._menu_items())
        self._display_dirty = True

    def _menu_down(self, _event=None):
        self._menu_selection = (self._menu_selection + 1) % len(self._menu_items())
        self._display_dirty = True

    def _menu_items(self) -> list[str]:
        """NTS entries, then each source that has something loaded."""
        items = list(self.MENU_ITEMS)
        for spec in SOURCES.values():
            if self._router_state.now_playing(spec.name).title:
                items.append(spec.label)
        return items

    def _select_menu_item(self, _event=None):
        """Handle menu item selection."""
        items = self._menu_items()
        item = items[min(self._menu_selection, len(items) - 1)]

        spec = source_by_label(item)
        if spec is not None:
            self._show_source(spec.name)
            return

        # Picking an NTS source releases whichever source owns the speaker
        self._release_active_source()

        if item == "Live Radio":
            self._set_state(AppState.LIVE)
            self._play_current()
            self._request_live_refresh()

        elif item == "Mixtapes":
            self._set_state(AppState.MIXTAPE)
            if self._mixtapes:
                self._play_mixtape(self._current_mixtape_idx)
            else:
                # Display shows "Loading..."; playback starts on MixtapesFetched
                self._request_mixtapes()

    # ── Transition handlers: source screens ──────────────────

    def _source_button(self, event: str):
        """A/B/X on a source screen: send that source's mapped command."""
        spec = SOURCES.get(self._screen_source or "")
        if spec is None:
            return
        command = spec.buttons.get(event)
        if command == "toggle":
            playing = self._router_state.now_playing(spec.name).is_playing
            command = "pause" if playing else "play"
        if command:
            self._source_command(spec.name, command)

    def _back_to_nts(self, _event=None):
        """Hold Y on a source screen: release it and resume NTS."""
        if self._screen_source:
            self._source_command(self._screen_source, "stop")
        if self._router_state.active in SOURCES and self._router_state.active != self._screen_source:
            self._source_command(self._router_state.active, "stop")
        self._source_idle_since = None

        # Resume whatever NTS screen was on before the source took over
        if self._state_before_source == AppState.MIXTAPE and self._mixtapes:
            self._set_state(AppState.MIXTAPE)
            self._play_mixtape(self._current_mixtape_idx)
        else:
            self._set_state(AppState.LIVE)
            self._play_current()
            self._request_live_refresh()

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
        if self._screen_source and self._router_state.now_playing(self._screen_source).artwork_url == url:
            self._display_dirty = True

    # ── Sources (via the router) ─────────────────────────────

    def _source_command(self, source: str, command: str):
        if self._router is not None:
            self._router.send_command(source, command)

    def _release_active_source(self):
        """Pause the source owning the speaker and free its device."""
        active = self._router_state.active
        if active in SOURCES:
            self._source_command(active, "stop")
        self._source_idle_since = None

    def _show_source(self, source: str):
        """Switch to a source's screen, remembering the NTS screen to return to."""
        if self._state == AppState.SOURCE and self._screen_source == source:
            return
        current = self._state
        if current == AppState.MENU:
            current = self._state_before_menu
        if current in NTS_SCREENS:
            self._state_before_source = current
        self._source_idle_since = None
        self._set_state(AppState.SOURCE, source)

    def _on_owner_change(self, old: Optional[str], new: Optional[str]):
        """The router moved the speaker to another source.

        Any non-NTS owner stops NTS (always, whatever is on screen) and gets
        its screen. NTS taking over needs nothing here: the app started NTS
        itself, and the router merely confirms it.
        """
        if new == old:
            return
        if new in SOURCES:
            # Stopping mpv entirely frees memory; X / hold-Y restarts it
            self._player.suspend()
            self._show_source(new)

    def _on_router_state(self, new: RouterState):
        """Follow the router: owner changes and now-playing updates."""
        old = self._router_state
        self._router_state = new

        if new.active != old.active:
            self._on_owner_change(old.active, new.active)

        if self._state == AppState.SOURCE and self._screen_source:
            if new.now_playing(self._screen_source).is_playing:
                self._source_idle_since = None
            elif self._source_idle_since is None:
                self._source_idle_since = time.time()
            self._display_dirty = True

        for name in SOURCES:
            url = new.now_playing(name).artwork_url
            if url and url != old.now_playing(name).artwork_url:
                self._request_artwork(url)

    def _check_source_idle(self, now: float):
        """Back to the previous NTS screen once a source has been idle a while.

        NTS stays paused; X resumes it.
        """
        if (
            self._state == AppState.SOURCE
            and self._source_idle_since is not None
            and now - self._source_idle_since >= SOURCE_RETURN_DELAY
        ):
            self._source_idle_since = None
            self._set_state(self._state_before_source)

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

            elif state == AppState.SOURCE:
                spec = SOURCES.get(self._screen_source or "")
                if spec is not None:
                    info = self._router_state.now_playing(spec.name)
                    artwork = self._artwork_cache.get(info.artwork_url) if info.artwork_url else None
                    self._display.render_now_playing(
                        spec.label, spec.accent, info, info.is_playing, artwork=artwork
                    )

            self._display_dirty = False
        except Exception:
            logger.exception("Display update failed")

    def _cleanup(self):
        """Clean up all resources."""
        self._running = False

        if self._router:
            self._router.stop()
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
        # Hold Y: back to NTS from Spotify
        self._buttons.on_long_press(
            BUTTON_Y, lambda: self._events.put(ButtonLongPressed(BUTTON_Y))
        )
        self._buttons.start()

        self._running = True

        # Start audio first — the stream URL is a constant, no need to
        # wait on the API before making sound
        self._play_current()

        # Fetch metadata in the background
        self._request_live_refresh()
        self._request_mixtapes()

        # Source router: who owns the speaker + Spotify now-playing; NTS works
        # as before if the router never comes up
        self._router = RouterClient(
            lambda st: self._events.put(RouterStateChanged(st))
        )
        self._router.start()

        try:
            self._run_loop()
        finally:
            self._cleanup()

    def _run_loop(self):
        """Main event loop: wait for an event or the next timer, then act."""
        now = time.time()
        next_live_refresh = now + LIVE_REFRESH_INTERVAL
        next_display_refresh = now + DISPLAY_REFRESH_INTERVAL
        next_source_refresh = now + SOURCE_REFRESH_INTERVAL
        next_watchdog = now + PLAYER_WATCHDOG_INTERVAL

        while self._running:
            deadlines = [next_live_refresh, next_display_refresh, next_watchdog]
            if self._state == AppState.SOURCE and self._screen_source:
                if self._router_state.now_playing(self._screen_source).is_playing:
                    deadlines.append(next_source_refresh)
                if self._source_idle_since is not None:
                    deadlines.append(self._source_idle_since + SOURCE_RETURN_DELAY)
            wait = min(deadlines) - time.time()
            wait = max(MIN_WAIT, min(MAX_WAIT, wait))

            try:
                event = self._events.get(timeout=wait)
            except queue.Empty:
                event = None

            try:
                if event is not None:
                    self._handle_event(event)

                now = time.time()

                # Periodic live data refresh
                if now >= next_live_refresh:
                    if self._get_state() == AppState.LIVE:
                        self._request_live_refresh(force=True)
                    next_live_refresh = now + LIVE_REFRESH_INTERVAL

                # Periodic display refresh (for progress bar)
                if now >= next_display_refresh:
                    if self._get_state() == AppState.LIVE:
                        self._display_dirty = True
                    next_display_refresh = now + DISPLAY_REFRESH_INTERVAL

                # Restart mpv if the stream died (network blip); not while
                # another source owns the output
                if now >= next_watchdog:
                    if self._router_state.active not in SOURCES and self._player.ensure_playing():
                        self._display_dirty = True
                    next_watchdog = now + PLAYER_WATCHDOG_INTERVAL

                # Source screen: progress bar while playing, hand back when idle
                self._check_source_idle(now)
                if (
                    self._state == AppState.SOURCE
                    and self._screen_source
                    and self._router_state.now_playing(self._screen_source).is_playing
                    and now >= next_source_refresh
                ):
                    self._display_dirty = True
                    next_source_refresh = now + SOURCE_REFRESH_INTERVAL

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
