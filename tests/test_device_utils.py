"""
Unit tests for device_utils.py - device management utilities.
Tests use mocks to avoid requiring root/sudo or actual devices.
"""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from src.kerneldev_mcp.device_utils import (
    create_loop_device,
    cleanup_loop_device,
    validate_block_device,
)


class TestCreateLoopDevice:
    """Test create_loop_device function with mocked subprocess calls."""

    @patch("subprocess.run")
    def test_create_loop_device_success(self, mock_run):
        """Test successful loop device creation."""
        # Mock subprocess responses
        mock_run.side_effect = [
            MagicMock(returncode=0),  # truncate
            MagicMock(returncode=0, stdout="/dev/loop0\n"),  # losetup
            MagicMock(returncode=0),  # chmod
        ]

        loop_dev, backing_file = create_loop_device("10G", "test")

        assert loop_dev == "/dev/loop0"
        assert backing_file.name == "test.img"
        assert mock_run.call_count == 3

        # Verify truncate called correctly
        assert "truncate" in mock_run.call_args_list[0][0][0]
        assert "10G" in mock_run.call_args_list[0][0][0]

    @patch("subprocess.run")
    def test_create_loop_device_with_custom_backing_dir(self, mock_run, tmp_path):
        """Test loop device creation with custom backing directory."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="/dev/loop1\n"),
            MagicMock(returncode=0),
        ]

        custom_dir = tmp_path / "custom"
        loop_dev, backing_file = create_loop_device("5G", "custom", custom_dir)

        assert loop_dev == "/dev/loop1"
        assert backing_file.parent == custom_dir

    @patch("subprocess.run")
    def test_create_loop_device_truncate_fails(self, mock_run):
        """Test handling of truncate failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "truncate")

        loop_dev, backing_file = create_loop_device("10G", "test")

        assert loop_dev is None
        assert backing_file is None

    @patch("subprocess.run")
    @patch("pathlib.Path.unlink")
    def test_create_loop_device_losetup_fails(self, mock_unlink, mock_run, tmp_path):
        """Test handling of losetup failure."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # truncate succeeds
            subprocess.CalledProcessError(1, "losetup"),  # losetup fails
        ]

        loop_dev, backing_file = create_loop_device("10G", "test", tmp_path)

        assert loop_dev is None
        assert backing_file is None

    @patch("subprocess.run")
    def test_create_loop_device_chmod_fails(self, mock_run):
        """Test handling of chmod failure."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # truncate succeeds
            MagicMock(returncode=0, stdout="/dev/loop0\n"),  # losetup succeeds
            subprocess.CalledProcessError(1, "chmod"),  # chmod fails
            MagicMock(returncode=0),  # losetup -d for cleanup
        ]

        loop_dev, backing_file = create_loop_device("10G", "test")

        assert loop_dev is None
        assert backing_file is None
        # Verify cleanup was attempted (losetup -d call)
        assert mock_run.call_count == 4  # truncate, losetup, chmod, losetup -d

    @patch("subprocess.run")
    def test_create_loop_device_various_sizes(self, mock_run):
        """Test creating loop devices with various size formats."""
        for size in ["10G", "512M", "1024K"]:
            mock_run.reset_mock()
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0, stdout="/dev/loop0\n"),
                MagicMock(returncode=0),
            ]

            loop_dev, _ = create_loop_device(size, "test")
            assert loop_dev == "/dev/loop0"

    @patch("subprocess.run")
    def test_create_loop_device_default_backing_dir(self, mock_run, tmp_path):
        """Test loop device creation uses default backing directory."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="/dev/loop0\n"),
            MagicMock(returncode=0),
        ]

        loop_dev, backing_file = create_loop_device("10G", "test")

        assert loop_dev == "/dev/loop0"
        assert "/var/tmp/kerneldev-loop" in str(backing_file)


class TestCleanupLoopDevice:
    """Test cleanup_loop_device function."""

    @patch("subprocess.run")
    def test_cleanup_loop_device_success(self, mock_run, tmp_path):
        """Test successful cleanup."""
        backing_file = tmp_path / "test.img"
        backing_file.touch()

        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_loop_device("/dev/loop0", backing_file)

        assert result is True
        assert not backing_file.exists()  # File was removed
        mock_run.assert_called_once()
        assert "losetup" in mock_run.call_args[0][0]
        assert "-d" in mock_run.call_args[0][0]

    @patch("subprocess.run")
    def test_cleanup_loop_device_without_backing_file(self, mock_run):
        """Test cleanup without backing file."""
        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_loop_device("/dev/loop0", None)

        assert result is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_cleanup_loop_device_losetup_fails(self, mock_run):
        """Test handling of losetup failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "losetup")

        result = cleanup_loop_device("/dev/loop0")

        assert result is False
        # Should have tried both losetup -d and losetup -D
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cleanup_loop_device_file_removal_fails(self, mock_run, tmp_path):
        """Test handling of backing file removal failure."""
        backing_file = tmp_path / "test.img"
        backing_file.touch()

        # Make file unremovable by patching unlink
        mock_run.return_value = MagicMock(returncode=0)

        with patch.object(Path, "unlink", side_effect=OSError):
            result = cleanup_loop_device("/dev/loop0", backing_file)

            assert result is False

    @patch("subprocess.run")
    def test_cleanup_loop_device_timeout(self, mock_run):
        """Test handling of timeout during cleanup."""
        mock_run.side_effect = subprocess.TimeoutExpired("losetup", 10)

        result = cleanup_loop_device("/dev/loop0")

        assert result is False
        # Should have tried both losetup -d and losetup -D
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cleanup_loop_device_force_detach(self, mock_run, tmp_path):
        """Test force detach when normal detach fails."""
        backing_file = tmp_path / "test.img"
        backing_file.touch()

        # First losetup -d fails, second losetup -D succeeds
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "losetup -d"),
            MagicMock(returncode=0),  # losetup -D
        ]

        result = cleanup_loop_device("/dev/loop0", backing_file)

        assert result is False  # Because losetup -d failed
        assert mock_run.call_count == 2
        # Verify losetup -D was called
        assert "-D" in mock_run.call_args_list[1][0][0]


class TestValidateBlockDevice:
    """Test validate_block_device function."""

    def test_validate_nonexistent_device(self):
        """Test validation of non-existent device."""
        valid, error = validate_block_device("/dev/nonexistent")

        assert valid is False
        assert "does not exist" in error

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    def test_validate_non_block_device(self, mock_stat, mock_exists, tmp_path):
        """Test validation of non-block device."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFREG | 0o644  # Regular file

        valid, error = validate_block_device(str(tmp_path / "file"))

        assert valid is False
        assert "Not a block device" in error

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_mounted_device_not_readonly(self, mock_run, mock_stat, mock_exists):
        """Test validation fails for mounted device without readonly flag."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=0, stdout="/mnt/test\n")

        valid, error = validate_block_device("/dev/sda1", readonly=False)

        assert valid is False
        assert "mounted" in error.lower()

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_mounted_device_with_readonly(self, mock_run, mock_stat, mock_exists):
        """Test validation succeeds for mounted device with readonly flag."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=0, stdout="/mnt/test\n")

        valid, error = validate_block_device("/dev/sda1", readonly=True)

        assert valid is True
        assert error == ""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_device_with_filesystem_require_empty(self, mock_run, mock_stat, mock_exists):
        """Test validation fails for device with filesystem when require_empty=True."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.side_effect = [
            MagicMock(returncode=1),  # findmnt - not mounted
            MagicMock(returncode=0, stdout="TYPE=ext4\n"),  # blkid - has filesystem
        ]

        valid, error = validate_block_device("/dev/sda1", require_empty=True)

        assert valid is False
        assert "filesystem signature" in error.lower()

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_whole_disk_without_readonly(self, mock_run, mock_stat, mock_exists):
        """Test validation fails for whole disk without readonly flag."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=1)  # Not mounted

        for device in ["/dev/sda", "/dev/nvme0n1", "/dev/vda"]:
            valid, error = validate_block_device(device, readonly=False)
            assert valid is False, f"Should reject whole disk {device}"
            assert "readonly=True" in error

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_whole_disk_with_readonly(self, mock_run, mock_stat, mock_exists):
        """Test validation succeeds for whole disk with readonly flag."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=1)

        valid, error = validate_block_device("/dev/sda", readonly=True)

        assert valid is True
        assert error == ""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_partition_success(self, mock_run, mock_stat, mock_exists):
        """Test validation succeeds for partition."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=1)  # Not mounted

        valid, error = validate_block_device("/dev/sda1")

        assert valid is True
        assert error == ""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_nvme_partition(self, mock_run, mock_stat, mock_exists):
        """Test validation succeeds for NVMe partition."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.return_value = MagicMock(returncode=1)

        valid, error = validate_block_device("/dev/nvme0n1p1")

        assert valid is True
        assert error == ""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    def test_validate_device_stat_fails(self, mock_stat, mock_exists):
        """Test validation fails when stat raises exception."""
        mock_exists.return_value = True
        mock_stat.side_effect = PermissionError("Access denied")

        valid, error = validate_block_device("/dev/sda1")

        assert valid is False
        assert "Cannot stat device" in error

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_findmnt_exception_handled(self, mock_run, mock_stat, mock_exists):
        """Test that findmnt exceptions are handled gracefully."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.side_effect = Exception("findmnt error")

        # Should continue validation despite findmnt error
        valid, error = validate_block_device("/dev/sda1")

        # Validation should succeed since it's a partition (not a whole disk)
        assert valid is True

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("subprocess.run")
    def test_validate_blkid_exception_handled(self, mock_run, mock_stat, mock_exists):
        """Test that blkid exceptions are handled gracefully."""
        import stat as stat_module

        mock_exists.return_value = True
        mock_stat.return_value.st_mode = stat_module.S_IFBLK | 0o660
        mock_run.side_effect = [
            MagicMock(returncode=1),  # findmnt - not mounted
            Exception("blkid error"),  # blkid fails
        ]

        # Should continue validation despite blkid error
        valid, error = validate_block_device("/dev/sda1", require_empty=True)

        # Validation should succeed since blkid error is logged but not fatal
        assert valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
