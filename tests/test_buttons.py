"""Tests for nts.buttons — GPIO button handler."""

import time
import threading
from unittest.mock import MagicMock, call

import pytest

from nts.buttons import (
    ButtonHandler,
    BUTTON_A,
    BUTTON_B,
    BUTTON_X,
    BUTTON_Y,
    ALL_BUTTONS,
    BUTTON_NAMES,
    LONG_PRESS_THRESHOLD_S,
)


class TestButtonConstants:
    """Tests for button pin constants."""

    def test_button_pins_are_distinct(self):
        pins = [BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y]
        assert len(set(pins)) == 4

    def test_all_buttons_list(self):
        assert set(ALL_BUTTONS) == {BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y}

    def test_button_names(self):
        assert BUTTON_NAMES[BUTTON_A] == "A"
        assert BUTTON_NAMES[BUTTON_B] == "B"
        assert BUTTON_NAMES[BUTTON_X] == "X"
        assert BUTTON_NAMES[BUTTON_Y] == "Y"


class TestButtonInit:
    """Tests for button handler initialization."""

    def test_init_with_default_debounce(self):
        handler = ButtonHandler()
        assert handler._debounce_ms == 200

    def test_init_with_custom_debounce(self):
        handler = ButtonHandler(debounce_ms=300)
        assert handler._debounce_ms == 300

    def test_gpio_initialized(self, mock_gpio):
        handler = ButtonHandler()
        assert handler._gpio_available is True
        # GPIO should have been set up for all 4 buttons
        assert mock_gpio.setup.call_count == 4


class TestCallbackRegistration:
    """Tests for callback registration."""

    def test_register_press_callback(self):
        handler = ButtonHandler()
        cb = MagicMock()
        handler.on_press(BUTTON_A, cb)
        assert handler._callbacks[BUTTON_A] == cb

    def test_register_long_press_callback(self):
        handler = ButtonHandler()
        cb = MagicMock()
        handler.on_long_press(BUTTON_Y, cb)
        assert handler._long_press_callbacks[BUTTON_Y] == cb

    def test_register_multiple_callbacks(self):
        handler = ButtonHandler()
        cb_a = MagicMock()
        cb_b = MagicMock()
        handler.on_press(BUTTON_A, cb_a)
        handler.on_press(BUTTON_B, cb_b)
        assert handler._callbacks[BUTTON_A] == cb_a
        assert handler._callbacks[BUTTON_B] == cb_b


class TestButtonCallback:
    """Tests for button press/release detection."""

    def test_button_callback_records_press_time(self):
        handler = ButtonHandler()
        before = time.time()
        handler._button_callback(BUTTON_A)
        after = time.time()

        assert BUTTON_A in handler._press_times
        assert before <= handler._press_times[BUTTON_A] <= after

    def test_short_press_fires_press_callback(self, mock_gpio):
        handler = ButtonHandler()
        cb = MagicMock()
        handler.on_press(BUTTON_A, cb)

        # Simulate: button pressed, then released quickly
        mock_gpio.input.return_value = 1  # released
        handler._press_times[BUTTON_A] = time.time() - 0.1  # 100ms ago

        handler._check_release(BUTTON_A)
        cb.assert_called_once()

    def test_long_press_fires_long_press_callback(self, mock_gpio):
        handler = ButtonHandler()
        short_cb = MagicMock()
        long_cb = MagicMock()
        handler.on_press(BUTTON_Y, short_cb)
        handler.on_long_press(BUTTON_Y, long_cb)

        # Simulate: button held for >1s
        mock_gpio.input.return_value = 1  # released
        handler._press_times[BUTTON_Y] = time.time() - (LONG_PRESS_THRESHOLD_S + 0.1)

        handler._check_release(BUTTON_Y)
        long_cb.assert_called_once()
        short_cb.assert_not_called()

    def test_button_still_held_no_callback(self, mock_gpio):
        handler = ButtonHandler()
        cb = MagicMock()
        handler.on_press(BUTTON_A, cb)

        # Button still pressed (active low = 0)
        mock_gpio.input.return_value = 0
        handler._press_times[BUTTON_A] = time.time()

        handler._check_release(BUTTON_A)
        cb.assert_not_called()

    def test_check_release_no_press_time(self, mock_gpio):
        """check_release with no recorded press should be a no-op."""
        handler = ButtonHandler()
        cb = MagicMock()
        handler.on_press(BUTTON_A, cb)

        handler._check_release(BUTTON_A)
        cb.assert_not_called()

    def test_callback_exception_doesnt_crash(self, mock_gpio):
        """Exceptions in callbacks should be caught."""
        handler = ButtonHandler()
        bad_cb = MagicMock(side_effect=RuntimeError("oops"))
        handler.on_press(BUTTON_A, bad_cb)

        mock_gpio.input.return_value = 1
        handler._press_times[BUTTON_A] = time.time() - 0.1

        # Should not raise
        handler._check_release(BUTTON_A)
        bad_cb.assert_called_once()


class TestButtonStart:
    """Tests for starting the button handler."""

    def test_start_registers_edge_detect(self, mock_gpio):
        handler = ButtonHandler()
        handler.start()

        assert mock_gpio.add_event_detect.call_count == 4
        for pin in ALL_BUTTONS:
            mock_gpio.add_event_detect.assert_any_call(
                pin,
                mock_gpio.FALLING,
                callback=handler._button_callback,
                bouncetime=handler._debounce_ms,
            )

    def test_start_launches_poll_thread(self):
        handler = ButtonHandler()
        handler.start()

        assert handler._running is True
        # Give poll thread a moment to start
        time.sleep(0.1)

        handler.stop()


class TestButtonCleanup:
    """Tests for cleanup."""

    def test_stop_sets_running_false(self):
        handler = ButtonHandler()
        handler._running = True
        handler.stop()
        assert handler._running is False

    def test_cleanup_calls_gpio_cleanup(self, mock_gpio):
        handler = ButtonHandler()
        handler.cleanup()
        mock_gpio.cleanup.assert_called_once_with(ALL_BUTTONS)

    def test_cleanup_after_stop(self, mock_gpio):
        handler = ButtonHandler()
        handler.start()
        handler.cleanup()
        assert handler._running is False
        mock_gpio.cleanup.assert_called()


class TestLongPressWhileHeld:
    """Long press fires once while held; release then triggers nothing."""

    def test_fires_while_held_then_no_short_press(self, mock_gpio):
        handler = ButtonHandler()
        short_cb, long_cb = MagicMock(), MagicMock()
        handler.on_press(BUTTON_Y, short_cb)
        handler.on_long_press(BUTTON_Y, long_cb)
        handler._press_times[BUTTON_Y] = time.time() - (LONG_PRESS_THRESHOLD_S + 0.1)

        mock_gpio.input.return_value = 0  # still held
        handler._check_release(BUTTON_Y)
        handler._check_release(BUTTON_Y)
        long_cb.assert_called_once()

        mock_gpio.input.return_value = 1  # released
        handler._check_release(BUTTON_Y)
        long_cb.assert_called_once()
        short_cb.assert_not_called()
        assert BUTTON_Y not in handler._press_times

    def test_short_press_unaffected(self, mock_gpio):
        handler = ButtonHandler()
        short_cb, long_cb = MagicMock(), MagicMock()
        handler.on_press(BUTTON_Y, short_cb)
        handler.on_long_press(BUTTON_Y, long_cb)
        handler._press_times[BUTTON_Y] = time.time() - 0.2

        mock_gpio.input.return_value = 0
        handler._check_release(BUTTON_Y)
        mock_gpio.input.return_value = 1
        handler._check_release(BUTTON_Y)
        short_cb.assert_called_once()
        long_cb.assert_not_called()

    def test_held_without_long_callback_waits_for_release(self, mock_gpio):
        handler = ButtonHandler()
        short_cb = MagicMock()
        handler.on_press(BUTTON_A, short_cb)
        handler._press_times[BUTTON_A] = time.time() - (LONG_PRESS_THRESHOLD_S + 0.1)
        mock_gpio.input.return_value = 0
        handler._check_release(BUTTON_A)
        short_cb.assert_not_called()
        mock_gpio.input.return_value = 1
        handler._check_release(BUTTON_A)
        short_cb.assert_called_once()
