"""
Unit tests for DeviceSpec, DeviceProfile, and VMDeviceManager classes.
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from src.kerneldev_mcp.boot_manager import (
    DeviceSpec,
    DeviceProfile,
    VMDeviceManager,
    MAX_CUSTOM_DEVICES,
    MAX_DEVICE_SIZE_GB,
    MAX_TMPFS_TOTAL_GB,
)


class TestDeviceSpec:
    """Test DeviceSpec validation."""

    def test_valid_loop_device(self):
        """Test valid loop device specification."""
        spec = DeviceSpec(size="10G", name="test")
        valid, error = spec.validate()
        assert valid is True
        assert error == ""

    def test_valid_existing_device(self, tmp_path):
        """Test valid existing device specification."""
        # Create a fake block device file for testing
        device_file = tmp_path / "fake_device"
        device_file.touch()

        # Note: We can't actually create a block device without root,
        # so this test will fail validation for not being a block device.
        # This is expected behavior.
        spec = DeviceSpec(path=str(device_file))
        valid, error = spec.validate()
        assert valid is False
        assert "Not a block device" in error

    def test_both_path_and_size(self):
        """Test that specifying both path and size fails."""
        spec = DeviceSpec(path="/dev/sda1", size="10G")
        valid, error = spec.validate()
        assert valid is False
        assert "Exactly one of 'path' or 'size'" in error

    def test_neither_path_nor_size(self):
        """Test that specifying neither path nor size fails."""
        spec = DeviceSpec(name="test")
        valid, error = spec.validate()
        assert valid is False
        assert "Exactly one of 'path' or 'size'" in error

    def test_invalid_size_format(self):
        """Test invalid size format."""
        spec = DeviceSpec(size="invalid")
        valid, error = spec.validate()
        assert valid is False
        assert "Invalid size format" in error

    def test_size_too_large(self):
        """Test size exceeding maximum."""
        spec = DeviceSpec(size=f"{MAX_DEVICE_SIZE_GB + 1}G")
        valid, error = spec.validate()
        assert valid is False
        assert "exceeds maximum" in error

    def test_valid_size_formats(self):
        """Test various valid size formats."""
        for size in ["10G", "512M", "1024K", "100g", "256m"]:
            spec = DeviceSpec(size=size)
            valid, error = spec.validate()
            assert valid is True, f"Size {size} should be valid but got error: {error}"

    def test_device_not_exists(self):
        """Test non-existent device path."""
        spec = DeviceSpec(path="/dev/nonexistent_device")
        valid, error = spec.validate()
        assert valid is False
        assert "does not exist" in error

    def test_size_unit_conversion(self):
        """Test size unit conversion logic."""
        # Test that 1G = 1024M in our validation
        spec_g = DeviceSpec(size="1G")
        spec_m = DeviceSpec(size="1024M")

        valid_g, _ = spec_g.validate()
        valid_m, _ = spec_m.validate()

        assert valid_g is True
        assert valid_m is True


class TestDeviceProfile:
    """Test DeviceProfile predefined configurations."""

    def test_get_fstests_default_profile(self):
        """Test getting default fstests profile."""
        profile = DeviceProfile.get_profile("fstests_default")
        assert profile is not None
        assert profile.name == "fstests_default"
        assert len(profile.devices) == 7
        assert profile.devices[0].name == "test"
        assert profile.devices[0].env_var == "TEST_DEV"
        assert profile.devices[6].name == "logwrites"

    def test_get_fstests_small_profile(self):
        """Test getting small fstests profile."""
        profile = DeviceProfile.get_profile("fstests_small")
        assert profile is not None
        assert len(profile.devices) == 7
        assert all(d.size == "5G" for d in profile.devices)

    def test_get_fstests_large_profile(self):
        """Test getting large fstests profile."""
        profile = DeviceProfile.get_profile("fstests_large")
        assert profile is not None
        assert len(profile.devices) == 7
        assert all(d.size == "50G" for d in profile.devices)

    def test_profile_with_tmpfs(self):
        """Test profile with tmpfs override."""
        profile = DeviceProfile.get_profile("fstests_default", use_tmpfs=True)
        assert profile is not None
        assert all(d.use_tmpfs is True for d in profile.devices)

    def test_profile_without_tmpfs(self):
        """Test profile without tmpfs."""
        profile = DeviceProfile.get_profile("fstests_default", use_tmpfs=False)
        assert profile is not None
        assert all(d.use_tmpfs is False for d in profile.devices)

    def test_nonexistent_profile(self):
        """Test getting non-existent profile."""
        profile = DeviceProfile.get_profile("nonexistent")
        assert profile is None

    def test_list_profiles(self):
        """Test listing available profiles."""
        profiles = DeviceProfile.list_profiles()
        assert len(profiles) == 3
        assert ("fstests_default", "Default 7 devices for fstests (10G each)") in profiles
        assert ("fstests_small", "Smaller devices (5G each) for faster setup") in profiles
        assert ("fstests_large", "Larger devices (50G each) for extensive testing") in profiles

    def test_profile_device_order(self):
        """Test that profile devices have correct order."""
        profile = DeviceProfile.get_profile("fstests_default")
        for i, device in enumerate(profile.devices):
            assert device.order == i


class TestVMDeviceManager:
    """Test DeviceManager setup and cleanup."""

    @pytest.mark.asyncio
    async def test_init(self):
        """Test DeviceManager initialization."""
        manager = VMDeviceManager()
        assert manager.created_loop_devices == []
        assert manager.attached_block_devices == []
        assert manager.device_specs == []
        assert manager.tmpfs_setup is False

    @pytest.mark.asyncio
    async def test_too_many_devices(self):
        """Test device count limit."""
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="1G") for _ in range(MAX_CUSTOM_DEVICES + 1)]

        success, error, _ = await manager.setup_devices(specs)
        assert success is False
        assert "Too many devices" in error

    @pytest.mark.asyncio
    async def test_invalid_device_spec(self):
        """Test setup with invalid device spec."""
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="invalid_size")]

        success, error, _ = await manager.setup_devices(specs)
        assert success is False
        assert "Invalid size format" in error

    @pytest.mark.asyncio
    async def test_tmpfs_size_limit(self):
        """Test tmpfs total size limit."""
        manager = VMDeviceManager()
        # Create devices that exceed tmpfs limit
        specs = [
            DeviceSpec(size=f"{MAX_TMPFS_TOTAL_GB + 1}G", use_tmpfs=True)
        ]

        success, error, _ = await manager.setup_devices(specs)
        assert success is False
        assert "exceeds maximum" in error

    @pytest.mark.asyncio
    async def test_device_ordering(self):
        """Test device ordering by order parameter."""
        manager = VMDeviceManager()
        specs = [
            DeviceSpec(size="1G", name="third", order=2),
            DeviceSpec(size="1G", name="first", order=0),
            DeviceSpec(size="1G", name="second", order=1),
        ]

        # Just test that sorting works (not actually creating devices)
        manager.device_specs = sorted(specs, key=lambda s: s.order)

        assert manager.device_specs[0].name == "first"
        assert manager.device_specs[1].name == "second"
        assert manager.device_specs[2].name == "third"

    @pytest.mark.asyncio
    @patch('src.kerneldev_mcp.boot_manager._setup_tmpfs_for_loop_devices')
    @patch('src.kerneldev_mcp.boot_manager._create_host_loop_device')
    async def test_setup_loop_devices(self, mock_create, mock_setup_tmpfs):
        """Test setting up loop devices."""
        mock_setup_tmpfs.return_value = True
        mock_create.return_value = ("/dev/loop0", Path("/tmp/backing"))

        manager = VMDeviceManager()
        specs = [DeviceSpec(size="10G", name="test", use_tmpfs=True)]

        success, error, devices = await manager.setup_devices(specs)

        assert success is True
        assert error == ""
        assert len(devices) == 1
        assert devices[0] == "/dev/loop0"
        assert len(manager.created_loop_devices) == 1

    @pytest.mark.asyncio
    @patch('src.kerneldev_mcp.boot_manager._cleanup_host_loop_device')
    @patch('src.kerneldev_mcp.boot_manager._cleanup_tmpfs_for_loop_devices')
    async def test_cleanup(self, mock_cleanup_tmpfs, mock_cleanup_device):
        """Test cleanup of devices."""
        manager = VMDeviceManager()
        manager.created_loop_devices = [("/dev/loop0", Path("/tmp/backing"))]
        manager.tmpfs_setup = True

        manager.cleanup()

        mock_cleanup_device.assert_called_once_with("/dev/loop0", Path("/tmp/backing"))
        mock_cleanup_tmpfs.assert_called_once()
        assert manager.created_loop_devices == []
        assert manager.tmpfs_setup is False

    def test_get_vng_disk_args(self):
        """Test generating vng disk arguments."""
        manager = VMDeviceManager()
        manager.created_loop_devices = [("/dev/loop0", Path("/tmp/backing1")), ("/dev/loop1", Path("/tmp/backing2"))]
        manager.attached_block_devices = ["/dev/sda1"]

        args = manager.get_vng_disk_args()

        expected = ["--disk", "/dev/loop0", "--disk", "/dev/loop1", "--disk", "/dev/sda1"]
        assert args == expected

    def test_get_vm_env_script(self):
        """Test generating VM environment variable script."""
        manager = VMDeviceManager()
        manager.device_specs = [
            DeviceSpec(size="10G", name="test", env_var="TEST_DEV", order=0),
            DeviceSpec(size="10G", name="scratch", env_var="SCRATCH_DEV", order=1),
            DeviceSpec(size="10G", name="other", order=2),  # No env_var
        ]

        script = manager.get_vm_env_script()

        assert "export TEST_DEV=/dev/vda" in script
        assert "export SCRATCH_DEV=/dev/vdb" in script
        assert "other" not in script

    def test_get_vm_env_script_with_custom_index(self):
        """Test VM env script with custom index."""
        manager = VMDeviceManager()
        manager.device_specs = [
            DeviceSpec(size="10G", name="test", env_var="TEST_DEV", env_var_index=2, order=0),
        ]

        script = manager.get_vm_env_script()

        # Should use vdc (index 2) instead of vda (index 0)
        assert "export TEST_DEV=/dev/vdc" in script

    def test_get_vm_env_script_empty(self):
        """Test VM env script with no env vars."""
        manager = VMDeviceManager()
        manager.device_specs = [
            DeviceSpec(size="10G", name="test", order=0),
        ]

        script = manager.get_vm_env_script()

        assert script == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
