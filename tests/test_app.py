"""Tests for nts.app — main application state machine."""

import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from tests.conftest import SAMPLE_LIVE_RESPONSE, SAMPLE_MIXTAPES_RESPONSE


def _make_app():
    """Create an NTSRadioApp with mocked components."""
    from nts.app import NTSRadioApp

    app = NTSRadioApp()
    app._api = MagicMock()
    app._player = MagicMock()
    app._display = MagicMock()
    app._buttons = MagicMock()

    # Set up default API behavior
    app._api.get_channel_info.return_value = {
        "channel_name": "NTS 1",
        "title": "Test Show",
        "artist": "DJ Test",
        "artwork_url": None,
        "start_timestamp": "2026-07-23T10:00:00Z",
        "end_timestamp": "2026-07-23T12:00:00Z",
    }
    app._api.get_stream_url.return_value = "https://stream-relay-geo.ntslive.net/stream"
    app._api.get_mixtapes.return_value = SAMPLE_MIXTAPES_RESPONSE["results"]
    app._api.get_mixtape_stream_url.return_value = "https://stream-mixtape.ntslive.net/mixtape-poolside"
    app._player.is_playing.return_value = True
    app._player.get_current_url.return_value = "https://stream-relay-geo.ntslive.net/stream"

    return app


class TestConfigLoading:
    """Tests for environment-based config loading."""

    def test_default_config(self):
        from nts.app import NTSRadioApp

        app = NTSRadioApp()
        assert app._config["default_channel"] == 1
        assert app._config["display_brightness"] == 80
        assert app._config["button_debounce_ms"] == 200

    def test_config_from_env(self, monkeypatch):
        from nts.app import NTSRadioApp

        monkeypatch.setenv("NTS_DEFAULT_CHANNEL", "2")
        monkeypatch.setenv("NTS_DISPLAY_BRIGHTNESS", "50")
        monkeypatch.setenv("NTS_BUTTON_DEBOUNCE_MS", "300")

        app = NTSRadioApp()
        assert app._config["default_channel"] == 2
        assert app._config["display_brightness"] == 50
        assert app._config["button_debounce_ms"] == 300

    def test_config_invalid_env_uses_default(self, monkeypatch):
        from nts.app import NTSRadioApp

        monkeypatch.setenv("NTS_DEFAULT_CHANNEL", "not_a_number")

        app = NTSRadioApp()
        assert app._config["default_channel"] == 1

    def test_config_out_of_range_channel_uses_default(self, monkeypatch):
        from nts.app import NTSRadioApp

        monkeypatch.setenv("NTS_DEFAULT_CHANNEL", "3")

        app = NTSRadioApp()
        assert app._config["default_channel"] == 1


class TestStateMachine:
    """Tests for state transitions."""

    def test_initial_state_is_live(self):
        from nts.app import AppState

        app = _make_app()
        assert app._get_state() == AppState.LIVE

    def test_button_y_opens_menu(self):
        from nts.app import AppState

        app = _make_app()
        app._on_button_y()
        assert app._get_state() == AppState.MENU
        assert app._menu_selection == 0

    def test_button_y_closes_menu(self):
        from nts.app import AppState

        app = _make_app()
        app._on_button_y()  # open menu
        assert app._get_state() == AppState.MENU

        app._on_button_y()  # close menu -> back to live
        assert app._get_state() == AppState.LIVE

    def test_menu_select_live_radio(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MENU)
        app._menu_selection = 0  # "Live Radio"

        app._on_button_x()  # select
        assert app._get_state() == AppState.LIVE
        app._player.play.assert_called()

    def test_menu_select_mixtapes(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MENU)
        app._menu_selection = 1  # "Mixtapes"

        app._on_button_x()  # select
        assert app._get_state() == AppState.MIXTAPE

    def test_menu_navigation_wraps(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MENU)
        app._menu_selection = 0

        app._on_button_a()  # scroll up from top -> wraps to bottom
        assert app._menu_selection == len(app.MENU_ITEMS) - 1

        app._on_button_b()  # scroll down from bottom -> wraps to top
        assert app._menu_selection == 0


class TestLiveChannelSwitching:
    """Tests for live channel switching."""

    def test_button_a_toggles_channel(self):
        app = _make_app()
        assert app._current_channel == 1

        app._on_button_a()
        assert app._current_channel == 2

        app._on_button_a()
        assert app._current_channel == 1

    def test_button_b_toggles_channel(self):
        app = _make_app()
        assert app._current_channel == 1

        app._on_button_b()
        assert app._current_channel == 2

    def test_channel_switch_triggers_playback(self):
        app = _make_app()
        app._on_button_a()
        app._player.play.assert_called()

    def test_play_pause_in_live(self):
        app = _make_app()
        app._on_button_x()
        app._player.toggle_pause.assert_called_once()


class TestMixtapeNavigation:
    """Tests for mixtape browsing."""

    def test_mixtape_scroll(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MIXTAPE)
        app._mixtapes = SAMPLE_MIXTAPES_RESPONSE["results"]
        app._current_mixtape_idx = 0

        app._on_button_b()  # next
        assert app._current_mixtape_idx == 1

        app._on_button_b()  # wraps
        assert app._current_mixtape_idx == 0

        app._on_button_a()  # prev wraps
        assert app._current_mixtape_idx == 1

    def test_play_pause_in_mixtape(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MIXTAPE)
        app._on_button_x()
        app._player.toggle_pause.assert_called_once()


class TestDisplayUpdate:
    """Tests for display rendering dispatch."""

    def test_live_display_renders_channel_info(self):
        from nts.app import AppState

        app = _make_app()
        app._channel_info = {
            "channel_name": "NTS 1",
            "title": "Test",
            "artwork_url": None,
        }
        app._display_dirty = True
        app._update_display()

        app._display.render_live.assert_called_once()

    def test_mixtape_display_renders(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MIXTAPE)
        app._mixtapes = SAMPLE_MIXTAPES_RESPONSE["results"]
        app._display_dirty = True
        app._update_display()

        app._display.render_mixtape.assert_called_once()

    def test_menu_display_renders(self):
        from nts.app import AppState

        app = _make_app()
        app._set_state(AppState.MENU)
        app._display_dirty = True
        app._update_display()

        app._display.render_menu.assert_called_once()

    def test_display_not_updated_when_clean(self):
        app = _make_app()
        app._channel_info = {"channel_name": "NTS 1", "title": "Test", "artwork_url": None}
        app._display_dirty = False
        app._update_display()

        app._display.render_live.assert_not_called()

    def test_loading_screen_when_no_channel_info(self):
        app = _make_app()
        app._channel_info = None
        app._display_dirty = True
        app._update_display()

        app._display.render_message.assert_called_with("NTS RADIO", "Loading...")


class TestReturnFromMenu:
    """Tests for returning to the correct screen from menu."""

    def test_returns_to_live_when_opened_from_live(self):
        from nts.app import AppState

        app = _make_app()
        app._on_button_y()  # open menu from LIVE
        assert app._get_state() == AppState.MENU

        app._on_button_y()  # close
        assert app._get_state() == AppState.LIVE

    def test_returns_to_mixtape_when_opened_from_mixtape(self):
        from nts.app import AppState

        app = _make_app()
        app._mixtapes = SAMPLE_MIXTAPES_RESPONSE["results"]
        app._set_state(AppState.MIXTAPE)

        app._on_button_y()  # open menu from MIXTAPE
        assert app._get_state() == AppState.MENU

        app._on_button_y()  # close
        assert app._get_state() == AppState.MIXTAPE


class TestEventHandling:
    """Tests for the event queue dispatch."""

    def test_button_event_dispatches_to_handler(self):
        from nts.app import ButtonPressed
        from nts.buttons import BUTTON_X

        app = _make_app()
        app._handle_event(ButtonPressed(BUTTON_X))
        app._player.toggle_pause.assert_called_once()

    def test_live_info_updates_current_channel(self):
        from nts.app import LiveInfoFetched

        app = _make_app()
        app._display_dirty = False
        info = {"channel_name": "NTS 1", "title": "New Show", "artwork_url": None}

        app._handle_event(LiveInfoFetched({1: info, 2: {"title": "Other"}}))

        assert app._channel_info == info
        assert app._display_dirty
        assert not app._live_fetch_pending

    def test_live_info_requests_artwork(self):
        from nts.app import LiveInfoFetched

        app = _make_app()
        app._request_artwork = MagicMock()
        info = {
            "channel_name": "NTS 1",
            "title": "New Show",
            "artwork_url": "https://media.ntslive.co.uk/art.jpg",
        }

        app._handle_event(LiveInfoFetched({1: info}))

        app._request_artwork.assert_called_once_with("https://media.ntslive.co.uk/art.jpg")

    def test_failed_live_fetch_clears_pending_flag(self):
        from nts.app import LiveInfoFetched

        app = _make_app()
        app._live_fetch_pending = True
        app._handle_event(LiveInfoFetched({}))
        assert not app._live_fetch_pending

    def test_mixtapes_fetched_starts_playback_in_mixtape_state(self):
        from nts.app import AppState, MixtapesFetched

        app = _make_app()
        app._set_state(AppState.MIXTAPE)

        app._handle_event(MixtapesFetched(SAMPLE_MIXTAPES_RESPONSE["results"]))

        assert app._mixtapes == SAMPLE_MIXTAPES_RESPONSE["results"]
        app._player.play.assert_called()

    def test_mixtapes_fetched_does_not_play_in_other_states(self):
        from nts.app import MixtapesFetched

        app = _make_app()
        app._handle_event(MixtapesFetched(SAMPLE_MIXTAPES_RESPONSE["results"]))

        assert app._mixtapes == SAMPLE_MIXTAPES_RESPONSE["results"]
        app._player.play.assert_not_called()

    def test_artwork_fetched_populates_cache(self):
        from nts.app import ArtworkFetched

        app = _make_app()
        img = MagicMock()
        app._artwork_pending.add("https://media.ntslive.co.uk/art.jpg")

        app._handle_event(ArtworkFetched("https://media.ntslive.co.uk/art.jpg", img))

        assert app._artwork_cache["https://media.ntslive.co.uk/art.jpg"] is img
        assert "https://media.ntslive.co.uk/art.jpg" not in app._artwork_pending

    def test_failed_artwork_not_cached(self):
        from nts.app import ArtworkFetched

        app = _make_app()
        app._artwork_pending.add("https://media.ntslive.co.uk/art.jpg")

        app._handle_event(ArtworkFetched("https://media.ntslive.co.uk/art.jpg", None))

        assert "https://media.ntslive.co.uk/art.jpg" not in app._artwork_cache
        assert "https://media.ntslive.co.uk/art.jpg" not in app._artwork_pending

    def test_artwork_request_deduplicates(self):
        app = _make_app()
        app._spawn_worker = MagicMock()

        app._request_artwork("https://media.ntslive.co.uk/art.jpg")
        app._request_artwork("https://media.ntslive.co.uk/art.jpg")

        assert app._spawn_worker.call_count == 1

    def test_live_refresh_request_deduplicates(self):
        app = _make_app()
        app._spawn_worker = MagicMock()

        app._request_live_refresh()
        app._request_live_refresh()

        assert app._spawn_worker.call_count == 1


class TestSpotifyConnect:
    """The router says Spotify owns the speaker: screen switches, NTS stops."""

    @staticmethod
    def _state(status="playing", title="Song", artwork_url=None, active="spotify"):
        from nts.router_client import RouterState
        from router.state import SpotifyState
        spotify = SpotifyState(status=status, title=title, artist="Artist", artwork_url=artwork_url)
        playing = frozenset({"spotify"}) if status == "playing" else frozenset()
        return RouterState(active=active, playing=playing, spotify=spotify, connected=True)

    def _app(self):
        app = _make_app()
        app._router = MagicMock()
        return app

    @staticmethod
    def _sent(app):
        return [c.args for c in app._router.send_command.call_args_list]

    def test_spotify_owner_stops_nts_and_shows_spotify(self):
        from nts.app import AppState
        app = self._app()
        app._on_router_state(self._state())
        assert app._get_state() == AppState.SPOTIFY
        app._player.suspend.assert_called_once()

    def test_spotify_playing_but_not_owner_does_nothing(self):
        from nts.app import AppState
        app = self._app()
        app._on_router_state(self._state(active="nts"))
        assert app._get_state() == AppState.LIVE
        app._player.suspend.assert_not_called()

    def test_playing_from_mixtape_remembers_mixtape(self):
        from nts.app import AppState
        app = self._app()
        app._set_state(AppState.MIXTAPE)
        app._on_router_state(self._state())
        assert app._state_before_spotify == AppState.MIXTAPE

    def test_playing_from_menu_remembers_screen_under_menu(self):
        from nts.app import AppState
        app = self._app()
        app._on_button_y()  # open menu from LIVE
        app._on_router_state(self._state())
        assert app._get_state() == AppState.SPOTIFY
        assert app._state_before_spotify == AppState.LIVE

    def test_idle_returns_after_delay_without_resuming_nts(self):
        from nts.app import AppState, SPOTIFY_RETURN_DELAY
        app = self._app()
        with patch("nts.app.time.time", return_value=1000.0):
            app._on_router_state(self._state())
            app._on_router_state(self._state(status="paused"))
        app._check_spotify_idle(1000.0 + SPOTIFY_RETURN_DELAY - 1)
        assert app._get_state() == AppState.SPOTIFY
        app._check_spotify_idle(1000.0 + SPOTIFY_RETURN_DELAY)
        assert app._get_state() == AppState.LIVE
        app._player.play.assert_not_called()
        app._player.toggle_pause.assert_not_called()

    def test_resuming_spotify_cancels_return(self):
        from nts.app import AppState, SPOTIFY_RETURN_DELAY
        app = self._app()
        with patch("nts.app.time.time", return_value=1000.0):
            app._on_router_state(self._state())
            app._on_router_state(self._state(status="paused"))
            app._on_router_state(self._state())
        app._check_spotify_idle(1000.0 + SPOTIFY_RETURN_DELAY * 2)
        assert app._get_state() == AppState.SPOTIFY

    def test_buttons_send_spotify_commands_via_router(self):
        app = self._app()
        app._on_router_state(self._state())
        app._on_button_x()
        app._on_button_a()
        app._on_button_b()
        assert self._sent(app) == [("spotify", "pause"), ("spotify", "skip_prev"), ("spotify", "skip_next")]

    def test_x_plays_when_spotify_paused(self):
        app = self._app()
        app._on_router_state(self._state())
        app._on_router_state(self._state(status="paused"))
        app._on_button_x()
        app._router.send_command.assert_called_with("spotify", "play")

    def test_menu_live_radio_releases_spotify(self):
        from nts.app import AppState
        app = self._app()
        app._on_router_state(self._state())
        app._on_button_y()  # menu
        app._menu_selection = 0  # Live Radio
        app._on_button_x()
        assert self._sent(app)[-1] == ("spotify", "stop")
        assert app._get_state() == AppState.LIVE
        app._player.play.assert_called()

    def test_menu_offers_spotify_while_track_loaded(self):
        from nts.app import AppState
        app = self._app()
        assert "Spotify" not in app._menu_items()
        app._on_router_state(self._state())
        assert app._menu_items()[-1] == "Spotify"
        app._on_button_y()
        app._menu_selection = len(app._menu_items()) - 1
        app._on_button_x()
        assert app._get_state() == AppState.SPOTIFY

    def test_spotify_artwork_requested_and_rendered(self):
        app = self._app()
        app._on_router_state(self._state(artwork_url="https://i.scdn.co/c"))
        assert "https://i.scdn.co/c" in app._artwork_pending
        app._update_display()
        app._display.render_spotify.assert_called_once()

    def test_router_event_dispatched(self):
        from nts.app import AppState, RouterStateChanged
        app = self._app()
        app._handle_event(RouterStateChanged(self._state()))
        assert app._get_state() == AppState.SPOTIFY

    def test_router_disconnect_keeps_current_screen(self):
        from nts.app import AppState
        from nts.router_client import RouterState
        app = self._app()
        app._on_router_state(RouterState(connected=False))
        assert app._get_state() == AppState.LIVE


class TestBackToNts:
    """Hold Y on the Spotify screen: release Spotify, resume NTS."""

    def _app_on_spotify(self, from_state=None):
        from nts.app import AppState
        app = _make_app()
        app._router = MagicMock()
        if from_state is not None:
            app._set_state(from_state)
        app._on_router_state(TestSpotifyConnect._state())
        assert app._get_state() == AppState.SPOTIFY
        app._player.reset_mock()
        return app

    def test_hold_y_returns_to_live_and_plays(self):
        from nts.app import AppState
        app = self._app_on_spotify()
        app._on_button_y_long()
        assert app._get_state() == AppState.LIVE
        app._player.play.assert_called_once()
        app._router.send_command.assert_called_once_with("spotify", "stop")

    def test_hold_y_returns_to_mixtape_when_that_was_playing(self):
        from nts.app import AppState
        app = _make_app()
        app._mixtapes = SAMPLE_MIXTAPES_RESPONSE["results"]
        app._router = MagicMock()
        app._set_state(AppState.MIXTAPE)
        app._on_router_state(TestSpotifyConnect._state())
        app._on_button_y_long()
        assert app._get_state() == AppState.MIXTAPE
        app._api.get_mixtape_stream_url.assert_called()

    def test_late_updates_while_spotify_still_owner_dont_switch_back(self):
        from nts.app import AppState
        app = self._app_on_spotify()
        app._on_button_y_long()
        # Router still reports Spotify as owner until mpv's stream is running
        app._on_router_state(TestSpotifyConnect._state())
        assert app._get_state() == AppState.LIVE
        app._player.suspend.assert_not_called()

    def test_casting_again_after_nts_took_over_switches(self):
        from nts.app import AppState
        app = self._app_on_spotify()
        app._on_button_y_long()
        app._on_router_state(TestSpotifyConnect._state(status="paused", active="nts"))
        app._on_router_state(TestSpotifyConnect._state())
        assert app._get_state() == AppState.SPOTIFY

    def test_hold_y_on_nts_screens_acts_like_y(self):
        from nts.app import AppState
        app = _make_app()
        app._router = MagicMock()
        app._on_button_y_long()
        assert app._get_state() == AppState.MENU
        app._router.send_command.assert_not_called()
        app._player.play.assert_not_called()

    def test_long_press_event_dispatched(self):
        from nts.app import AppState, ButtonLongPressed
        from nts.buttons import BUTTON_Y
        app = self._app_on_spotify()
        app._handle_event(ButtonLongPressed(BUTTON_Y))
        assert app._get_state() == AppState.LIVE
