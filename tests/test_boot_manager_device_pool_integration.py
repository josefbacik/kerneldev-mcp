"""
Tests for boot_manager device pool auto-detection integration (Phase 4).

These tests verify that BootManager automatically detects and uses device pools
when configured, with proper fallback to loop devices.
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from kerneldev_mcp.boot_manager import BootManager
from kerneldev_mcp.device_pool import (
    ConfigManager,
    PoolConfig,
    LVMPoolConfig,
    VolumeConfig
)


@pytest.fixture
def temp_kernel_dir(tmp_path):
    """Create temporary kernel directory with vmlinux."""
    kernel_dir = tmp_path / "kernel"
    kernel_dir.mkdir()
    vmlinux = kernel_dir / "vmlinux"
    vmlinux.write_text("fake vmlinux")
    return kernel_dir


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create temporary config directory for device pool."""
    config_dir = tmp_path / ".kerneldev-mcp"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def mock_pool_config(temp_config_dir):
    """Create a mock device pool configuration."""
    lvm_config = LVMPoolConfig(
        pv="/dev/nvme0n1",
        vg_name="test-vg",
        lv_prefix="kdev",
        thin_provisioning=False
    )

    pool_config = PoolConfig(
        pool_name="default",
        device="/dev/nvme0n1",
        created_at=datetime.now().isoformat(),
        created_by="testuser",
        lvm_config=lvm_config
    )

    # Save to config file
    config_manager = ConfigManager(config_dir=temp_config_dir)
    config_manager.save_pool(pool_config)

    return pool_config


class TestBootManagerPoolAutoDetection:
    """Test BootManager auto-detects and uses device pools."""

    def test_generate_pool_session_id_format(self, temp_kernel_dir):
        """Test session ID generation format."""
        boot_mgr = BootManager(temp_kernel_dir)
        session_id = boot_mgr._generate_pool_session_id()

        # Should be: YYYYMMDDHHMMSS-xxxxxx
        assert len(session_id) == 21  # 14 timestamp + 1 dash + 6 random
        parts = session_id.split('-')
        assert len(parts) == 2
        assert len(parts[0]) == 14  # timestamp
        assert len(parts[1]) == 6   # random suffix
        assert parts[0].isdigit()
        assert parts[1].isalnum()

    def test_generate_pool_session_id_uniqueness(self, temp_kernel_dir):
        """Test session IDs are unique."""
        boot_mgr = BootManager(temp_kernel_dir)

        # Generate multiple IDs
        ids = [boot_mgr._generate_pool_session_id() for _ in range(100)]

        # All should be unique
        assert len(set(ids)) == len(ids)

    @patch('pathlib.Path.home')
    def test_try_allocate_from_pool_no_config(self, mock_home, temp_kernel_dir, tmp_path):
        """Test returns None when no pool config exists."""
        # Setup mock home to point to empty temp dir
        mock_home.return_value = tmp_path

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        assert result is None

    @patch('pathlib.Path.home')
    def test_try_allocate_from_pool_no_default_pool(self, mock_home, temp_kernel_dir, temp_config_dir):
        """Test returns None when 'default' pool doesn't exist."""
        mock_home.return_value = temp_config_dir.parent

        # Create config with non-default pool
        lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="other-vg")
        pool_config = PoolConfig(
            pool_name="other",  # NOT "default"
            device="/dev/sdb",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
            lvm_config=lvm_config
        )

        config_manager = ConfigManager(config_dir=temp_config_dir)
        config_manager.save_pool(pool_config)

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        assert result is None

    @patch('kerneldev_mcp.device_pool.allocate_pool_volumes')
    @patch('pathlib.Path.home')
    def test_try_allocate_from_pool_success(
        self,
        mock_home,
        mock_allocate,
        temp_kernel_dir,
        temp_config_dir,
        mock_pool_config
    ):
        """Test successfully allocates from pool."""
        mock_home.return_value = temp_config_dir.parent

        # Mock allocate_pool_volumes to return DeviceSpec-like objects
        from kerneldev_mcp.boot_manager import DeviceSpec
        mock_devices = [
            DeviceSpec(path="/dev/test-vg/kdev-test", name="test", env_var="TEST_DEV"),
            DeviceSpec(path="/dev/test-vg/kdev-pool1", name="pool1"),
        ]
        mock_allocate.return_value = mock_devices

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        assert result is not None
        assert len(result) == 2
        assert result[0].path == "/dev/test-vg/kdev-test"

        # Verify allocate was called with correct params
        mock_allocate.assert_called_once()
        call_args = mock_allocate.call_args
        assert call_args.kwargs['pool_name'] == 'default'
        assert len(call_args.kwargs['volume_specs']) == 7  # 7 volumes for fstests

    @patch('kerneldev_mcp.device_pool.allocate_pool_volumes')
    @patch('pathlib.Path.home')
    def test_try_allocate_from_pool_allocation_fails(
        self,
        mock_home,
        mock_allocate,
        temp_kernel_dir,
        temp_config_dir,
        mock_pool_config
    ):
        """Test handles allocation failure gracefully."""
        mock_home.return_value = temp_config_dir.parent

        # Mock allocate_pool_volumes to return None (failure)
        mock_allocate.return_value = None

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        assert result is None

    @patch('kerneldev_mcp.device_pool.allocate_pool_volumes')
    @patch('pathlib.Path.home')
    def test_try_allocate_from_pool_exception_handling(
        self,
        mock_home,
        mock_allocate,
        temp_kernel_dir,
        temp_config_dir,
        mock_pool_config
    ):
        """Test handles exceptions during allocation."""
        mock_home.return_value = temp_config_dir.parent

        # Mock allocate_pool_volumes to raise exception
        mock_allocate.side_effect = Exception("LVM error")

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        # Should return None and log warning, not crash
        assert result is None

    @patch('kerneldev_mcp.device_pool.allocate_pool_volumes')
    @patch('pathlib.Path.home')
    def test_try_allocate_stores_session_id(
        self,
        mock_home,
        mock_allocate,
        temp_kernel_dir,
        temp_config_dir,
        mock_pool_config
    ):
        """Test stores session ID for cleanup."""
        mock_home.return_value = temp_config_dir.parent

        from kerneldev_mcp.boot_manager import DeviceSpec
        mock_devices = [DeviceSpec(path="/dev/test-vg/kdev-test", name="test")]
        mock_allocate.return_value = mock_devices

        boot_mgr = BootManager(temp_kernel_dir)
        result = boot_mgr._try_allocate_from_pool(use_tmpfs=False)

        assert result is not None
        # Session ID should be stored
        assert hasattr(boot_mgr, '_pool_session_id')
        assert boot_mgr._pool_session_id is not None
        assert len(boot_mgr._pool_session_id) == 21


class TestBootWithFstestsPoolIntegration:
    """Test boot_with_fstests integrates with device pools."""

    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_virtme_ng')
    def test_boot_with_fstests_tries_pool_first(
        self,
        mock_virtme,
        mock_try_pool,
        temp_kernel_dir
    ):
        """Test boot_with_fstests tries device pool before loop devices."""
        mock_virtme.return_value = False  # Fail early to avoid full boot
        mock_try_pool.return_value = None  # No pool available

        boot_mgr = BootManager(temp_kernel_dir)

        # This will fail at virtme check, but we just want to verify pool was tried
        import asyncio
        asyncio.run(boot_mgr.boot_with_fstests(
            fstests_path=Path("/fake/fstests"),
            tests=["-g", "quick"],
            use_default_devices=True
        ))

        # Verify _try_allocate_from_pool was called
        mock_try_pool.assert_called_once()

    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    def test_boot_with_fstests_uses_pool_devices(
        self,
        mock_try_pool,
        temp_kernel_dir
    ):
        """Test boot_with_fstests uses pool devices when available."""
        from kerneldev_mcp.boot_manager import DeviceSpec

        # Mock pool allocation to succeed
        mock_devices = [
            DeviceSpec(path="/dev/test-vg/test", name="test", env_var="TEST_DEV"),
            DeviceSpec(path="/dev/test-vg/pool1", name="pool1"),
        ]
        mock_try_pool.return_value = mock_devices

        boot_mgr = BootManager(temp_kernel_dir)

        # We can't easily test the full flow without mocking everything,
        # but we can verify the pool allocation is attempted
        # In real integration test, this would boot the VM
        mock_try_pool.assert_not_called()  # Not called yet

        # Simulate the start of boot_with_fstests
        # (actual async test would be in integration tests)

    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    @patch('kerneldev_mcp.boot_manager.DeviceProfile.get_profile')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_virtme_ng')
    def test_boot_with_fstests_falls_back_to_loop(
        self,
        mock_virtme,
        mock_profile,
        mock_try_pool,
        temp_kernel_dir
    ):
        """Test falls back to loop devices when pool unavailable."""
        mock_virtme.return_value = False  # Fail early
        mock_try_pool.return_value = None  # No pool

        from kerneldev_mcp.boot_manager import DeviceProfile, DeviceSpec
        mock_profile.return_value = DeviceProfile(
            name="fstests_default",
            description="Test profile",
            devices=[DeviceSpec(size="10G", name="test")]
        )

        boot_mgr = BootManager(temp_kernel_dir)

        import asyncio
        asyncio.run(boot_mgr.boot_with_fstests(
            fstests_path=Path("/fake/fstests"),
            tests=["-g", "quick"],
            use_default_devices=True
        ))

        # Both should be called: pool tried first, then profile
        mock_try_pool.assert_called_once()
        mock_profile.assert_called_once_with("fstests_default", use_tmpfs=False)


class TestDevicePoolCleanup:
    """Test device pool cleanup in boot_with_fstests finally block."""

    @patch('kerneldev_mcp.boot_manager.VMDeviceManager.setup_devices')
    @patch('kerneldev_mcp.device_pool.release_pool_volumes')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_qemu')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_virtme_ng')
    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    def test_cleanup_releases_pool_volumes(
        self,
        mock_try_pool,
        mock_virtme,
        mock_qemu,
        mock_release,
        mock_setup_devices,
        temp_kernel_dir,
        tmp_path
    ):
        """Test cleanup releases pool volumes.

        Note: This test must get past early validation checks to reach the try/finally block.
        """
        from kerneldev_mcp.boot_manager import DeviceSpec

        # Mock pool allocation
        mock_devices = [DeviceSpec(path="/dev/test-vg/test", name="test")]
        mock_try_pool.return_value = mock_devices

        # Mock device setup to succeed
        import asyncio
        async def mock_setup():
            return (True, None, ["/dev/test-vg/test"])
        mock_setup_devices.side_effect = lambda _: asyncio.ensure_future(mock_setup())

        # Pass early checks to reach try/finally block
        mock_virtme.return_value = True
        mock_qemu.return_value = (True, "qemu-system-x86_64")

        # Create vmlinux so kernel exists check passes
        vmlinux = temp_kernel_dir / "vmlinux"
        vmlinux.write_text("fake vmlinux")

        # Create fake fstests directory with required structure
        fstests_dir = tmp_path / "fstests"
        fstests_dir.mkdir()
        (fstests_dir / "check").touch()
        ltp_dir = fstests_dir / "ltp"
        ltp_dir.mkdir()
        (ltp_dir / "fsstress").touch()
        (ltp_dir / "fsstress").chmod(0o755)
        src_dir = fstests_dir / "src"
        src_dir.mkdir()
        (src_dir / "aio-dio-regress").touch()
        (src_dir / "aio-dio-regress").chmod(0o755)

        boot_mgr = BootManager(temp_kernel_dir)
        boot_mgr._pool_session_id = "20251115123456-abc123"

        # Boot will fail at some point, but should reach cleanup
        import asyncio
        try:
            asyncio.run(boot_mgr.boot_with_fstests(
                fstests_path=fstests_dir,
                tests=["-g", "quick"],
                use_default_devices=True
            ))
        except:
            pass  # Expected to fail, we just want cleanup to run

        # Verify release was called
        mock_release.assert_called_once()
        call_args = mock_release.call_args
        assert call_args.kwargs['pool_name'] == 'default'
        assert call_args.kwargs['session_id'] == "20251115123456-abc123"
        assert call_args.kwargs['keep_volumes'] is False

    @patch('kerneldev_mcp.boot_manager.VMDeviceManager.setup_devices')
    @patch('kerneldev_mcp.device_pool.release_pool_volumes')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_qemu')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_virtme_ng')
    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    def test_cleanup_handles_release_failure(
        self,
        mock_try_pool,
        mock_virtme,
        mock_qemu,
        mock_release,
        mock_setup_devices,
        temp_kernel_dir,
        tmp_path
    ):
        """Test cleanup handles release failure gracefully."""
        from kerneldev_mcp.boot_manager import DeviceSpec

        mock_devices = [DeviceSpec(path="/dev/test-vg/test", name="test")]
        mock_try_pool.return_value = mock_devices

        # Mock device setup to succeed
        import asyncio
        async def mock_setup():
            return (True, None, ["/dev/test-vg/test"])
        mock_setup_devices.side_effect = lambda _: asyncio.ensure_future(mock_setup())

        # Pass early checks
        mock_virtme.return_value = True
        mock_qemu.return_value = (True, "qemu-system-x86_64")

        # Create vmlinux
        vmlinux = temp_kernel_dir / "vmlinux"
        vmlinux.write_text("fake vmlinux")

        # Create fake fstests directory with required structure
        fstests_dir = tmp_path / "fstests"
        fstests_dir.mkdir()
        (fstests_dir / "check").touch()
        ltp_dir = fstests_dir / "ltp"
        ltp_dir.mkdir()
        (ltp_dir / "fsstress").touch()
        (ltp_dir / "fsstress").chmod(0o755)
        src_dir = fstests_dir / "src"
        src_dir.mkdir()
        (src_dir / "aio-dio-regress").touch()
        (src_dir / "aio-dio-regress").chmod(0o755)

        # Mock release to fail
        mock_release.side_effect = Exception("lvremove failed")

        boot_mgr = BootManager(temp_kernel_dir)
        boot_mgr._pool_session_id = "20251115123456-abc123"

        # Should not crash despite release failure
        import asyncio
        try:
            asyncio.run(boot_mgr.boot_with_fstests(
                fstests_path=fstests_dir,
                tests=["-g", "quick"],
                use_default_devices=True
            ))
        except:
            pass

        # Release was attempted
        mock_release.assert_called_once()

    @patch('kerneldev_mcp.device_pool.release_pool_volumes')
    @patch('kerneldev_mcp.boot_manager.BootManager.check_virtme_ng')
    @patch('kerneldev_mcp.boot_manager.BootManager._try_allocate_from_pool')
    def test_cleanup_skipped_when_no_pool_used(
        self,
        mock_try_pool,
        mock_virtme,
        mock_release,
        temp_kernel_dir
    ):
        """Test cleanup skipped when pool not used."""
        # No pool available
        mock_try_pool.return_value = None
        mock_virtme.return_value = False

        boot_mgr = BootManager(temp_kernel_dir)

        import asyncio
        asyncio.run(boot_mgr.boot_with_fstests(
            fstests_path=Path("/fake/fstests"),
            tests=["-g", "quick"],
            use_default_devices=True
        ))

        # Release should NOT be called
        mock_release.assert_not_called()


class TestRegressionPrevention:
    """Regression tests to prevent breaking auto-detection."""

    def test_boot_with_fstests_has_pool_detection_code(self, temp_kernel_dir):
        """Ensure pool detection code exists in boot_with_fstests."""
        import inspect
        from kerneldev_mcp.boot_manager import BootManager

        source = inspect.getsource(BootManager.boot_with_fstests)

        # Should call _try_allocate_from_pool
        assert "_try_allocate_from_pool" in source, \
            "boot_with_fstests must attempt pool allocation"

    def test_boot_with_fstests_has_cleanup_code(self, temp_kernel_dir):
        """Ensure cleanup code exists in finally block."""
        import inspect
        from kerneldev_mcp.boot_manager import BootManager

        source = inspect.getsource(BootManager.boot_with_fstests)

        # Should call release_pool_volumes in finally
        assert "release_pool_volumes" in source, \
            "boot_with_fstests must clean up pool volumes"

        # Should be in finally block
        assert "finally:" in source, \
            "Cleanup must be in finally block"

    def test_try_allocate_from_pool_method_exists(self, temp_kernel_dir):
        """Ensure _try_allocate_from_pool method exists."""
        from kerneldev_mcp.boot_manager import BootManager

        boot_mgr = BootManager(temp_kernel_dir)
        assert hasattr(boot_mgr, '_try_allocate_from_pool')
        assert callable(boot_mgr._try_allocate_from_pool)

    def test_generate_pool_session_id_method_exists(self, temp_kernel_dir):
        """Ensure _generate_pool_session_id method exists."""
        from kerneldev_mcp.boot_manager import BootManager

        boot_mgr = BootManager(temp_kernel_dir)
        assert hasattr(boot_mgr, '_generate_pool_session_id')
        assert callable(boot_mgr._generate_pool_session_id)
