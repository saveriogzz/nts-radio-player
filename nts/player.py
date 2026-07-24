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


class MPVPlayer:
    """Wrapper around mpv with IPC socket control."""

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._current_url: Optional[str] = None
        self._paused = False

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
            "--no-terminal",
            "--ao=pulse",
            f"--input-ipc-server={IPC_SOCKET_PATH}",
            "--idle=no",
            "--cache=yes",
            "--cache-secs=10",
            "--demuxer-max-bytes=500KiB",
            "--demuxer-max-back-bytes=100KiB",
            url,
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp,
            )
            self._current_url = url
            self._paused = False
            logger.info("mpv started with PID %d", self._process.pid)

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
