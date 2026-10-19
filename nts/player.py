"""mpv player wrapper with IPC socket control.

Manages an mpv subprocess and communicates via JSON IPC
over a Unix domain socket.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

IPC_SOCKET_PATH = "/tmp/nts-mpv.sock"

# Watchdog: restart mpv when it dies while it should be playing (stream
# dropped, Wi-Fi blip). Delay doubles on repeated failures, up to the max.
RESTART_DELAY_MIN = 2.0
RESTART_DELAY_MAX = 60.0
# A run this long counts as healthy and resets the restart delay
HEALTHY_RUN_SECS = 30.0


class MPVPlayer:
    """Wrapper around mpv with IPC socket control."""

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._current_url: Optional[str] = None
        self._paused = False
        self._started_at = 0.0
        self._restart_delay = RESTART_DELAY_MIN
        self._next_restart = 0.0

    def _cleanup_socket(self):
        """Remove stale IPC socket file."""
        try:
            os.unlink(IPC_SOCKET_PATH)
        except FileNotFoundError:
            pass

    def _start_mpv(self, url: str):
        """Start a new mpv process with the given URL."""
        self._stop_process()
        self._cleanup_socket()

        cmd = [
            "mpv",
            "--no-video",
            # Only warnings/errors on stdout, forwarded to our log
            "--no-input-terminal",
            "--quiet",
            "--msg-level=all=warn",
            "--ao=pipewire",
            f"--input-ipc-server={IPC_SOCKET_PATH}",
            "--idle=no",
            # ~60 s read-ahead (~2 MiB at NTS's 256 kbps) rides out Wi-Fi
            # stalls; after an underrun, refill 5 s before resuming instead
            # of stuttering on every packet
            "--cache=yes",
            "--cache-secs=60",
            "--demuxer-max-bytes=2MiB",
            "--demuxer-max-back-bytes=100KiB",
            "--cache-pause-wait=5",
            url,
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                preexec_fn=os.setpgrp,
            )
            self._current_url = url
            self._paused = False
            self._started_at = time.monotonic()
            logger.info("mpv started with PID %d", self._process.pid)
            threading.Thread(
                target=self._log_output, args=(self._process,), daemon=True, name="mpv-log"
            ).start()

            # Wait briefly for the IPC socket to appear
            for _ in range(20):
                if os.path.exists(IPC_SOCKET_PATH):
                    break
                time.sleep(0.1)
        except FileNotFoundError:
            logger.error("mpv not found. Is it installed?")
            self._process = None
        except Exception:
            logger.exception("Failed to start mpv")
            self._process = None

    @staticmethod
    def _log_output(process: subprocess.Popen):
        """Forward mpv's warnings/errors to the app log until it exits."""
        try:
            for line in process.stdout:
                line = line.strip()
                if line:
                    logger.warning("mpv: %s", line)
        except Exception:
            pass

    def _stop_process(self):
        """Terminate the mpv process if running."""
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            except Exception:
                logger.warning("Error stopping mpv process")
            finally:
                self._process = None

    def _send_command(self, command: list) -> Optional[dict]:
        """Send a JSON IPC command to mpv and return the response."""
        if not os.path.exists(IPC_SOCKET_PATH):
            return None

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(IPC_SOCKET_PATH)

            msg = json.dumps({"command": command}) + "\n"
            sock.sendall(msg.encode("utf-8"))

            # Read response
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            sock.close()

            if data:
                # Take the first complete JSON line
                line = data.split(b"\n")[0]
                return json.loads(line)
        except (socket.error, json.JSONDecodeError, OSError):
            logger.debug("IPC command failed: %s", command)
        return None

    def _is_process_alive(self) -> bool:
        """Check if the mpv process is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def play(self, url: str):
        """Start playing a URL. Restarts mpv if needed."""
        with self._lock:
            if url == self._current_url and self._is_process_alive() and not self._paused:
                return  # Already playing this URL

            if self._is_process_alive() and self._current_url:
                # Load new URL into existing mpv
                result = self._send_command(["loadfile", url, "replace"])
                if result and result.get("error") == "success":
                    # loadfile keeps mpv's pause flag; clear it or a paused
                    # player stays silent while we report it as playing
                    self._send_command(["set_property", "pause", False])
                    self._current_url = url
                    self._paused = False
                    return

            # Start fresh mpv process
            self._start_mpv(url)

    def stop(self):
        """Stop playback and kill mpv."""
        with self._lock:
            self._stop_process()
            self._cleanup_socket()
            self._current_url = None
            self._paused = False

    def pause(self):
        """Pause playback."""
        with self._lock:
            if self._is_process_alive():
                self._send_command(["set_property", "pause", True])
                self._paused = True

    def suspend(self):
        """Stop mpv but remember the stream; X / resume() restarts it.

        Frees mpv's memory while another source plays. A paused live stream
        would resume from a stale point anyway.
        """
        with self._lock:
            self._stop_process()
            self._cleanup_socket()
            self._paused = True

    def ensure_playing(self, now: Optional[float] = None) -> bool:
        """Restart mpv if it died while it should be playing.

        Returns True when a restart was attempted.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._paused or not self._current_url:
                return False
            if self._is_process_alive():
                if now - self._started_at >= HEALTHY_RUN_SECS:
                    self._restart_delay = RESTART_DELAY_MIN
                return False
            if now < self._next_restart:
                return False

            logger.warning(
                "mpv exited unexpectedly, restarting (next retry in %.0fs)",
                self._restart_delay,
            )
            self._next_restart = now + self._restart_delay
            self._restart_delay = min(self._restart_delay * 2, RESTART_DELAY_MAX)
            self._start_mpv(self._current_url)
            return True

    def resume(self):
        """Resume playback."""
        with self._lock:
            if self._is_process_alive():
                self._send_command(["set_property", "pause", False])
                self._paused = False
            elif self._current_url:
                # mpv died, restart
                self._start_mpv(self._current_url)

    def toggle_pause(self):
        """Toggle play/pause state."""
        with self._lock:
            if not self._is_process_alive():
                if self._current_url:
                    self._start_mpv(self._current_url)
                return

            if self._paused:
                self._send_command(["set_property", "pause", False])
                self._paused = False
            else:
                self._send_command(["set_property", "pause", True])
                self._paused = True

    def is_playing(self) -> bool:
        """Check if audio is currently playing (not paused, process alive)."""
        with self._lock:
            return self._is_process_alive() and not self._paused

    def is_paused(self) -> bool:
        """Check if playback is paused."""
        return self._paused

    def get_current_url(self) -> Optional[str]:
        """Return the currently loaded URL."""
        return self._current_url

    def shutdown(self):
        """Clean shutdown of the player."""
        self.stop()
        logger.info("Player shut down")
