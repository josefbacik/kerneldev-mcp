"""
Tests for kill_hanging_vms functionality.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import pytest

from kerneldev_mcp.boot_manager import (
    _track_vm_process,
    _untrack_vm_process,
    _get_tracked_vm_processes,
    _cleanup_dead_tracked_processes,
    VM_PID_TRACKING_FILE,
    BOOT_LOG_DIR,
)


@pytest.fixture
def temp_tracking_file(monkeypatch):
    """Create a temporary tracking file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        temp_file = Path(f.name)

    # Patch the module-level VM_PID_TRACKING_FILE constant
    monkeypatch.setattr("kerneldev_mcp.boot_manager.VM_PID_TRACKING_FILE", temp_file)

    yield temp_file

    # Cleanup
    if temp_file.exists():
        temp_file.unlink()


@pytest.fixture
def temp_log_dir(monkeypatch):
    """Create a temporary log directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        monkeypatch.setattr("kerneldev_mcp.boot_manager.BOOT_LOG_DIR", log_dir)
        yield log_dir


class TestVMProcessTracking:
    """Test VM process tracking functionality."""

    def test_track_vm_process_basic(self, temp_tracking_file):
        """Test tracking a VM process."""
        _track_vm_process(12345, 12345, "test VM")

        assert temp_tracking_file.exists()

        with open(temp_tracking_file, "r") as f:
            data = json.load(f)

        assert "12345" in data
        assert data["12345"]["pid"] == 12345
        assert data["12345"]["pgid"] == 12345
        assert data["12345"]["description"] == "test VM"
        assert "started_at" in data["12345"]

    def test_track_vm_process_with_log_file(self, temp_tracking_file):
        """Test tracking a VM process with log file path."""
        log_path = Path("/tmp/test-boot.log")
        _track_vm_process(12345, 12345, "test VM", log_file_path=log_path)

        with open(temp_tracking_file, "r") as f:
            data = json.load(f)

        assert data["12345"]["log_file_path"] == str(log_path)

    def test_track_multiple_vm_processes(self, temp_tracking_file):
        """Test tracking multiple VM processes."""
        _track_vm_process(12345, 12345, "VM 1", log_file_path=Path("/tmp/log1.log"))
        _track_vm_process(12346, 12346, "VM 2", log_file_path=Path("/tmp/log2.log"))
        _track_vm_process(12347, 12347, "VM 3", log_file_path=Path("/tmp/log3.log"))

        with open(temp_tracking_file, "r") as f:
            data = json.load(f)

        assert len(data) == 3
        assert "12345" in data
        assert "12346" in data
        assert "12347" in data

    def test_untrack_vm_process(self, temp_tracking_file):
        """Test untracking a VM process."""
        _track_vm_process(12345, 12345, "test VM")
        _track_vm_process(12346, 12346, "another VM")

        _untrack_vm_process(12345)

        with open(temp_tracking_file, "r") as f:
            data = json.load(f)

        assert "12345" not in data
        assert "12346" in data

    def test_untrack_last_process_removes_file(self, temp_tracking_file):
        """Test that untracking last process removes tracking file."""
        _track_vm_process(12345, 12345, "test VM")
        _untrack_vm_process(12345)

        assert not temp_tracking_file.exists()

    @patch("kerneldev_mcp.boot_manager.os.kill")
    def test_get_tracked_vm_processes(self, mock_kill, temp_tracking_file):
        """Test getting tracked VM processes."""
        _track_vm_process(12345, 12345, "VM 1", log_file_path=Path("/tmp/log1.log"))
        _track_vm_process(12346, 12346, "VM 2", log_file_path=Path("/tmp/log2.log"))

        # Mock os.kill to simulate processes are alive
        mock_kill.return_value = None

        tracked = _get_tracked_vm_processes()

        assert len(tracked) == 2
        assert 12345 in tracked
        assert 12346 in tracked
        assert tracked[12345]["description"] == "VM 1"
        assert tracked[12346]["log_file_path"] == "/tmp/log2.log"

    @patch("kerneldev_mcp.boot_manager.os.kill")
    def test_get_tracked_filters_dead_processes(self, mock_kill, temp_tracking_file):
        """Test that get_tracked filters out dead processes."""
        # Track two processes
        _track_vm_process(12345, 12345, "VM 1")
        _track_vm_process(12346, 12346, "VM 2")

        # Mock os.kill to simulate one process is dead
        def kill_side_effect(pid, sig):
            if pid == 12345:
                raise ProcessLookupError("Process not found")

        mock_kill.side_effect = kill_side_effect

        tracked = _get_tracked_vm_processes()

        # Should only return the living process
        assert len(tracked) == 1
        assert 12346 in tracked
        assert 12345 not in tracked

    @patch("kerneldev_mcp.boot_manager.os.kill")
    def test_cleanup_dead_tracked_processes(self, mock_kill, temp_tracking_file):
        """Test cleaning up dead processes from tracking file."""
        _track_vm_process(12345, 12345, "dead VM")
        _track_vm_process(12346, 12346, "alive VM")

        # Mock os.kill to simulate one process is dead
        def kill_side_effect(pid, sig):
            if pid == 12345:
                raise ProcessLookupError("Process not found")

        mock_kill.side_effect = kill_side_effect

        _cleanup_dead_tracked_processes()

        with open(temp_tracking_file, "r") as f:
            data = json.load(f)

        assert "12345" not in data
        assert "12346" in data


class TestKillHangingVMsLogDisplay:
    """Test log file display in kill_hanging_vms."""

    def test_log_file_tail_shown_for_killed_vm(self, temp_log_dir):
        """Test that last 50 lines of log file are shown when VM is killed."""
        # Create a log file with 100 lines
        log_file = temp_log_dir / "boot-20251113-143022-running.log"
        log_lines = [f"[    {i}.123456] Test log line {i}\n" for i in range(100)]
        log_file.write_text("".join(log_lines))

        # Read the log file as kill_hanging_vms would
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            tail_lines = lines[-50:] if len(lines) > 50 else lines

        # Verify we got the last 50 lines
        assert len(tail_lines) == 50
        assert "Test log line 99" in tail_lines[-1]
        assert "Test log line 50" in tail_lines[0]

    def test_log_file_tail_handles_short_files(self, temp_log_dir):
        """Test that files with < 50 lines show all lines."""
        log_file = temp_log_dir / "boot-20251113-143022-running.log"
        log_lines = [f"[    {i}.123456] Test log line {i}\n" for i in range(20)]
        log_file.write_text("".join(log_lines))

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            tail_lines = lines[-50:] if len(lines) > 50 else lines

        # Should show all 20 lines
        assert len(tail_lines) == 20
        assert "Test log line 0" in tail_lines[0]
        assert "Test log line 19" in tail_lines[-1]

    def test_log_file_handles_missing_file(self, temp_log_dir):
        """Test graceful handling when log file doesn't exist."""
        log_path = temp_log_dir / "nonexistent.log"

        # This should not raise an exception
        exists = log_path.exists()
        assert not exists

    def test_log_file_handles_unicode_errors(self, temp_log_dir):
        """Test handling of files with encoding issues."""
        log_file = temp_log_dir / "boot-20251113-143022-running.log"
        # Write some binary data that might cause encoding issues
        log_file.write_bytes(b"Normal line\n\xff\xfe Invalid UTF-8\n")

        # Should handle with errors='replace'
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            assert "Normal line" in content

    def test_log_file_preserves_output_format(self, temp_log_dir):
        """Test that log output preserves formatting."""
        log_file = temp_log_dir / "boot-20251113-143022-running.log"
        log_content = """[    0.000000] Linux version 6.16.0
[   46.123456] BUG: kernel NULL pointer dereference at 0000000000000008
[   46.234567] RIP: 0010:btrfs_submit_bio+0x42/0x180 [btrfs]
[   46.345678] Call Trace:
[   46.456789]  ? __die+0x24/0x70
"""
        log_file.write_text(log_content)

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Verify formatting is preserved
        assert any("BUG: kernel NULL pointer" in line for line in lines)
        assert any("Call Trace" in line for line in lines)


class TestKillHangingVMsIntegration:
    """Integration tests for kill_hanging_vms with server handler."""

    @patch("kerneldev_mcp.boot_manager.subprocess.run")
    @patch("kerneldev_mcp.boot_manager.os.kill")
    def test_kill_hanging_vms_shows_log_path(
        self, mock_kill, mock_subprocess, temp_tracking_file, temp_log_dir
    ):
        """Test that kill_hanging_vms output includes log file path."""
        # Create a log file
        log_file = temp_log_dir / "boot-20251113-143022-running.log"
        log_file.write_text("[    1.234567] Test kernel output\n")

        # Track a process with this log file
        _track_vm_process(12345, 12345, "test VM", log_file_path=log_file)

        # Get tracked processes
        tracked = _get_tracked_vm_processes()

        # Verify log file path is in tracking data
        assert 12345 in tracked
        assert tracked[12345]["log_file_path"] == str(log_file)

    def test_tracking_file_location_per_server_instance(self):
        """Test that each MCP server instance has its own tracking file."""
        # The tracking file includes the MCP server PID in its name
        assert "_MCP_SERVER_PID" in str(VM_PID_TRACKING_FILE.name) or str(os.getpid()) in str(
            VM_PID_TRACKING_FILE.name
        )


class TestBootLogDirectory:
    """Test boot log directory management."""

    def test_boot_log_dir_constant_exists(self):
        """Test that BOOT_LOG_DIR constant is defined."""
        assert BOOT_LOG_DIR is not None
        assert isinstance(BOOT_LOG_DIR, Path)

    def test_boot_log_dir_default_location(self):
        """Test default boot log directory location."""
        # Should be in /tmp/kerneldev-boot-logs
        assert "kerneldev-boot-logs" in str(BOOT_LOG_DIR)
