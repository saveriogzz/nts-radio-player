"""Tests for nts.player — mpv subprocess wrapper."""

import json
import os
import socket
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from nts.player import MPVPlayer, IPC_SOCKET_PATH


@pytest.fixture
def player():
    """Create an MPVPlayer with mocked subprocess."""
    with patch("nts.player.subprocess") as mock_sub:
        p = MPVPlayer()
        yield p
        # Cleanup
        p._process = None


class TestPlayerInit:
    """Tests for player initialization."""

    def test_initial_state(self):
        p = MPVPlayer()
        assert p._process is None
        assert p._current_url is None
        assert p._paused is False


class TestPlayerPlayback:
    """Tests for play/stop/pause/resume."""

    def test_play_starts_mpv(self):
        with patch("nts.player.subprocess.Popen") as mock_popen, \
             patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.os.unlink"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            p = MPVPlayer()
            p.play("https://stream.example.com/stream1")

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert "mpv" in cmd[0]
            assert "--no-video" in cmd
            assert "https://stream.example.com/stream1" in cmd
            assert p._current_url == "https://stream.example.com/stream1"

    def test_play_same_url_noop(self):
        with patch("nts.player.subprocess.Popen") as mock_popen, \
             patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.os.unlink"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            p = MPVPlayer()
            p.play("https://stream.example.com/stream1")
            mock_popen.reset_mock()

            # Playing same URL while already playing should be a no-op
            p.play("https://stream.example.com/stream1")
            mock_popen.assert_not_called()

    def test_stop_terminates_process(self):
        with patch("nts.player.subprocess.Popen") as mock_popen, \
             patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.os.unlink"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            p = MPVPlayer()
            p.play("https://stream.example.com/stream1")
            p.stop()

            mock_proc.terminate.assert_called_once()
            assert p._current_url is None
            assert p._process is None

    def test_toggle_pause(self):
        with patch("nts.player.subprocess.Popen") as mock_popen, \
             patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.os.unlink"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            p = MPVPlayer()
            p.play("https://stream.example.com/stream1")

            # Mock _send_command to avoid socket operations
            p._send_command = MagicMock(return_value={"error": "success"})

            assert not p._paused
            p.toggle_pause()
            assert p._paused
            p._send_command.assert_called_with(["set_property", "pause", True])

            p.toggle_pause()
            assert not p._paused
            p._send_command.assert_called_with(["set_property", "pause", False])

    def test_is_playing(self):
        p = MPVPlayer()
        assert not p.is_playing()  # no process

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process alive
        p._process = mock_proc
        p._paused = False
        assert p.is_playing()

        p._paused = True
        assert not p.is_playing()

    def test_is_paused(self):
        p = MPVPlayer()
        assert not p.is_paused()
        p._paused = True
        assert p.is_paused()


class TestPlayerIPC:
    """Tests for mpv JSON IPC communication."""

    def test_send_command_no_socket(self):
        with patch("nts.player.os.path.exists", return_value=False):
            p = MPVPlayer()
            result = p._send_command(["get_property", "volume"])
            assert result is None

    def test_send_command_with_socket(self):
        """Test IPC command formatting (mock at socket level)."""
        with patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value = mock_sock

            response = json.dumps({"error": "success", "data": 100}).encode() + b"\n"
            mock_sock.recv.return_value = response

            p = MPVPlayer()
            result = p._send_command(["get_property", "volume"])

            assert result is not None
            assert result["error"] == "success"
            assert result["data"] == 100

            # Verify the command was sent correctly
            sent_data = mock_sock.sendall.call_args[0][0]
            sent_msg = json.loads(sent_data.decode().strip())
            assert sent_msg["command"] == ["get_property", "volume"]


class TestPlayerProcessLifecycle:
    """Tests for process management."""

    def test_process_alive_check(self):
        p = MPVPlayer()
        assert not p._is_process_alive()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        p._process = mock_proc
        assert p._is_process_alive()

        mock_proc.poll.return_value = 0  # exited
        assert not p._is_process_alive()

    def test_resume_restarts_dead_process(self):
        with patch("nts.player.subprocess.Popen") as mock_popen, \
             patch("nts.player.os.path.exists", return_value=True), \
             patch("nts.player.os.unlink"):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0  # dead
            mock_proc.pid = 1234
            mock_popen.return_value = mock_proc

            p = MPVPlayer()
            p._current_url = "https://stream.example.com/stream1"
            p._process = mock_proc
            p._paused = False

            # Make new process look alive
            new_proc = MagicMock()
            new_proc.poll.return_value = None
            new_proc.pid = 5678
            mock_popen.return_value = new_proc

            p.resume()
            mock_popen.assert_called()

    def test_shutdown(self):
        p = MPVPlayer()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        p._process = mock_proc
        p._current_url = "https://stream.example.com"

        with patch("nts.player.os.unlink"):
            p.shutdown()

        mock_proc.terminate.assert_called()
        assert p._current_url is None

    def test_get_current_url(self):
        p = MPVPlayer()
        assert p.get_current_url() is None

        p._current_url = "https://stream.example.com/stream1"
        assert p.get_current_url() == "https://stream.example.com/stream1"

    def test_cleanup_socket_no_file(self):
        """Cleanup should not raise if socket file doesn't exist."""
        p = MPVPlayer()
        with patch("nts.player.os.unlink", side_effect=FileNotFoundError):
            p._cleanup_socket()  # should not raise


class TestPlayAfterPause:
    def test_play_unpauses_reused_mpv(self):
        """Returning to NTS after a pause must clear mpv's pause flag."""
        p = MPVPlayer()
        proc = MagicMock()
        proc.poll.return_value = None
        p._process = proc
        p._current_url = "https://stream.example.com/live"
        p._paused = True
        p._send_command = MagicMock(return_value={"error": "success"})

        p.play("https://stream.example.com/live")

        sent = [c.args[0] for c in p._send_command.call_args_list]
        assert ["loadfile", "https://stream.example.com/live", "replace"] in sent
        assert sent[-1] == ["set_property", "pause", False]
        assert p.is_playing()
class TestWatchdogAndSuspend:
    """mpv restarts after unexpected exits; suspend frees mpv but keeps the stream."""

    def _player_with_dead_mpv(self):
        p = MPVPlayer()
        p._current_url = "https://stream.example.com/live"
        p._paused = False
        dead = MagicMock()
        dead.poll.return_value = 1
        p._process = dead
        p._start_mpv = MagicMock()
        return p

    def test_restarts_dead_mpv(self):
        p = self._player_with_dead_mpv()
        assert p.ensure_playing(now=100.0) is True
        p._start_mpv.assert_called_once_with("https://stream.example.com/live")

    def test_backoff_between_restarts(self):
        from nts.player import RESTART_DELAY_MIN
        p = self._player_with_dead_mpv()
        p.ensure_playing(now=100.0)
        assert p.ensure_playing(now=100.0 + RESTART_DELAY_MIN - 0.1) is False
        assert p.ensure_playing(now=100.0 + RESTART_DELAY_MIN) is True
        # delay doubled after the second failure
        assert p.ensure_playing(now=100.0 + RESTART_DELAY_MIN * 2) is False
        assert p.ensure_playing(now=100.0 + RESTART_DELAY_MIN * 3) is True

    def test_backoff_capped(self):
        from nts.player import RESTART_DELAY_MAX
        p = self._player_with_dead_mpv()
        now = 0.0
        for _ in range(20):
            p.ensure_playing(now=now)
            now += RESTART_DELAY_MAX
        assert p._restart_delay == RESTART_DELAY_MAX

    def test_healthy_run_resets_backoff(self):
        from nts.player import HEALTHY_RUN_SECS, RESTART_DELAY_MIN
        p = self._player_with_dead_mpv()
        p._restart_delay = 32.0
        alive = MagicMock()
        alive.poll.return_value = None
        p._process = alive
        p._started_at = 0.0
        p.ensure_playing(now=HEALTHY_RUN_SECS)
        assert p._restart_delay == RESTART_DELAY_MIN

    def test_no_restart_when_paused_or_no_url(self):
        p = self._player_with_dead_mpv()
        p._paused = True
        assert p.ensure_playing(now=100.0) is False
        p._paused = False
        p._current_url = None
        assert p.ensure_playing(now=100.0) is False
        p._start_mpv.assert_not_called()

    def test_suspend_stops_mpv_keeps_url(self):
        p = MPVPlayer()
        p._current_url = "https://stream.example.com/live"
        proc = MagicMock()
        proc.poll.return_value = None
        p._process = proc
        with patch("nts.player.os.unlink"):
            p.suspend()
        proc.terminate.assert_called_once()
        assert p._process is None
        assert p._paused is True
        assert p._current_url == "https://stream.example.com/live"
        assert p.is_playing() is False
        # watchdog leaves a suspended stream alone
        assert p.ensure_playing(now=1000.0) is False

    def test_toggle_after_suspend_restarts_stream(self):
        p = MPVPlayer()
        p._current_url = "https://stream.example.com/live"
        p._paused = True
        p._start_mpv = MagicMock()
        p.toggle_pause()
        p._start_mpv.assert_called_once_with("https://stream.example.com/live")

    def test_mpv_output_forwarded_to_log(self, caplog):
        proc = MagicMock()
        proc.stdout = iter(["[ffmpeg] tcp: Connection refused\n", "\n"])
        with caplog.at_level("WARNING", logger="nts.player"):
            MPVPlayer._log_output(proc)
        assert "mpv: [ffmpeg] tcp: Connection refused" in caplog.text


class TestSuspendThenPlay:
    def test_play_after_suspend_starts_fresh_mpv(self):
        """Back to NTS after Spotify: suspended player starts a new mpv."""
        p = MPVPlayer()
        proc = MagicMock()
        proc.poll.return_value = None
        p._process = proc
        p._current_url = "https://stream.example.com/live"
        with patch("nts.player.os.unlink"):
            p.suspend()
        p._start_mpv = MagicMock()
        p.play("https://stream.example.com/live")
        p._start_mpv.assert_called_once_with("https://stream.example.com/live")
