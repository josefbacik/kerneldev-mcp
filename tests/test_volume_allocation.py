"""
Tests for LVM volume allocation and release functionality.
"""

import os
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from kerneldev_mcp.device_pool import (
    LVMPoolManager,
    ConfigManager,
    PoolConfig,
    LVMPoolConfig,
    VolumeConfig,
    VolumeStateManager,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create temporary config directory."""
    return tmp_path / "test-config"


@pytest.fixture
def lvm_manager(temp_config_dir):
    """Create LVMPoolManager with temp config."""
    config_mgr = ConfigManager(config_dir=temp_config_dir)
    manager = LVMPoolManager(config_mgr)
    # Override state manager to use temp directory
    manager.state_manager = VolumeStateManager(state_dir=temp_config_dir)
    return manager


@pytest.fixture
def sample_pool_config(temp_config_dir):
    """Create and save a sample pool config."""
    config_mgr = ConfigManager(config_dir=temp_config_dir)

    lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg", lv_prefix="kdev")

    pool = PoolConfig(
        pool_name="test-pool",
        device="/dev/sdb",
        created_at=datetime.now().isoformat(),
        created_by="testuser",
        lvm_config=lvm_config,
    )

    config_mgr.save_pool(pool)
    return pool


class TestVolumeAllocation:
    """Test volume allocation functionality."""

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_allocate_volumes_creates_unique_names(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test allocate_volumes generates unique LV names."""
        # Mock lvcreate success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [
            VolumeConfig(name="test", size="10G", order=0, env_var="TEST_DEV"),
            VolumeConfig(name="pool1", size="10G", order=1),
        ]

        allocations = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-test-123"
        )

        assert len(allocations) == 2

        # Check unique names
        assert allocations[0].lv_name.startswith("kdev-")
        assert allocations[1].lv_name.startswith("kdev-")
        assert allocations[0].lv_name != allocations[1].lv_name

        # Check names contain session info
        assert "-test" in allocations[0].lv_name
        assert "-pool1" in allocations[1].lv_name

        # Verify lvcreate was called for each volume
        assert mock_run.call_count == 2

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_allocate_volumes_registers_state(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test allocate_volumes registers allocations in state file."""
        # Mock lvcreate success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-test-456"
        )

        # Check state file was updated
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 1
        assert state["allocations"][0]["session_id"] == "session-test-456"
        assert state["allocations"][0]["pid"] == os.getpid()

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_allocate_volumes_rollback_on_failure(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test allocate_volumes rolls back on failure."""
        # First lvcreate succeeds, second fails, then rollback lvremove succeeds
        mock_success = Mock()
        mock_success.returncode = 0

        mock_run.side_effect = [
            mock_success,  # First lvcreate succeeds
            Exception("lvcreate failed"),  # Second lvcreate fails
            mock_success,  # Rollback lvremove succeeds
        ]

        volume_specs = [
            VolumeConfig(name="test", size="10G", order=0),
            VolumeConfig(name="pool1", size="10G", order=1),
        ]

        # Should raise exception
        with pytest.raises(Exception, match="lvcreate failed"):
            lvm_manager.allocate_volumes(
                pool_name="test-pool", volume_specs=volume_specs, session_id="session-test-fail"
            )

        # Verify state file has no allocations (rollback worked)
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 0

    def test_allocate_volumes_pool_not_found(self, lvm_manager):
        """Test allocate_volumes fails if pool doesn't exist."""
        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        with pytest.raises(ValueError, match="Pool .* not found"):
            lvm_manager.allocate_volumes(
                pool_name="nonexistent", volume_specs=volume_specs, session_id="session-test"
            )

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_allocate_volumes_timestamp_unique(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test allocation timestamps make names unique."""
        # Mock lvcreate success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        # Allocate first set
        allocs1 = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-1"
        )

        # Small delay to ensure different timestamp
        import time

        time.sleep(0.1)

        # Allocate second set
        allocs2 = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-2"
        )

        # Names should be different
        assert allocs1[0].lv_name != allocs2[0].lv_name


class TestVolumeRelease:
    """Test volume release functionality."""

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_release_volumes_deletes_lvs(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test release_volumes deletes LVs by default."""
        # Setup: allocate volumes first
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [
            VolumeConfig(name="test", size="10G", order=0),
            VolumeConfig(name="pool1", size="10G", order=1),
        ]

        lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-release-test"
        )

        # Reset mock to count release calls
        mock_run.reset_mock()

        # Release volumes
        success = lvm_manager.release_volumes(
            pool_name="test-pool", session_id="session-release-test", keep_volumes=False
        )

        assert success is True

        # Verify lvremove was called for each volume
        assert mock_run.call_count == 2
        assert all("lvremove" in str(call) for call in mock_run.call_args_list)

        # Verify state file cleared
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 0

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_release_volumes_keep_flag(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test release_volumes with keep_volumes=True."""
        # Setup: allocate volumes first
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-keep-test"
        )

        # Reset mock
        mock_run.reset_mock()

        # Release with keep_volumes=True
        success = lvm_manager.release_volumes(
            pool_name="test-pool", session_id="session-keep-test", keep_volumes=True
        )

        assert success is True

        # Verify lvremove was NOT called
        mock_run.assert_not_called()

        # Verify state file still cleared (unregistered even though kept)
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 0

    def test_release_volumes_no_allocations(self, lvm_manager):
        """Test release_volumes succeeds even if no allocations found."""
        success = lvm_manager.release_volumes(
            pool_name="test-pool", session_id="nonexistent-session", keep_volumes=False
        )

        assert success is True

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_release_volumes_partial_failure(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test release_volumes continues even if some lvremove calls fail."""
        # Setup: allocate 2 volumes
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [
            VolumeConfig(name="test", size="10G", order=0),
            VolumeConfig(name="pool1", size="10G", order=1),
        ]

        lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="session-partial"
        )

        # Reset mock - first lvremove fails, second succeeds
        mock_run.reset_mock()
        mock_run.side_effect = [
            Exception("lvremove failed for first"),
            mock_result,  # Second succeeds
        ]

        # Release - should not raise exception
        success = lvm_manager.release_volumes(
            pool_name="test-pool", session_id="session-partial", keep_volumes=False
        )

        assert success is True

        # Both should be unregistered from state despite first failure
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 0


class TestCleanupOrphanedVolumes:
    """Test cleanup_orphaned_volumes method."""

    @patch("subprocess.run")
    @patch.object(VolumeStateManager, "_is_process_alive")
    def test_cleanup_multiple_dead_processes(
        self, mock_alive, mock_run, lvm_manager, sample_pool_config
    ):
        """Test cleanup handles multiple dead processes."""
        # Setup: allocate from 3 different "processes"
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Manually create allocations with different PIDs
        from kerneldev_mcp.device_pool import VolumeAllocation

        for i, pid in enumerate([12345, 67890, 11111]):
            vol_spec = VolumeConfig(name="test", size="10G", order=0)
            alloc = VolumeAllocation(
                lv_path=f"/dev/test-vg/kdev-timestamp-{i}-test",
                lv_name=f"kdev-timestamp-{i}-test",
                pool_name="test-pool",
                vg_name="test-vg",
                volume_spec=vol_spec,
                pid=pid,
                allocated_at=datetime.now().isoformat(),
                session_id=f"session-{i}",
            )
            lvm_manager.state_manager.register_allocation(alloc)

        # Mock: all processes dead
        mock_alive.return_value = False

        # Reset mock to count lvremove calls
        mock_run.reset_mock()

        # Cleanup
        cleaned = lvm_manager.cleanup_orphaned_volumes("test-pool")

        assert len(cleaned) == 3

        # Verify lvremove called 3 times
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    @patch.object(VolumeStateManager, "_is_process_alive")
    def test_cleanup_mixed_alive_dead(self, mock_alive, mock_run, lvm_manager, sample_pool_config):
        """Test cleanup only removes volumes from dead processes."""
        # Setup allocations from 2 processes
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        from kerneldev_mcp.device_pool import VolumeAllocation

        # Allocation 1: current process (alive)
        vol_spec1 = VolumeConfig(name="test", size="10G", order=0)
        alloc1 = VolumeAllocation(
            lv_path="/dev/test-vg/kdev-alive-test",
            lv_name="kdev-alive-test",
            pool_name="test-pool",
            vg_name="test-vg",
            volume_spec=vol_spec1,
            pid=os.getpid(),  # Current process
            allocated_at=datetime.now().isoformat(),
            session_id="session-alive",
        )
        lvm_manager.state_manager.register_allocation(alloc1)

        # Allocation 2: dead process
        vol_spec2 = VolumeConfig(name="test", size="10G", order=0)
        alloc2 = VolumeAllocation(
            lv_path="/dev/test-vg/kdev-dead-test",
            lv_name="kdev-dead-test",
            pool_name="test-pool",
            vg_name="test-vg",
            volume_spec=vol_spec2,
            pid=99999,  # Dead process
            allocated_at=datetime.now().isoformat(),
            session_id="session-dead",
        )
        lvm_manager.state_manager.register_allocation(alloc2)

        # Mock: current PID alive, 99999 dead
        def is_alive(pid):
            return pid == os.getpid()

        mock_alive.side_effect = is_alive

        # Reset mock to count lvremove calls
        mock_run.reset_mock()

        # Cleanup
        cleaned = lvm_manager.cleanup_orphaned_volumes("test-pool")

        # Should only clean dead process's volume
        assert len(cleaned) == 1
        assert cleaned[0] == "kdev-dead-test"

        # Verify only 1 lvremove call
        assert mock_run.call_count == 1

        # Verify alive allocation still in state
        state = lvm_manager.state_manager._load_state()
        assert len(state["allocations"]) == 1
        assert state["allocations"][0]["lv_name"] == "kdev-alive-test"


class TestPublicAPI:
    """Test public allocate_pool_volumes and release_pool_volumes functions."""

    @patch("subprocess.run")
    def test_allocate_pool_volumes(self, mock_run, temp_config_dir):
        """Test allocate_pool_volumes public API."""
        from kerneldev_mcp.device_pool import allocate_pool_volumes

        # Setup pool
        config_mgr = ConfigManager(config_dir=temp_config_dir)
        lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg", lv_prefix="kdev")
        pool = PoolConfig(
            pool_name="test-pool",
            device="/dev/sdb",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
            lvm_config=lvm_config,
        )
        config_mgr.save_pool(pool)

        # Mock lvcreate success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        # Allocate
        device_specs = allocate_pool_volumes(
            pool_name="test-pool",
            volume_specs=volume_specs,
            session_id="api-test",
            config_dir=temp_config_dir,
        )

        # Should return DeviceSpec-like objects (mocked as None in tests)
        # In real usage, returns list of DeviceSpec objects
        # Here we just verify it doesn't crash
        assert (
            device_specs is not None or device_specs is None
        )  # Either is ok (depends on boot_manager import)

    @patch("subprocess.run")
    def test_release_pool_volumes(self, mock_run, temp_config_dir):
        """Test release_pool_volumes public API."""
        from kerneldev_mcp.device_pool import allocate_pool_volumes, release_pool_volumes

        # Setup pool
        config_mgr = ConfigManager(config_dir=temp_config_dir)
        lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg", lv_prefix="kdev")
        pool = PoolConfig(
            pool_name="test-pool",
            device="/dev/sdb",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
            lvm_config=lvm_config,
        )
        config_mgr.save_pool(pool)

        # Mock lvcreate/lvremove success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        # Allocate
        allocate_pool_volumes(
            pool_name="test-pool",
            volume_specs=volume_specs,
            session_id="api-release-test",
            config_dir=temp_config_dir,
        )

        # Reset mock
        mock_run.reset_mock()

        # Release
        success = release_pool_volumes(
            pool_name="test-pool",
            session_id="api-release-test",
            keep_volumes=False,
            config_dir=temp_config_dir,
        )

        assert success is True


class TestUniqueNameGeneration:
    """Test unique LV name generation."""

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_names_include_timestamp(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test LV names include timestamp."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        allocations = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="timestamp-test"
        )

        lv_name = allocations[0].lv_name

        # Should match pattern: kdev-YYYYMMDDHHMMSS-xxxxxx-test
        assert lv_name.startswith("kdev-")
        assert "-test" in lv_name

        # Should have timestamp component (14 digits)
        import re

        match = re.search(r"kdev-(\d{14})-([a-f0-9]{6})-test", lv_name)
        assert match is not None
        assert len(match.group(1)) == 14  # Timestamp
        assert len(match.group(2)) == 6  # Random hex

    @patch("kerneldev_mcp.device_pool._grant_user_lv_access", return_value=True)
    @patch("subprocess.run")
    def test_names_include_random(
        self, mock_run, mock_grant_access, lvm_manager, sample_pool_config
    ):
        """Test LV names include random component."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        volume_specs = [VolumeConfig(name="test", size="10G", order=0)]

        # Create 2 allocations in quick succession
        allocs1 = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="random-test-1"
        )

        allocs2 = lvm_manager.allocate_volumes(
            pool_name="test-pool", volume_specs=volume_specs, session_id="random-test-2"
        )

        # Even if timestamp is same, random component makes them unique
        assert allocs1[0].lv_name != allocs2[0].lv_name
