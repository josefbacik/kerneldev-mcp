"""
Unit tests for device_manager module.
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
import subprocess

from kerneldev_mcp.device_manager import (
    DeviceManager,
    DeviceConfig,
    DeviceSetupResult
)


@pytest.fixture
def device_manager(tmp_path):
    """Create a DeviceManager with temporary work directory."""
    return DeviceManager(work_dir=tmp_path)


@pytest.fixture
def mock_subprocess_success():
    """Mock subprocess.run to return success."""
    with patch('kerneldev_mcp.device_manager.subprocess.run') as mock:
        mock.return_value = Mock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def mock_subprocess_failure():
    """Mock subprocess.run to raise CalledProcessError."""
    with patch('kerneldev_mcp.device_manager.subprocess.run') as mock:
        mock.side_effect = subprocess.CalledProcessError(1, "cmd")
        yield mock


class TestDeviceManager:
    """Test DeviceManager class."""

    def test_init_creates_work_dir(self, tmp_path):
        """Test that DeviceManager creates work directory."""
        work_dir = tmp_path / "test_work"
        manager = DeviceManager(work_dir=work_dir)

        assert manager.work_dir == work_dir
        assert work_dir.exists()

    def test_init_default_work_dir(self):
        """Test default work directory."""
        manager = DeviceManager()
        assert manager.work_dir == Path("/tmp/kerneldev-fstests")

    def test_find_free_loop_device_success(self, device_manager):
        """Test finding a free loop device."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="/dev/loop0\n",
                stderr=""
            )

            loop_dev = device_manager.find_free_loop_device()

            assert loop_dev == "/dev/loop0"
            mock_run.assert_called_once()

    def test_find_free_loop_device_failure(self, device_manager):
        """Test finding loop device when none available."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "losetup")

            loop_dev = device_manager.find_free_loop_device()

            assert loop_dev is None

    def test_create_loop_device_success(self, device_manager):
        """Test creating a loop device."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            with patch.object(device_manager, 'find_free_loop_device') as mock_find:
                mock_find.return_value = "/dev/loop0"

                loop_dev, backing_file = device_manager.create_loop_device("10G", "test")

                assert loop_dev == "/dev/loop0"
                assert backing_file == device_manager.work_dir / "test.img"
                assert backing_file.exists()
                assert "/dev/loop0" in device_manager._created_loop_devices

    def test_create_loop_device_no_free_device(self, device_manager):
        """Test creating loop device when none available."""
        with patch('kerneldev_mcp.device_manager.subprocess.run'):
            with patch.object(device_manager, 'find_free_loop_device') as mock_find:
                mock_find.return_value = None

                loop_dev, backing_file = device_manager.create_loop_device("10G", "test")

                assert loop_dev is None
                assert backing_file is None

    def test_validate_device_success(self, device_manager):
        """Test validating a block device."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            with patch('kerneldev_mcp.device_manager.Path.exists') as mock_exists:
                mock_exists.return_value = True

                result = device_manager.validate_device("/dev/sda1")

                assert result is True

    def test_validate_device_not_exists(self, device_manager):
        """Test validating non-existent device."""
        with patch('kerneldev_mcp.device_manager.Path.exists') as mock_exists:
            mock_exists.return_value = False

            result = device_manager.validate_device("/dev/nonexistent")

            assert result is False

    def test_validate_device_not_block(self, device_manager):
        """Test validating non-block device."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1)

            with patch('kerneldev_mcp.device_manager.Path.exists') as mock_exists:
                mock_exists.return_value = True

                result = device_manager.validate_device("/tmp/file")

                assert result is False

    def test_get_device_size_success(self, device_manager):
        """Test getting device size."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="10737418240\n",
                stderr=""
            )

            size = device_manager.get_device_size("/dev/sda1")

            assert size == 10737418240

    def test_get_device_size_failure(self, device_manager):
        """Test getting device size failure."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "blockdev")

            size = device_manager.get_device_size("/dev/sda1")

            assert size is None

    def test_create_filesystem_ext4(self, device_manager):
        """Test creating ext4 filesystem."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.create_filesystem("/dev/loop0", "ext4")

            assert result is True
            # Check that -F flag was used for ext4
            call_args = mock_run.call_args[0][0]
            assert "mkfs.ext4" in call_args
            assert "-F" in call_args

    def test_create_filesystem_btrfs(self, device_manager):
        """Test creating btrfs filesystem."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.create_filesystem("/dev/loop0", "btrfs")

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "mkfs.btrfs" in call_args
            assert "-f" in call_args

    def test_create_filesystem_with_options(self, device_manager):
        """Test creating filesystem with options."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.create_filesystem(
                "/dev/loop0", "ext4", mkfs_options="-b 4096"
            )

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "-b" in call_args
            assert "4096" in call_args

    def test_create_filesystem_failure(self, device_manager):
        """Test filesystem creation failure."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "mkfs")

            result = device_manager.create_filesystem("/dev/loop0", "ext4")

            assert result is False

    def test_mount_device_success(self, device_manager, tmp_path):
        """Test mounting a device."""
        mount_point = tmp_path / "mnt"

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.mount_device("/dev/loop0", mount_point)

            assert result is True
            assert mount_point.exists()
            assert mount_point in device_manager._created_mounts

    def test_mount_device_with_options(self, device_manager, tmp_path):
        """Test mounting device with options."""
        mount_point = tmp_path / "mnt"

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.mount_device(
                "/dev/loop0", mount_point, mount_options="noatime,nodiratime"
            )

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "-o" in call_args
            assert "noatime,nodiratime" in call_args

    def test_mount_device_failure(self, device_manager, tmp_path):
        """Test mount failure."""
        mount_point = tmp_path / "mnt"

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "mount")

            result = device_manager.mount_device("/dev/loop0", mount_point)

            assert result is False

    def test_umount_device_success(self, device_manager, tmp_path):
        """Test unmounting a device."""
        mount_point = tmp_path / "mnt"
        device_manager._created_mounts.append(mount_point)

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.umount_device(mount_point)

            assert result is True
            assert mount_point not in device_manager._created_mounts

    def test_umount_device_failure(self, device_manager, tmp_path):
        """Test unmount failure."""
        mount_point = tmp_path / "mnt"

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "umount")

            result = device_manager.umount_device(mount_point)

            assert result is False

    def test_detach_loop_device_success(self, device_manager):
        """Test detaching a loop device."""
        device_manager._created_loop_devices.append("/dev/loop0")

        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)

            result = device_manager.detach_loop_device("/dev/loop0")

            assert result is True
            assert "/dev/loop0" not in device_manager._created_loop_devices

    def test_detach_loop_device_failure(self, device_manager):
        """Test detach failure."""
        with patch('kerneldev_mcp.device_manager.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "losetup")

            result = device_manager.detach_loop_device("/dev/loop0")

            assert result is False

    def test_setup_loop_devices_success(self, device_manager):
        """Test setting up loop devices."""
        with patch.object(device_manager, 'create_loop_device') as mock_create:
            mock_create.side_effect = [
                ("/dev/loop0", device_manager.work_dir / "test.img"),
                ("/dev/loop1", device_manager.work_dir / "scratch.img")
            ]

            with patch.object(device_manager, 'create_filesystem') as mock_mkfs:
                mock_mkfs.return_value = True

                with patch.object(device_manager, 'mount_device') as mock_mount:
                    mock_mount.return_value = True

                    result = device_manager.setup_loop_devices(
                        test_size="10G",
                        scratch_size="10G",
                        fstype="ext4"
                    )

                    assert result.success
                    assert result.test_device.device_path == "/dev/loop0"
                    assert result.scratch_device.device_path == "/dev/loop1"
                    assert result.cleanup_needed

    def test_setup_loop_devices_test_creation_fails(self, device_manager):
        """Test setup when test device creation fails."""
        with patch.object(device_manager, 'create_loop_device') as mock_create:
            mock_create.return_value = (None, None)

            result = device_manager.setup_loop_devices()

            assert not result.success
            assert "Failed to create test loop device" in result.message

    def test_setup_loop_devices_scratch_creation_fails(self, device_manager):
        """Test setup when scratch device creation fails."""
        with patch.object(device_manager, 'create_loop_device') as mock_create:
            mock_create.side_effect = [
                ("/dev/loop0", device_manager.work_dir / "test.img"),
                (None, None)
            ]

            with patch.object(device_manager, 'detach_loop_device') as mock_detach:
                mock_detach.return_value = True

                result = device_manager.setup_loop_devices()

                assert not result.success
                assert "Failed to create scratch loop device" in result.message

    def test_setup_existing_devices_success(self, device_manager):
        """Test setting up existing devices."""
        with patch.object(device_manager, 'validate_device') as mock_validate:
            mock_validate.return_value = True

            with patch.object(device_manager, 'create_filesystem') as mock_mkfs:
                mock_mkfs.return_value = True

                with patch.object(device_manager, 'mount_device') as mock_mount:
                    mock_mount.return_value = True

                    result = device_manager.setup_existing_devices(
                        test_dev="/dev/sda1",
                        scratch_dev="/dev/sda2",
                        fstype="btrfs"
                    )

                    assert result.success
                    assert result.test_device.device_path == "/dev/sda1"
                    assert result.scratch_device.device_path == "/dev/sda2"
                    assert not result.cleanup_needed

    def test_setup_existing_devices_invalid_test(self, device_manager):
        """Test setup with invalid test device."""
        with patch.object(device_manager, 'validate_device') as mock_validate:
            mock_validate.return_value = False

            result = device_manager.setup_existing_devices(
                test_dev="/dev/invalid",
                scratch_dev="/dev/sda2",
                fstype="ext4"
            )

            assert not result.success
            assert "not valid" in result.message

    def test_cleanup_all(self, device_manager, tmp_path):
        """Test cleaning up all devices."""
        # Setup some mounts and loop devices
        mount1 = tmp_path / "mnt1"
        mount2 = tmp_path / "mnt2"
        mount1.mkdir()
        mount2.mkdir()

        device_manager._created_mounts = [mount1, mount2]
        device_manager._created_loop_devices = ["/dev/loop0", "/dev/loop1"]

        # Create backing files
        (device_manager.work_dir / "test.img").touch()
        (device_manager.work_dir / "scratch.img").touch()

        with patch.object(device_manager, 'umount_device') as mock_umount:
            mock_umount.return_value = True

            with patch.object(device_manager, 'detach_loop_device') as mock_detach:
                mock_detach.return_value = True

                device_manager.cleanup_all()

                assert len(device_manager._created_mounts) == 0
                assert len(device_manager._created_loop_devices) == 0
                assert not (device_manager.work_dir / "test.img").exists()
                assert not (device_manager.work_dir / "scratch.img").exists()


class TestDeviceConfig:
    """Test DeviceConfig dataclass."""

    def test_device_config_creation(self):
        """Test creating DeviceConfig."""
        config = DeviceConfig(
            device_path="/dev/loop0",
            mount_point=Path("/mnt/test"),
            filesystem_type="ext4",
            size="10G"
        )

        assert config.device_path == "/dev/loop0"
        assert config.mount_point == Path("/mnt/test")
        assert config.filesystem_type == "ext4"
        assert config.size == "10G"

    def test_device_config_defaults(self):
        """Test DeviceConfig default values."""
        config = DeviceConfig(
            device_path="/dev/sda1",
            mount_point=Path("/mnt"),
            filesystem_type="btrfs"
        )

        assert config.size is None
        assert config.mount_options is None
        assert config.mkfs_options is None
        assert config.is_loop_device is False
        assert config.backing_file is None


class TestDeviceSetupResult:
    """Test DeviceSetupResult dataclass."""

    def test_setup_result_success(self):
        """Test successful setup result."""
        test_config = DeviceConfig(
            device_path="/dev/loop0",
            mount_point=Path("/mnt/test"),
            filesystem_type="ext4"
        )

        result = DeviceSetupResult(
            success=True,
            test_device=test_config,
            message="Success"
        )

        assert result.success
        assert result.test_device == test_config
        assert result.message == "Success"

    def test_setup_result_failure(self):
        """Test failure result."""
        result = DeviceSetupResult(
            success=False,
            message="Failed"
        )

        assert not result.success
        assert result.test_device is None
        assert result.scratch_device is None
