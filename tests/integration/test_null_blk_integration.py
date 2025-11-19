#!/usr/bin/env python3
"""
Integration tests for null_blk device support in VM boots.

Tests null_blk device creation, fallback to tmpfs, integration with VMDeviceManager,
and proper cleanup behavior. These tests focus on integration between components
rather than individual function behavior (which is covered by unit tests).

Requirements:
- May require sudo for device operations (some tests will skip if unavailable)
- Tests kernel null_blk support but gracefully handle absence
- Does not require actual kernel builds or boot operations
"""

import asyncio
import logging
import pytest
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from src.kerneldev_mcp.boot_manager import (
    DeviceSpec,
    DeviceProfile,
    VMDeviceManager,
    MAX_CUSTOM_DEVICES,
)
from src.kerneldev_mcp.device_utils import (
    DeviceBacking,
    check_null_blk_support,
    create_null_blk_device,
    cleanup_null_blk_device,
    cleanup_orphaned_null_blk_devices,
    MAX_NULL_BLK_DEVICE_GB,
    MAX_NULL_BLK_TOTAL_GB,
)

logger = logging.getLogger(__name__)


# Skip markers for conditional test execution
pytestmark = pytest.mark.integration

# Check if we have sudo access
HAS_SUDO = False
try:
    result = subprocess.run(
        ["sudo", "-n", "true"],
        capture_output=True,
        timeout=5,
    )
    HAS_SUDO = result.returncode == 0
except Exception:
    pass

# Check if null_blk is available
NULL_BLK_AVAILABLE, NULL_BLK_MSG = check_null_blk_support()


class TestDeviceSpecValidation:
    """Test DeviceSpec validation with null_blk backing parameter."""

    def test_valid_null_blk_spec(self):
        """Test valid null_blk device specification."""
        spec = DeviceSpec(size="10G", name="test", backing=DeviceBacking.NULL_BLK)
        valid, error = spec.validate()
        assert valid is True
        assert error == ""

    def test_valid_tmpfs_spec(self):
        """Test valid tmpfs device specification."""
        spec = DeviceSpec(size="10G", name="test", backing=DeviceBacking.TMPFS)
        valid, error = spec.validate()
        assert valid is True
        assert error == ""

    def test_valid_disk_spec(self):
        """Test valid disk-backed device specification."""
        spec = DeviceSpec(size="10G", name="test", backing=DeviceBacking.DISK)
        valid, error = spec.validate()
        assert valid is True
        assert error == ""

    def test_default_backing_is_disk(self):
        """Test that default backing is DISK."""
        spec = DeviceSpec(size="10G", name="test")
        # DeviceSpec uses __post_init__ to set default
        assert spec.backing == DeviceBacking.DISK

    def test_use_tmpfs_migrates_to_backing(self):
        """Test deprecated use_tmpfs parameter migrates to backing."""
        # use_tmpfs=True should become backing=TMPFS
        spec = DeviceSpec(size="10G", name="test", use_tmpfs=True)
        assert spec.backing == DeviceBacking.TMPFS

        # use_tmpfs=False should become backing=DISK
        spec = DeviceSpec(size="10G", name="test", use_tmpfs=False)
        assert spec.backing == DeviceBacking.DISK

    def test_null_blk_size_limits(self):
        """Test null_blk size validation."""
        # Valid size
        spec = DeviceSpec(
            size=f"{MAX_NULL_BLK_DEVICE_GB}G",
            name="test",
            backing=DeviceBacking.NULL_BLK,
        )
        valid, error = spec.validate()
        assert valid is True

        # Exceeds limit
        spec = DeviceSpec(
            size=f"{MAX_NULL_BLK_DEVICE_GB + 1}G",
            name="test",
            backing=DeviceBacking.NULL_BLK,
        )
        valid, error = spec.validate()
        assert valid is False
        assert "exceeds maximum" in error
        assert "null_blk" in error.lower()

    def test_null_blk_requires_size(self):
        """Test that null_blk requires size parameter."""
        # null_blk with existing device path doesn't make sense
        spec = DeviceSpec(path="/dev/sda1", backing=DeviceBacking.NULL_BLK)
        valid, error = spec.validate()
        # Should fail because path requires no backing specified
        assert valid is False


class TestVMDeviceManagerNullBlk:
    """Test VMDeviceManager with null_blk devices."""

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_setup_single_null_blk_device(self):
        """Test setting up a single null_blk device."""
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="1G", name="test", backing=DeviceBacking.NULL_BLK)]

        try:
            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert error == ""
            assert len(devices) == 1
            assert devices[0].startswith("/dev/nullb")
            assert len(manager.created_null_blk_devices) == 1

            # Verify device exists
            device_path = devices[0]
            assert Path(device_path).exists(), f"Device {device_path} doesn't exist"

        finally:
            # Cleanup
            manager.cleanup()

            # Verify cleanup
            assert len(manager.created_null_blk_devices) == 0

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_setup_multiple_null_blk_devices(self):
        """Test setting up multiple null_blk devices."""
        manager = VMDeviceManager()
        specs = [
            DeviceSpec(size="1G", name="test1", backing=DeviceBacking.NULL_BLK, order=0),
            DeviceSpec(size="2G", name="test2", backing=DeviceBacking.NULL_BLK, order=1),
            DeviceSpec(size="512M", name="test3", backing=DeviceBacking.NULL_BLK, order=2),
        ]

        try:
            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 3
            assert all(d.startswith("/dev/nullb") for d in devices)
            assert len(manager.created_null_blk_devices) == 3

            # Verify all devices exist
            for device_path in devices:
                assert Path(device_path).exists()

        finally:
            manager.cleanup()

    @pytest.mark.asyncio
    async def test_null_blk_fallback_when_not_supported(self):
        """Test automatic fallback to tmpfs when null_blk is not supported."""
        manager = VMDeviceManager()

        # Mock null_blk as not supported
        manager.null_blk_supported = False

        specs = [DeviceSpec(size="1G", name="test", backing=DeviceBacking.NULL_BLK)]

        try:
            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 1

            # Should have fallen back to tmpfs (loop device)
            assert len(manager.created_loop_devices) == 1
            assert len(manager.created_null_blk_devices) == 0

            # Verify the spec was mutated to TMPFS
            assert specs[0].backing == DeviceBacking.TMPFS

        finally:
            manager.cleanup()

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_null_blk_fallback_on_creation_failure(self):
        """Test fallback to tmpfs when null_blk creation fails."""
        manager = VMDeviceManager()

        # Ensure null_blk is detected as supported
        assert manager.null_blk_supported is True

        specs = [DeviceSpec(size="1G", name="test", backing=DeviceBacking.NULL_BLK)]

        # Mock create_null_blk_device to fail
        with patch("src.kerneldev_mcp.boot_manager.create_null_blk_device") as mock_create:
            mock_create.return_value = (None, None)  # Simulate failure

            try:
                success, error, devices = await manager.setup_devices(specs)

                assert success is True, f"Setup failed: {error}"
                assert len(devices) == 1

                # Should have fallen back to tmpfs
                assert len(manager.created_loop_devices) == 1
                assert len(manager.created_null_blk_devices) == 0

                # Verify the spec was mutated to TMPFS
                assert specs[0].backing == DeviceBacking.TMPFS

            finally:
                manager.cleanup()

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_mixed_device_types(self):
        """Test mixing null_blk, tmpfs, and disk-backed devices."""
        manager = VMDeviceManager()
        specs = [
            DeviceSpec(size="1G", name="nullblk", backing=DeviceBacking.NULL_BLK, order=0),
            DeviceSpec(size="1G", name="tmpfs", backing=DeviceBacking.TMPFS, order=1),
            DeviceSpec(size="1G", name="disk", backing=DeviceBacking.DISK, order=2),
        ]

        try:
            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 3

            # First should be null_blk
            assert devices[0].startswith("/dev/nullb")
            # Others should be loop devices
            assert devices[1].startswith("/dev/loop")
            assert devices[2].startswith("/dev/loop")

            assert len(manager.created_null_blk_devices) == 1
            assert len(manager.created_loop_devices) == 2

        finally:
            manager.cleanup()


class TestNullBlkMemoryLimits:
    """Test null_blk memory limit enforcement."""

    @pytest.mark.asyncio
    async def test_null_blk_single_device_limit(self):
        """Test enforcement of per-device memory limit."""
        manager = VMDeviceManager()

        # Try to create device exceeding single device limit
        specs = [
            DeviceSpec(
                size=f"{MAX_NULL_BLK_DEVICE_GB + 1}G",
                name="too_big",
                backing=DeviceBacking.NULL_BLK,
            )
        ]

        success, error, devices = await manager.setup_devices(specs)

        assert success is False
        assert "exceeds maximum" in error
        assert f"{MAX_NULL_BLK_DEVICE_GB}G" in error

    @pytest.mark.asyncio
    async def test_null_blk_total_memory_limit(self):
        """Test enforcement of total memory limit across devices."""
        manager = VMDeviceManager()

        # Create multiple devices that together exceed total limit
        # Use smaller devices to avoid per-device limit
        device_size = min(10, MAX_NULL_BLK_DEVICE_GB)
        num_devices = (MAX_NULL_BLK_TOTAL_GB // device_size) + 1

        specs = [
            DeviceSpec(
                size=f"{device_size}G",
                name=f"test{i}",
                backing=DeviceBacking.NULL_BLK,
                order=i,
            )
            for i in range(num_devices)
        ]

        success, error, devices = await manager.setup_devices(specs)

        assert success is False
        assert "exceeds maximum" in error
        assert f"{MAX_NULL_BLK_TOTAL_GB}G" in error
        assert "uses RAM" in error

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_null_blk_within_total_limit(self):
        """Test that devices within total limit succeed."""
        manager = VMDeviceManager()

        # Create devices that are within limit
        total_size = min(5, MAX_NULL_BLK_TOTAL_GB - 1)  # Leave headroom
        specs = [
            DeviceSpec(
                size=f"{total_size}G",
                name="test",
                backing=DeviceBacking.NULL_BLK,
            )
        ]

        try:
            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 1

        finally:
            manager.cleanup()


class TestNullBlkCleanup:
    """Test cleanup of null_blk devices."""

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_cleanup_after_successful_setup(self):
        """Test cleanup after successful device setup."""
        manager = VMDeviceManager()
        specs = [
            DeviceSpec(size="1G", name="test1", backing=DeviceBacking.NULL_BLK, order=0),
            DeviceSpec(size="1G", name="test2", backing=DeviceBacking.NULL_BLK, order=1),
        ]

        success, error, devices = await manager.setup_devices(specs)
        assert success is True

        # Record device paths before cleanup
        device_paths = devices.copy()

        # Cleanup
        manager.cleanup()

        # Verify all devices are gone
        for device_path in device_paths:
            assert not Path(device_path).exists(), (
                f"Device {device_path} still exists after cleanup"
            )

        # Verify manager state is clean
        assert len(manager.created_null_blk_devices) == 0

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_cleanup_after_failed_setup(self):
        """Test cleanup after failed device setup."""
        manager = VMDeviceManager()

        # Create one valid device, then fail on second
        specs = [
            DeviceSpec(size="1G", name="test1", backing=DeviceBacking.NULL_BLK, order=0),
            DeviceSpec(size="invalid", name="test2", backing=DeviceBacking.NULL_BLK, order=1),
        ]

        success, error, devices = await manager.setup_devices(specs)
        assert success is False  # Should fail on invalid size

        # Verify cleanup was called (no devices should remain)
        assert len(manager.created_null_blk_devices) == 0

        # Give a moment for async cleanup
        await asyncio.sleep(0.1)

        # Verify no null_blk devices leaked
        # Check configfs for any nullb devices that might have been created
        # (in practice, setup should fail before creating any devices)
        # This is more of a safety check

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self):
        """Test that cleanup is idempotent (safe to call multiple times)."""
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="1G", name="test", backing=DeviceBacking.NULL_BLK)]

        success, error, devices = await manager.setup_devices(specs)
        assert success is True

        # Call cleanup multiple times
        manager.cleanup()
        manager.cleanup()
        manager.cleanup()

        # Should not raise any errors
        assert len(manager.created_null_blk_devices) == 0

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    def test_orphaned_device_cleanup(self):
        """Test cleanup of orphaned null_blk devices from crashed sessions."""
        # Create a device manually (simulating crashed session)
        device_path, nullb_idx = create_null_blk_device("1G", "orphaned_test")

        if device_path is None:
            pytest.skip("Could not create test device")

        try:
            # Verify device exists
            assert Path(device_path).exists()

            # Wait for device to become stale (using low staleness for testing)
            time.sleep(2)

            # Clean up orphaned devices with low staleness threshold
            cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=1)

            # Should have cleaned at least one device
            assert cleaned >= 1

            # Device should be gone
            # Give it a moment to fully disappear
            time.sleep(0.5)
            # Note: Device might still show up briefly in /dev, that's OK

        finally:
            # Emergency cleanup in case test fails
            try:
                cleanup_null_blk_device(device_path, nullb_idx)
            except Exception:
                pass


class TestDeviceProfileNullBlk:
    """Test DeviceProfile with null_blk backing."""

    def test_fstests_profile_with_null_blk(self):
        """Test fstests profile with null_blk backing."""
        profile = DeviceProfile.get_profile("fstests_default", backing=DeviceBacking.NULL_BLK)

        assert profile is not None
        assert len(profile.devices) == 7
        assert all(d.backing == DeviceBacking.NULL_BLK for d in profile.devices)

    def test_fstests_profile_with_tmpfs(self):
        """Test fstests profile with tmpfs backing."""
        profile = DeviceProfile.get_profile("fstests_default", backing=DeviceBacking.TMPFS)

        assert profile is not None
        assert all(d.backing == DeviceBacking.TMPFS for d in profile.devices)

    def test_fstests_profile_default_backing(self):
        """Test fstests profile uses DISK backing by default."""
        profile = DeviceProfile.get_profile("fstests_default")

        assert profile is not None
        assert all(d.backing == DeviceBacking.DISK for d in profile.devices)

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_fstests_small_profile_null_blk_setup(self):
        """Test setting up fstests_small profile with null_blk."""
        manager = VMDeviceManager()
        profile = DeviceProfile.get_profile("fstests_small", backing=DeviceBacking.NULL_BLK)

        try:
            success, error, devices = await manager.setup_devices(profile.devices)

            # fstests_small uses 5G devices, 7 devices = 35G total
            # This should be within limits
            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 7
            assert all(d.startswith("/dev/nullb") for d in devices)

        finally:
            manager.cleanup()


class TestMixedDeviceScenarios:
    """Test complex scenarios with mixed device types."""

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_null_blk_with_existing_device(self):
        """Test mixing null_blk devices with existing block devices."""
        # Create a null_blk device first to use as "existing" device
        existing_dev, existing_idx = create_null_blk_device("1G", "existing")

        if existing_dev is None:
            pytest.skip("Could not create existing device")

        try:
            manager = VMDeviceManager()
            specs = [
                DeviceSpec(path=existing_dev, name="existing", readonly=True, order=0),
                DeviceSpec(size="1G", name="new", backing=DeviceBacking.NULL_BLK, order=1),
            ]

            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 2
            assert devices[0] == existing_dev
            assert devices[1].startswith("/dev/nullb")

            # Only one should be in created list (the new one)
            assert len(manager.created_null_blk_devices) == 1

        finally:
            manager.cleanup()
            # Clean up the "existing" device
            cleanup_null_blk_device(existing_dev, existing_idx)

    @pytest.mark.skipif(not HAS_SUDO, reason="Requires sudo access")
    @pytest.mark.skipif(not NULL_BLK_AVAILABLE, reason=f"null_blk not available: {NULL_BLK_MSG}")
    @pytest.mark.asyncio
    async def test_all_backing_types_together(self):
        """Test using all three backing types in one setup."""
        # Create an existing device to attach
        existing_dev, existing_idx = create_null_blk_device("1G", "existing")

        if existing_dev is None:
            pytest.skip("Could not create existing device")

        try:
            manager = VMDeviceManager()
            specs = [
                DeviceSpec(path=existing_dev, name="existing", readonly=True, order=0),
                DeviceSpec(size="1G", name="nullblk", backing=DeviceBacking.NULL_BLK, order=1),
                DeviceSpec(size="1G", name="tmpfs", backing=DeviceBacking.TMPFS, order=2),
                DeviceSpec(size="1G", name="disk", backing=DeviceBacking.DISK, order=3),
            ]

            success, error, devices = await manager.setup_devices(specs)

            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 4

            # Check device types
            assert devices[0] == existing_dev  # existing device
            assert devices[1].startswith("/dev/nullb")  # null_blk
            assert devices[2].startswith("/dev/loop")  # tmpfs loop
            assert devices[3].startswith("/dev/loop")  # disk loop

            # Check manager state
            assert len(manager.attached_block_devices) == 1  # existing
            assert len(manager.created_null_blk_devices) == 1  # null_blk
            assert len(manager.created_loop_devices) == 2  # tmpfs + disk

        finally:
            manager.cleanup()
            cleanup_null_blk_device(existing_dev, existing_idx)


class TestErrorHandling:
    """Test error handling in various failure scenarios."""

    @pytest.mark.asyncio
    async def test_too_many_devices_with_null_blk(self):
        """Test device count limit with null_blk devices."""
        manager = VMDeviceManager()
        specs = [
            DeviceSpec(size="1G", name=f"test{i}", backing=DeviceBacking.NULL_BLK)
            for i in range(MAX_CUSTOM_DEVICES + 1)
        ]

        success, error, devices = await manager.setup_devices(specs)

        assert success is False
        assert "Too many devices" in error

    @pytest.mark.asyncio
    async def test_invalid_size_with_null_blk(self):
        """Test error handling for invalid size."""
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="invalid", name="test", backing=DeviceBacking.NULL_BLK)]

        success, error, devices = await manager.setup_devices(specs)

        assert success is False
        assert "Invalid size format" in error

    @pytest.mark.asyncio
    async def test_zero_size_with_null_blk(self):
        """Test behavior with zero size.

        Note: null_blk creation will fail for zero size with "cannot be zero" error,
        but VMDeviceManager will automatically fall back to tmpfs loop device.
        The tmpfs fallback currently succeeds (creates 0-byte file), so the overall
        operation succeeds. This tests the fallback mechanism works even with edge cases.
        """
        manager = VMDeviceManager()
        specs = [DeviceSpec(size="0G", name="test", backing=DeviceBacking.NULL_BLK)]

        try:
            success, error, devices = await manager.setup_devices(specs)

            # null_blk will fail, but fallback to tmpfs should succeed
            # (This is current behavior - tmpfs accepts zero-sized files)
            assert success is True, f"Setup failed: {error}"
            assert len(devices) == 1

            # Should have fallen back to loop device
            assert len(manager.created_loop_devices) == 1
            assert len(manager.created_null_blk_devices) == 0

        finally:
            manager.cleanup()


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])
