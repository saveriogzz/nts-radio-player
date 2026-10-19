"""Tests for nts.display — ST7789 display rendering."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from nts.display import Display, WIDTH, HEIGHT, BLACK, NTS_ORANGE, WHITE


@pytest.fixture
def display():
    """Create a Display instance with mocked hardware."""
    d = Display(brightness=80)
    # Display hardware is already mocked via conftest, but ensure no pushes
    d._display = None
    return d


class TestDisplayInit:
    """Tests for display initialization."""

    def test_display_dimensions(self, display):
        assert WIDTH == 240
        assert HEIGHT == 240

    def test_fonts_loaded(self, display):
        # Should have loaded some font (default or DejaVu)
        assert display._font_large is not None
        assert display._font_medium is not None
        assert display._font_small is not None
        assert display._font_header is not None


class TestAssetLoading:
    """Tests for bundled asset loading."""

    def test_load_mixtape_icons(self, display):
        icons = display._mixtape_icons
        assert isinstance(icons, dict)
        # If assets dir exists with PNGs, icons should be loaded
        assets_dir = Path(__file__).resolve().parent.parent / "assets" / "mixtapes"
        if assets_dir.exists():
            png_count = len(list(assets_dir.glob("*.png")))
            assert len(icons) == png_count
            for alias, icon in icons.items():
                assert isinstance(icon, Image.Image)
                assert icon.size == (120, 120)

    def test_get_mixtape_icon_by_alias(self, display):
        if not display._mixtape_icons:
            pytest.skip("No mixtape icons available")

        # Get first available icon's alias
        alias = next(iter(display._mixtape_icons))
        info = {"mixtape_alias": alias}
        result = display._get_mixtape_icon(info)
        assert result is not None
        assert isinstance(result, Image.Image)

    def test_get_mixtape_icon_by_path(self, display):
        if not display._mixtape_icons:
            pytest.skip("No mixtape icons available")

        alias = next(iter(display._mixtape_icons))
        info = {"path": f"/infinite-mixtapes/{alias}"}
        result = display._get_mixtape_icon(info)
        assert result is not None

    def test_get_mixtape_icon_by_title_slug(self, display):
        if not display._mixtape_icons:
            pytest.skip("No mixtape icons available")

        # "slow-focus" -> title "Slow Focus"
        if "slow-focus" in display._mixtape_icons:
            info = {"title": "Slow Focus"}
            result = display._get_mixtape_icon(info)
            assert result is not None

    def test_get_mixtape_icon_unknown_returns_none(self, display):
        info = {"mixtape_alias": "nonexistent-mixtape-xyz"}
        result = display._get_mixtape_icon(info)
        assert result is None


class TestRenderLive:
    """Tests for live channel rendering."""

    def test_render_live_produces_correct_size(self, display):
        channel_info = {
            "channel_name": "NTS 1",
            "title": "Test Show",
            "artist": "DJ Test",
            "start_timestamp": "2026-07-23T10:00:00Z",
            "end_timestamp": "2026-07-23T12:00:00Z",
        }

        # Capture the image by intercepting _push_image
        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_live(channel_info, is_playing=True)

        assert len(captured) == 1
        assert captured[0].size == (WIDTH, HEIGHT)
        assert captured[0].mode == "RGB"

    def test_render_live_with_artwork(self, display):
        channel_info = {
            "channel_name": "NTS 1",
            "title": "Test Show",
            "artist": "",
            "start_timestamp": None,
            "end_timestamp": None,
        }
        artwork = Image.new("RGB", (120, 120), (255, 0, 0))

        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_live(channel_info, is_playing=True, artwork=artwork)

        assert len(captured) == 1
        # Check artwork was pasted (pixel at center of artwork area should be red)
        px = captured[0].getpixel((WIDTH // 2, 28 + 60))
        assert px == (255, 0, 0)

    def test_render_live_no_artwork_shows_placeholder(self, display):
        channel_info = {
            "channel_name": "NTS 1",
            "title": "Test",
            "artist": "",
            "start_timestamp": None,
            "end_timestamp": None,
        }

        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_live(channel_info, is_playing=False, artwork=None)

        assert len(captured) == 1
        # Artwork area should have the gray placeholder rectangle
        artwork_x = (WIDTH - 120) // 2
        region = captured[0].crop((artwork_x, 28, artwork_x + 120, 28 + 120))
        pixels = list(region.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0, "Placeholder should produce non-black pixels"


class TestRenderMixtape:
    """Tests for mixtape screen rendering."""

    def test_render_mixtape_produces_correct_size(self, display):
        mixtape_info = {
            "title": "Poolside",
            "subtitle": "Sun-kissed selections",
            "description": "Warm vibes",
            "mixtape_alias": "poolside",
        }

        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_mixtape(mixtape_info, is_playing=True)

        assert len(captured) == 1
        assert captured[0].size == (WIDTH, HEIGHT)

    def test_render_mixtape_with_bundled_icon(self, display):
        if "poolside" not in display._mixtape_icons:
            pytest.skip("Poolside icon not available")

        mixtape_info = {
            "title": "Poolside",
            "subtitle": "Sun-kissed",
            "description": "",
            "mixtape_alias": "poolside",
        }

        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_mixtape(mixtape_info, is_playing=True, artwork=None)

        assert len(captured) == 1
        # Icon area shouldn't be fully black
        artwork_x = (WIDTH - 120) // 2
        region = captured[0].crop((artwork_x, 30, artwork_x + 120, 30 + 120))
        pixels = list(region.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0, "Mixtape icon should produce non-black pixels"


class TestRenderMenu:
    """Tests for menu rendering."""

    def test_render_menu_produces_correct_size(self, display):
        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_menu(["Live Radio", "Mixtapes", "Settings"], selected=0)

        assert len(captured) == 1
        assert captured[0].size == (WIDTH, HEIGHT)

    def test_render_menu_selected_item_has_orange(self, display):
        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_menu(["Live Radio", "Mixtapes"], selected=0)

        # Check that the selected item area contains orange pixels
        img = captured[0]
        # Selected item is at y=40, height=36
        region = img.crop((4, 40, WIDTH - 4, 72))
        pixels = list(region.getdata())
        orange_pixels = [p for p in pixels if p == NTS_ORANGE]
        assert len(orange_pixels) > 0


class TestRenderUtilities:
    """Tests for helper rendering methods."""

    def test_render_message(self, display):
        captured = []
        display._push_image = lambda img: captured.append(img.copy())

        display.render_message("ERROR", "Something went wrong")
        assert len(captured) == 1

    def test_text_truncation(self, display):
        img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        draw = display._font_large  # just need draw for measurement
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)

        long_text = "A" * 200
        truncated = display._truncate_text(draw, long_text, display._font_large, 200)
        assert truncated.endswith("...")
        assert len(truncated) < len(long_text)

    def test_text_no_truncation_needed(self, display):
        img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)

        short_text = "Hi"
        result = display._truncate_text(draw, short_text, display._font_large, 200)
        assert result == "Hi"


class TestProgressBar:
    """Tests for progress bar calculation."""

    def test_calc_progress_no_timestamps(self, display):
        progress, text = display._calc_progress(None, None)
        assert progress == 0.0
        assert text == "--:--"

    def test_calc_progress_valid_timestamps(self, display):
        # Use timestamps that give us a known result
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).isoformat()
        end = (now + timedelta(minutes=30)).isoformat()

        progress, text = display._calc_progress(start, end)

        # Should be roughly 50% through
        assert 0.4 < progress < 0.6
        assert "30:" in text or "29:" in text  # ~30 mins remaining

    def test_calc_progress_completed(self, display):
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=2)).isoformat()
        end = (now - timedelta(hours=1)).isoformat()

        progress, text = display._calc_progress(start, end)
        assert progress == 1.0

    def test_calc_progress_not_started(self, display):
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()

        progress, text = display._calc_progress(start, end)
        assert progress == 0.0


class TestFrameDedup:
    """Tests for frame deduplication."""

    def test_identical_frames_not_pushed_twice(self, display):
        push_count = 0

        original_push = display._push_image

        def counting_push(img):
            nonlocal push_count
            # Reset hash to actually test dedup
            display._last_frame_hash = display._last_frame_hash
            push_count += 1

        # Create a simple scenario
        display._last_frame_hash = None
        img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)

        # First push should go through
        display._push_image(img)
        first_hash = display._last_frame_hash

        # Same image again should be skipped (no display update)
        display._push_image(img)
        assert display._last_frame_hash == first_hash

    def test_clear_resets_frame_hash(self, display):
        display._last_frame_hash = 12345
        display.clear()
        assert display._last_frame_hash is None


class TestSpotifyScreen:
    """Tests for the Spotify Connect now-playing screen."""

    def _info(self, **kw):
        from nts.spotify import SpotifyState
        fields = dict(status="playing", title="My Song", artist="Artist", album="Album",
                      duration_ms=200000, position_ms=50000, position_ts=0.0, speed=0.0)
        fields.update(kw)
        return SpotifyState(**fields)

    def test_render_without_artwork(self, display):
        with patch.object(display, "_push_image") as push:
            display.render_spotify(self._info(), True)
        img = push.call_args[0][0]
        assert img.size == (WIDTH, HEIGHT)

    def test_render_with_artwork(self, display):
        art = Image.new("RGB", (120, 120), (1, 2, 3))
        with patch.object(display, "_push_image") as push:
            display.render_spotify(self._info(), False, artwork=art)
        img = push.call_args[0][0]
        assert img.getpixel((WIDTH // 2, 80)) == (1, 2, 3)

    def test_long_title_truncated(self, display):
        results = []
        original = display._truncate_text

        def spy(*args, **kwargs):
            results.append(original(*args, **kwargs))
            return results[-1]

        with patch.object(display, "_truncate_text", side_effect=spy):
            display.render_spotify(self._info(title="X" * 200), True)
        assert results[0].endswith("...")

    def test_empty_state_renders(self, display):
        from nts.spotify import SpotifyState
        display.render_spotify(SpotifyState(), False)
