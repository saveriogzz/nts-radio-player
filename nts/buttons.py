"""GPIO button handler for Pimoroni Pirate Audio.

Handles the 4 buttons (A, B, X, Y) with debouncing and
long-press detection. Runs callbacks in a background thread.
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# BCM pin numbers for Pirate Audio buttons
BUTTON_A = 5   # Top-left
BUTTON_B = 6   # Bottom-left
BUTTON_X = 16  # Top-right
BUTTON_Y = 24  # Bottom-right

ALL_BUTTONS = [BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y]
BUTTON_NAMES = {
    BUTTON_A: "A",
    BUTTON_B: "B",
    BUTTON_X: "X",
    BUTTON_Y: "Y",
}

# Default timings
DEFAULT_DEBOUNCE_MS = 200
LONG_PRESS_THRESHOLD_S = 1.0


class ButtonHandler:
    """Handles GPIO button input with debounce and long-press detection."""

    def __init__(self, debounce_ms: int = DEFAULT_DEBOUNCE_MS):
        """Initialize the button handler.

        Args:
            debounce_ms: Debounce time in milliseconds.
        """
        self._debounce_ms = debounce_ms
        self._gpio_available = False
        self._callbacks: dict[int, Callable] = {}
        self._long_press_callbacks: dict[int, Callable] = {}
        self._press_times: dict[int, float] = {}
        self._long_fired: set[int] = set()  # held buttons whose long press already fired
        self._running = False
        self._lock = threading.Lock()

        self._init_gpio()

    def _init_gpio(self):
        """Initialize GPIO pins for button input."""
        try:
            import RPi.GPIO as GPIO

            self._GPIO = GPIO
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)

            for pin in ALL_BUTTONS:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            self._gpio_available = True
            logger.info("GPIO buttons initialized")
        except ImportError:
            logger.warning("RPi.GPIO not available (not on Pi?)")
            self._GPIO = None
            self._gpio_available = False
        except Exception:
            logger.exception("Failed to initialize GPIO")
            self._GPIO = None
            self._gpio_available = False

    def on_press(self, button: int, callback: Callable):
        """Register a callback for a button press.

        Args:
            button: BCM pin number (use BUTTON_A/B/X/Y constants).
            callback: Function to call on press. Takes no arguments.
        """
        with self._lock:
            self._callbacks[button] = callback

    def on_long_press(self, button: int, callback: Callable):
        """Register a callback for a long press (>1s).

        Args:
            button: BCM pin number.
            callback: Function to call on long press.
        """
        with self._lock:
            self._long_press_callbacks[button] = callback

    def _button_callback(self, pin: int):
        """Internal callback triggered by GPIO edge detection."""
        # Record press time for long-press detection
        self._press_times[pin] = time.time()

    def _check_release(self, pin: int):
        """Check if a button was released and determine press type."""
        if not self._gpio_available or self._GPIO is None:
            return

        press_time = self._press_times.get(pin)
        if press_time is None:
            return

        duration = time.time() - press_time

        # Button is active-low (pressed = 0)
        if self._GPIO.input(pin) == 0:  # Still held
            # Fire a long press as soon as the threshold is reached, so the
            # user gets feedback without having to let go
            if duration >= LONG_PRESS_THRESHOLD_S and pin not in self._long_fired:
                with self._lock:
                    cb = self._long_press_callbacks.get(pin)
                if cb:
                    self._long_fired.add(pin)
                    try:
                        cb()
                    except Exception:
                        logger.exception(
                            "Error in long-press callback for %s",
                            BUTTON_NAMES.get(pin, pin),
                        )
            return

        # Released
        self._press_times.pop(pin, None)
        if pin in self._long_fired:
            self._long_fired.discard(pin)
            return  # long press already handled; no short press

        with self._lock:
            if duration >= LONG_PRESS_THRESHOLD_S:
                cb = self._long_press_callbacks.get(pin)
                if cb:
                    try:
                        cb()
                    except Exception:
                        logger.exception(
                            "Error in long-press callback for %s",
                            BUTTON_NAMES.get(pin, pin),
                        )
                    return

            cb = self._callbacks.get(pin)
            if cb:
                try:
                    cb()
                except Exception:
                    logger.exception(
                        "Error in button callback for %s",
                        BUTTON_NAMES.get(pin, pin),
                    )

    def start(self):
        """Start listening for button events."""
        if not self._gpio_available or self._GPIO is None:
            logger.warning("GPIO not available, buttons disabled")
            return

        self._running = True

        # Register edge detection for each button
        for pin in ALL_BUTTONS:
            try:
                self._GPIO.add_event_detect(
                    pin,
                    self._GPIO.FALLING,
                    callback=self._button_callback,
                    bouncetime=self._debounce_ms,
                )
            except Exception:
                logger.exception("Failed to add event detect for pin %d", pin)

        # Start polling thread for release detection
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="button-poll"
        )
        self._poll_thread.start()
        logger.info("Button handler started")

    def _poll_loop(self):
        """Poll for button releases to detect long presses."""
        while self._running:
            for pin in ALL_BUTTONS:
                if pin in self._press_times:
                    self._check_release(pin)
            time.sleep(0.05)  # 50ms poll interval

    def stop(self):
        """Stop listening for button events and clean up GPIO."""
        self._running = False

        if self._gpio_available and self._GPIO is not None:
            try:
                for pin in ALL_BUTTONS:
                    self._GPIO.remove_event_detect(pin)
            except Exception:
                pass

    def cleanup(self):
        """Full cleanup of GPIO resources."""
        self.stop()
        if self._gpio_available and self._GPIO is not None:
            try:
                self._GPIO.cleanup(ALL_BUTTONS)
            except Exception:
                pass
        logger.info("Button handler cleaned up")
