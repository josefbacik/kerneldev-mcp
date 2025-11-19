"""
Unit tests for null_blk utility functions in device_utils.py.

Tests use mocks to avoid requiring:
- Root/sudo permissions
- null_blk kernel module
- configfs filesystem
- Actual device creation

This ensures tests can run in any environment.
"""

import pytest
import subprocess
from unittest.mock import patch, MagicMock
from src.kerneldev_mcp.device_utils import (
    check_null_blk_support,
    _parse_size_to_mb,
    create_null_blk_device,
    cleanup_null_blk_device,
    cleanup_orphaned_null_blk_devices,
    _allocate_null_blk_index,
)


class TestParseSizeToMb:
    """Test _parse_size_to_mb function with various size formats."""

    @pytest.mark.parametrize(
        "size,expected_mb",
        [
            ("10G", 10240),  # 10 GB = 10240 MB
            ("1G", 1024),  # 1 GB = 1024 MB
            ("512M", 512),  # 512 MB
            ("1M", 1),  # 1 MB
            ("2048M", 2048),  # 2048 MB
            ("100G", 102400),  # 100 GB
            ("1024K", 1),  # 1024 KB = 1 MB (rounded)
            ("2048K", 2),  # 2048 KB = 2 MB
            ("512K", 1),  # 512 KB = 1 MB (rounded up)
            ("10", 10),  # No unit defaults to MB
        ],
    )
    def test_parse_size_valid_formats(self, size, expected_mb):
        """Test parsing valid size formats."""
        valid, error, size_mb = _parse_size_to_mb(size)

        assert valid is True
        assert error == ""
        assert size_mb == expected_mb

    @pytest.mark.parametrize(
        "size",
        [
            "abc",  # Invalid characters
            "10X",  # Invalid unit
            "",  # Empty string
            "G10",  # Unit before number
            "10.5G",  # Decimal not supported
            "-10G",  # Negative size
            "10 G",  # Space in size
            "10GB",  # Two-letter unit
        ],
    )
    def test_parse_size_invalid_formats(self, size):
        """Test parsing invalid size formats."""
        valid, error, size_mb = _parse_size_to_mb(size)

        assert valid is False
        assert "Invalid size format" in error
        assert size_mb == 0

    @pytest.mark.parametrize(
        "size",
        [
            "0G",
            "0M",
            "0K",
            "0",
        ],
    )
    def test_parse_size_zero_size(self, size):
        """Test that zero sizes are rejected."""
        valid, error, size_mb = _parse_size_to_mb(size)

        assert valid is False
        assert "cannot be zero" in error
        assert size_mb == 0

    def test_parse_size_case_insensitive(self):
        """Test that units are case-insensitive."""
        for size in ["10g", "10G", "512m", "512M", "1024k", "1024K"]:
            valid, error, size_mb = _parse_size_to_mb(size)
            assert valid is True, f"Failed for size: {size}"
            assert size_mb > 0

    def test_parse_size_kilobyte_rounding(self):
        """Test that kilobyte sizes round up to at least 1MB."""
        # Anything less than 1024K should round up to 1MB
        for kb in [1, 100, 512, 1023]:
            valid, error, size_mb = _parse_size_to_mb(f"{kb}K")
            assert valid is True
            assert size_mb == max(1, kb // 1024)

    def test_parse_size_very_large_sizes(self):
        """Test parsing very large sizes."""
        # 1TB = 1024GB = 1048576MB
        valid, error, size_mb = _parse_size_to_mb("1024G")
        assert valid is True
        assert size_mb == 1048576


class TestCheckNullBlkSupport:
    """Test check_null_blk_support function."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_check_support_module_already_loaded(self, mock_sleep, mock_exists, mock_run):
        """Test when null_blk module is already loaded."""
        # Module already loaded
        mock_exists.side_effect = [
            True,  # /sys/module/null_blk exists
            True,  # /sys/kernel/config exists
            True,  # /sys/kernel/config/nullb exists
        ]

        # Mock successful test directory creation/removal
        mock_run.side_effect = [
            MagicMock(returncode=0),  # mkdir
            MagicMock(returncode=0),  # rmdir
        ]

        supported, message = check_null_blk_support()

        assert supported is True
        assert "available" in message
        # Should not try to load module since it's already loaded
        # mock_run should only be called for mkdir/rmdir, not modprobe
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_check_support_module_needs_loading(self, mock_sleep, mock_exists, mock_run):
        """Test when null_blk module needs to be loaded."""
        # Module not loaded initially
        mock_exists.side_effect = [
            False,  # /sys/module/null_blk doesn't exist (need to load)
            True,  # /sys/kernel/config exists
            True,  # /sys/kernel/config/nullb exists
        ]

        # Mock successful module load and test directory operations
        mock_run.side_effect = [
            MagicMock(returncode=0),  # modprobe
            MagicMock(returncode=0),  # mkdir
            MagicMock(returncode=0),  # rmdir
        ]

        supported, message = check_null_blk_support()

        assert supported is True
        assert "available" in message
        # Should have called modprobe
        assert "modprobe" in mock_run.call_args_list[0][0][0]

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_check_support_module_not_available(self, mock_exists, mock_run):
        """Test when null_blk module is not available."""
        mock_exists.return_value = False  # Module not loaded

        # modprobe fails
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "modprobe", stderr=b"Module not found"
        )

        supported, message = check_null_blk_support()

        assert supported is False
        assert "not available" in message

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_check_support_configfs_not_mounted(self, mock_exists, mock_run):
        """Test when configfs is not mounted."""
        # Module loaded but configfs not mounted
        mock_exists.side_effect = [
            True,  # /sys/module/null_blk exists
            False,  # /sys/kernel/config doesn't exist
        ]

        supported, message = check_null_blk_support()

        assert supported is False
        assert "configfs not mounted" in message

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_check_support_nullb_directory_missing(self, mock_exists, mock_run):
        """Test when /sys/kernel/config/nullb doesn't exist."""
        # Module loaded, configfs mounted, but nullb directory missing
        mock_exists.side_effect = [
            True,  # /sys/module/null_blk exists
            True,  # /sys/kernel/config exists
            False,  # /sys/kernel/config/nullb doesn't exist
        ]

        supported, message = check_null_blk_support()

        assert supported is False
        assert "does not exist" in message

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_check_support_no_write_permission(self, mock_sleep, mock_exists, mock_run):
        """Test when user lacks write permission to configfs."""
        # Everything exists but can't create test directory
        mock_exists.side_effect = [
            True,  # /sys/module/null_blk exists
            True,  # /sys/kernel/config exists
            True,  # /sys/kernel/config/nullb exists
        ]

        # mkdir fails due to permission
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "mkdir", stderr=b"Permission denied"
        )

        supported, message = check_null_blk_support()

        assert supported is False
        assert "permission" in message.lower()

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_check_support_generic_exception(self, mock_sleep, mock_exists, mock_run):
        """Test handling of generic exceptions."""
        mock_exists.side_effect = [
            True,  # /sys/module/null_blk exists
            True,  # /sys/kernel/config exists
            True,  # /sys/kernel/config/nullb exists
        ]

        # Unexpected exception
        mock_run.side_effect = Exception("Unexpected error")

        supported, message = check_null_blk_support()

        assert supported is False
        assert "Cannot create null_blk devices" in message

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_check_support_modprobe_generic_exception(self, mock_sleep, mock_exists, mock_run):
        """Test handling of generic exception during module load."""
        # Module not loaded initially
        mock_exists.return_value = False

        # Generic exception during modprobe (not CalledProcessError)
        mock_run.side_effect = TimeoutError("modprobe timeout")

        supported, message = check_null_blk_support()

        assert supported is False
        assert "Failed to load null_blk module" in message


class TestAllocateNullBlkIndex:
    """Test _allocate_null_blk_index function."""

    @patch("subprocess.run")
    def test_allocate_first_available_index(self, mock_run):
        """Test allocating the first available index."""
        # First mkdir succeeds (index 0 available)
        mock_run.return_value = MagicMock(returncode=0)

        idx = _allocate_null_blk_index()

        assert idx == 0
        assert "mkdir" in mock_run.call_args[0][0]
        assert "nullb0" in mock_run.call_args[0][0][2]

    @patch("subprocess.run")
    def test_allocate_skips_used_indices(self, mock_run):
        """Test allocating when some indices are already in use."""
        # First two fail (already in use), third succeeds
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "mkdir"),  # index 0 taken
            subprocess.CalledProcessError(1, "mkdir"),  # index 1 taken
            MagicMock(returncode=0),  # index 2 available
        ]

        idx = _allocate_null_blk_index()

        assert idx == 2
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_allocate_all_indices_used(self, mock_run):
        """Test when all indices (0-1023) are in use."""
        # All mkdir calls fail
        mock_run.side_effect = subprocess.CalledProcessError(1, "mkdir")

        idx = _allocate_null_blk_index()

        assert idx is None
        # Should have tried all 1024 indices
        assert mock_run.call_count == 1024

    @patch("subprocess.run")
    def test_allocate_generic_exception(self, mock_run):
        """Test handling of generic exceptions during allocation."""
        # Unexpected exception
        mock_run.side_effect = Exception("Unexpected error")

        idx = _allocate_null_blk_index()

        assert idx is None


class TestCreateNullBlkDevice:
    """Test create_null_blk_device function."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_success(self, mock_allocate, mock_sleep, mock_exists, mock_run):
        """Test successful device creation."""
        mock_allocate.return_value = 0

        # Device appears after activation
        mock_exists.return_value = True

        # All subprocess calls succeed
        mock_run.return_value = MagicMock(returncode=0)

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path == "/dev/nullb0"
        assert idx == 0

        # Verify key operations were performed
        calls = [c[0][0] for c in mock_run.call_args_list]

        # Should have set size, memory_backed, and power
        assert any("size" in str(c) for c in calls)
        assert any("memory_backed" in str(c) for c in calls)
        assert any("power" in str(c) for c in calls)
        assert any("chmod" in str(c) for c in calls)

    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_invalid_size(self, mock_allocate):
        """Test device creation with invalid size."""
        device_path, idx = create_null_blk_device("invalid", "test")

        assert device_path is None
        assert idx is None
        # Should not try to allocate index for invalid size
        mock_allocate.assert_not_called()

    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_allocation_fails(self, mock_allocate):
        """Test when index allocation fails."""
        mock_allocate.return_value = None

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_size_setting_fails(self, mock_allocate, mock_exists, mock_run):
        """Test when setting device size fails."""
        mock_allocate.return_value = 5

        # Setting size fails
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "bash", stderr=b"Failed to set size"
        )

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_does_not_appear(self, mock_allocate, mock_sleep, mock_exists, mock_run):
        """Test when device doesn't appear after activation."""
        mock_allocate.return_value = 3

        # All configfs operations succeed
        mock_run.return_value = MagicMock(returncode=0)

        # But device never appears
        mock_exists.return_value = False

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None

        # Should have waited multiple times
        assert mock_sleep.call_count >= 10

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_does_not_appear_deactivate_fails(
        self, mock_allocate, mock_sleep, mock_exists, mock_run
    ):
        """Test when device doesn't appear and deactivation also fails during cleanup."""
        mock_allocate.return_value = 4

        # All configfs operations succeed, but device doesn't appear
        # When cleanup tries to deactivate, it fails (covers exception handler)
        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First calls succeed (setting params)
            if call_count[0] <= 6:
                return MagicMock(returncode=0)
            # Deactivation attempt fails (during cleanup)
            elif "echo 0" in str(args[0]):
                raise Exception("Deactivation error")
            # rmdir succeeds
            else:
                return MagicMock(returncode=0)

        mock_run.side_effect = run_side_effect
        mock_exists.return_value = False

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_create_device_chmod_fails(
        self, mock_cleanup, mock_allocate, mock_sleep, mock_exists, mock_run
    ):
        """Test when chmod fails after device creation."""
        mock_allocate.return_value = 2
        mock_exists.return_value = True

        # All succeed except chmod
        mock_run.side_effect = [
            MagicMock(returncode=0),  # size
            MagicMock(returncode=0),  # memory_backed
            MagicMock(returncode=0),  # blocksize
            MagicMock(returncode=0),  # hw_queue_depth
            MagicMock(returncode=0),  # irqmode
            MagicMock(returncode=0),  # completion_nsec
            MagicMock(returncode=0),  # power
            subprocess.CalledProcessError(1, "chmod"),  # chmod fails
        ]

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None
        # Should have attempted cleanup
        mock_cleanup.assert_called_once_with("/dev/nullb2", 2)

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_optional_params_fail(
        self, mock_allocate, mock_sleep, mock_exists, mock_run
    ):
        """Test that device creation succeeds even if optional params fail."""
        mock_allocate.return_value = 1
        mock_exists.return_value = True

        # Optional params fail but required ones succeed
        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if "blocksize" in str(cmd) or "hw_queue_depth" in str(cmd):
                # Optional params fail
                raise subprocess.CalledProcessError(1, "bash")
            return MagicMock(returncode=0)

        mock_run.side_effect = run_side_effect

        device_path, idx = create_null_blk_device("10G", "test")

        # Should still succeed
        assert device_path == "/dev/nullb1"
        assert idx == 1

    @patch("subprocess.run")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_generic_exception(self, mock_allocate, mock_run):
        """Test handling of generic exceptions."""
        mock_allocate.return_value = 0

        # Unexpected exception
        mock_run.side_effect = Exception("Unexpected error")

        device_path, idx = create_null_blk_device("10G", "test")

        assert device_path is None
        assert idx is None

    @pytest.mark.parametrize(
        "size,expected_mb",
        [
            ("10G", 10240),
            ("512M", 512),
            ("1024K", 1),
        ],
    )
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    @patch("src.kerneldev_mcp.device_utils._allocate_null_blk_index")
    def test_create_device_various_sizes(
        self, mock_allocate, mock_sleep, mock_exists, mock_run, size, expected_mb
    ):
        """Test creating devices with various sizes."""
        mock_allocate.return_value = 0
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        device_path, idx = create_null_blk_device(size, "test")

        assert device_path == "/dev/nullb0"
        assert idx == 0

        # Verify correct size was set
        size_call = None
        for c in mock_run.call_args_list:
            if "size" in str(c[0][0]) and "echo" in str(c[0][0]):
                size_call = c
                break

        assert size_call is not None
        assert str(expected_mb) in str(size_call[0][0])


class TestCleanupNullBlkDevice:
    """Test cleanup_null_blk_device function."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_success(self, mock_sleep, mock_exists, mock_run):
        """Test successful cleanup."""
        # Directory exists initially, device gone after cleanup
        mock_exists.side_effect = [True, False]  # dir exists, device doesn't exist
        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        assert result is True

        # Verify operations
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("power" in str(c) and "echo 0" in str(c) for c in calls)
        assert any("rmdir" in str(c) for c in calls)

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_directory_not_exists(self, mock_sleep, mock_exists, mock_run):
        """Test cleanup when directory doesn't exist."""
        # Directory already removed
        mock_exists.side_effect = [False, False]

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        # Should verify device is gone and succeed
        assert result is True

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_deactivate_fails(self, mock_sleep, mock_exists, mock_run):
        """Test when deactivating device fails."""
        mock_exists.side_effect = [True, True, False]  # dir exists, still exists, then gone

        # Deactivate fails, but rmdir succeeds
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "bash"),  # power=0 fails
            MagicMock(returncode=0),  # rmdir succeeds
        ]

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        # Should fail because deactivation failed
        assert result is False

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_rmdir_fails(self, mock_sleep, mock_exists, mock_run):
        """Test when removing directory fails."""
        mock_exists.side_effect = [True, True]

        # Deactivate succeeds, but rmdir fails
        mock_run.side_effect = [
            MagicMock(returncode=0),  # power=0 succeeds
            subprocess.CalledProcessError(1, "rmdir"),  # rmdir fails
        ]

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        assert result is False

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_device_still_exists(self, mock_sleep, mock_exists, mock_run):
        """Test when device still exists after cleanup."""
        # Directory exists, device still exists after cleanup
        mock_exists.side_effect = [True] + [True] * 11  # Dir exists, device persists

        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        # Should fail because device didn't disappear
        assert result is False
        # Should have checked multiple times
        assert mock_sleep.call_count >= 5

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_cleanup_generic_exception(self, mock_exists, mock_run):
        """Test handling of generic exceptions."""
        mock_exists.side_effect = Exception("Unexpected error")

        result = cleanup_null_blk_device("/dev/nullb0", 0)

        assert result is False

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    @patch("time.sleep")
    def test_cleanup_idempotent(self, mock_sleep, mock_exists, mock_run):
        """Test that cleanup is idempotent (safe to call multiple times)."""
        # Directory doesn't exist (already cleaned)
        mock_exists.side_effect = [False, False]

        result = cleanup_null_blk_device("/dev/nullb5", 5)

        # Should succeed even if nothing to clean
        assert result is True


class TestCleanupOrphanedNullBlkDevices:
    """Test cleanup_orphaned_null_blk_devices function."""

    @patch("pathlib.Path.exists")
    def test_cleanup_orphaned_configfs_not_exists(self, mock_exists):
        """Test when configfs nullb directory doesn't exist."""
        mock_exists.return_value = False

        cleaned = cleanup_orphaned_null_blk_devices()

        assert cleaned == 0

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    def test_cleanup_orphaned_no_devices(self, mock_exists, mock_iterdir):
        """Test when no devices exist."""
        mock_exists.return_value = True
        mock_iterdir.return_value = []  # No devices

        cleaned = cleanup_orphaned_null_blk_devices()

        assert cleaned == 0

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_stale_device(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir
    ):
        """Test cleaning up stale orphaned devices."""
        mock_exists.return_value = True

        # Create mock device directory
        mock_device_dir = MagicMock()
        mock_device_dir.name = "nullb3"
        mock_device_dir.is_dir.return_value = True
        mock_iterdir.return_value = [mock_device_dir]

        # Device is old enough to clean (90 seconds old, threshold is 60)
        mock_time.return_value = 1000.0
        mock_stat_obj = MagicMock()
        mock_stat_obj.st_mtime = 910.0  # 90 seconds ago
        mock_device_dir.stat.return_value = mock_stat_obj

        # Cleanup succeeds
        mock_cleanup.return_value = True

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=60)

        assert cleaned == 1
        mock_cleanup.assert_called_once_with("/dev/nullb3", 3)

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_recent_device_skipped(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir
    ):
        """Test that recent devices are not cleaned up (race condition prevention)."""
        mock_exists.return_value = True

        # Create mock device directory
        mock_device_dir = MagicMock()
        mock_device_dir.name = "nullb5"
        mock_device_dir.is_dir.return_value = True
        mock_iterdir.return_value = [mock_device_dir]

        # Device is too recent (30 seconds old, threshold is 60)
        mock_time.return_value = 1000.0
        mock_stat_obj = MagicMock()
        mock_stat_obj.st_mtime = 970.0  # 30 seconds ago
        mock_device_dir.stat.return_value = mock_stat_obj

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=60)

        assert cleaned == 0
        mock_cleanup.assert_not_called()

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_multiple_devices(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir
    ):
        """Test cleaning up multiple orphaned devices."""
        mock_exists.return_value = True

        # Create mock device directories
        devices = []
        for i in [1, 5, 10]:
            mock_device_dir = MagicMock()
            mock_device_dir.name = f"nullb{i}"
            mock_device_dir.is_dir.return_value = True
            mock_stat_obj = MagicMock()
            mock_stat_obj.st_mtime = 900.0  # All stale (100 seconds old)
            mock_device_dir.stat.return_value = mock_stat_obj
            devices.append(mock_device_dir)

        mock_iterdir.return_value = devices
        mock_time.return_value = 1000.0

        # All cleanups succeed
        mock_cleanup.return_value = True

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=60)

        assert cleaned == 3
        assert mock_cleanup.call_count == 3

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_some_fail(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir
    ):
        """Test when some cleanups fail."""
        mock_exists.return_value = True

        # Create mock device directories
        devices = []
        for i in [2, 4]:
            mock_device_dir = MagicMock()
            mock_device_dir.name = f"nullb{i}"
            mock_device_dir.is_dir.return_value = True
            mock_stat_obj = MagicMock()
            mock_stat_obj.st_mtime = 900.0
            mock_device_dir.stat.return_value = mock_stat_obj
            devices.append(mock_device_dir)

        mock_iterdir.return_value = devices
        mock_time.return_value = 1000.0

        # First cleanup succeeds, second fails
        mock_cleanup.side_effect = [True, False]

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=60)

        # Only count successful cleanups
        assert cleaned == 1

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    def test_cleanup_orphaned_non_nullb_directories(self, mock_is_dir, mock_exists, mock_iterdir):
        """Test that non-nullb directories are skipped."""
        mock_exists.return_value = True

        # Create mock directories with non-nullb names
        mock_dir1 = MagicMock()
        mock_dir1.name = "other_dir"
        mock_dir1.is_dir.return_value = True

        mock_dir2 = MagicMock()
        mock_dir2.name = "nullb_test"  # Doesn't match pattern
        mock_dir2.is_dir.return_value = True

        mock_iterdir.return_value = [mock_dir1, mock_dir2]

        cleaned = cleanup_orphaned_null_blk_devices()

        assert cleaned == 0

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    def test_cleanup_orphaned_stat_fails(self, mock_stat, mock_is_dir, mock_exists, mock_iterdir):
        """Test handling when stat() fails (device being deleted concurrently)."""
        mock_exists.return_value = True

        mock_device_dir = MagicMock()
        mock_device_dir.name = "nullb7"
        mock_device_dir.is_dir.return_value = True
        mock_device_dir.stat.side_effect = OSError("Cannot stat")

        mock_iterdir.return_value = [mock_device_dir]

        cleaned = cleanup_orphaned_null_blk_devices()

        # Should skip device with stat error
        assert cleaned == 0

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    def test_cleanup_orphaned_generic_exception(self, mock_exists, mock_iterdir):
        """Test handling of generic exceptions."""
        mock_exists.return_value = True
        mock_iterdir.side_effect = Exception("Unexpected error")

        cleaned = cleanup_orphaned_null_blk_devices()

        # Should handle exception gracefully
        assert cleaned == 0

    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_per_device_exception(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir
    ):
        """Test that exceptions during individual device cleanup don't stop the loop."""
        mock_exists.return_value = True

        # Create two mock devices
        devices = []
        for i in [8, 9]:
            mock_device_dir = MagicMock()
            mock_device_dir.name = f"nullb{i}"
            mock_device_dir.is_dir.return_value = True
            mock_stat_obj = MagicMock()
            mock_stat_obj.st_mtime = 900.0
            mock_device_dir.stat.return_value = mock_stat_obj
            devices.append(mock_device_dir)

        mock_iterdir.return_value = devices
        mock_time.return_value = 1000.0

        # First device cleanup throws exception, second succeeds
        mock_cleanup.side_effect = [Exception("Error"), True]

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=60)

        # Should still clean the second device
        assert cleaned == 1

    @pytest.mark.parametrize("staleness", [0, 30, 60, 120, 300])
    @patch("pathlib.Path.iterdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.stat")
    @patch("time.time")
    @patch("src.kerneldev_mcp.device_utils.cleanup_null_blk_device")
    def test_cleanup_orphaned_various_staleness(
        self, mock_cleanup, mock_time, mock_stat, mock_is_dir, mock_exists, mock_iterdir, staleness
    ):
        """Test cleanup with various staleness thresholds."""
        mock_exists.return_value = True

        mock_device_dir = MagicMock()
        mock_device_dir.name = "nullb0"
        mock_device_dir.is_dir.return_value = True

        # Device is 100 seconds old
        mock_time.return_value = 1000.0
        mock_stat_obj = MagicMock()
        mock_stat_obj.st_mtime = 900.0
        mock_device_dir.stat.return_value = mock_stat_obj

        mock_iterdir.return_value = [mock_device_dir]
        mock_cleanup.return_value = True

        cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=staleness)

        # Should clean if device age (100s) >= staleness threshold
        if staleness <= 100:
            assert cleaned == 1
        else:
            assert cleaned == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
