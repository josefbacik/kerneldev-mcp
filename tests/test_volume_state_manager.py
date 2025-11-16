"""
Tests for VolumeStateManager - PID tracking and state management.
"""

import os
import json
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from kerneldev_mcp.device_pool import VolumeStateManager, VolumeAllocation, VolumeConfig


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create temporary state directory."""
    return tmp_path / "test-state"


@pytest.fixture
def state_manager(temp_state_dir):
    """Create VolumeStateManager with temp directory."""
    return VolumeStateManager(state_dir=temp_state_dir)


@pytest.fixture
def sample_allocation():
    """Create sample VolumeAllocation."""
    vol_spec = VolumeConfig(
        name="test", size="10G", path="/dev/test-vg/kdev-123-test", order=0, env_var="TEST_DEV"
    )

    return VolumeAllocation(
        lv_path="/dev/test-vg/kdev-20251115103045-a3f9d2-test",
        lv_name="kdev-20251115103045-a3f9d2-test",
        pool_name="default",
        vg_name="test-vg",
        volume_spec=vol_spec,
        pid=os.getpid(),
        allocated_at=datetime.now().isoformat(),
        session_id="session-a3f9d2",
    )


class TestVolumeStateManager:
    """Test VolumeStateManager functionality."""

    def test_init_creates_state_dir(self, temp_state_dir):
        """Test state manager creates state directory."""
        manager = VolumeStateManager(state_dir=temp_state_dir)

        assert temp_state_dir.exists()
        assert manager.state_file == temp_state_dir / "lv-state.json"

    def test_load_state_empty(self, state_manager):
        """Test loading state when file doesn't exist."""
        state = state_manager._load_state()

        assert state == {"allocations": []}

    def test_register_allocation(self, state_manager, sample_allocation):
        """Test registering an allocation."""
        state_manager.register_allocation(sample_allocation)

        # Load state and verify
        state = state_manager._load_state()
        assert len(state["allocations"]) == 1

        alloc = state["allocations"][0]
        assert alloc["lv_name"] == sample_allocation.lv_name
        assert alloc["pid"] == sample_allocation.pid
        assert alloc["session_id"] == sample_allocation.session_id

    def test_register_multiple_allocations(self, state_manager, sample_allocation):
        """Test registering multiple allocations."""
        # Register first
        state_manager.register_allocation(sample_allocation)

        # Create second allocation
        vol_spec2 = VolumeConfig(
            name="pool1", size="10G", path="/dev/test-vg/kdev-123-pool1", order=1
        )
        alloc2 = VolumeAllocation(
            lv_path="/dev/test-vg/kdev-20251115103045-a3f9d2-pool1",
            lv_name="kdev-20251115103045-a3f9d2-pool1",
            pool_name="default",
            vg_name="test-vg",
            volume_spec=vol_spec2,
            pid=os.getpid(),
            allocated_at=datetime.now().isoformat(),
            session_id="session-a3f9d2",
        )

        state_manager.register_allocation(alloc2)

        # Load state and verify
        state = state_manager._load_state()
        assert len(state["allocations"]) == 2

    def test_unregister_allocation(self, state_manager, sample_allocation):
        """Test unregistering an allocation."""
        # Register first
        state_manager.register_allocation(sample_allocation)

        # Verify it's there
        state = state_manager._load_state()
        assert len(state["allocations"]) == 1

        # Unregister
        state_manager.unregister_allocation(sample_allocation.lv_name)

        # Verify it's gone
        state = state_manager._load_state()
        assert len(state["allocations"]) == 0

    def test_get_allocations_for_session(self, state_manager, sample_allocation):
        """Test getting allocations for a specific session."""
        # Register allocation for session-a3f9d2
        state_manager.register_allocation(sample_allocation)

        # Create allocation for different session
        vol_spec2 = VolumeConfig(
            name="test", size="10G", path="/dev/test-vg/kdev-456-test", order=0
        )
        alloc2 = VolumeAllocation(
            lv_path="/dev/test-vg/kdev-20251115104523-b7e4c1-test",
            lv_name="kdev-20251115104523-b7e4c1-test",
            pool_name="default",
            vg_name="test-vg",
            volume_spec=vol_spec2,
            pid=os.getpid(),
            allocated_at=datetime.now().isoformat(),
            session_id="session-b7e4c1",  # Different session
        )
        state_manager.register_allocation(alloc2)

        # Get allocations for first session
        session_allocs = state_manager.get_allocations_for_session("session-a3f9d2")

        assert len(session_allocs) == 1
        assert session_allocs[0]["session_id"] == "session-a3f9d2"

        # Get allocations for second session
        session_allocs = state_manager.get_allocations_for_session("session-b7e4c1")

        assert len(session_allocs) == 1
        assert session_allocs[0]["session_id"] == "session-b7e4c1"

    def test_is_process_alive_current(self, state_manager):
        """Test checking if current process is alive."""
        assert state_manager._is_process_alive(os.getpid()) is True

    def test_is_process_alive_dead(self, state_manager):
        """Test checking if non-existent process is dead."""
        # PID 99999 should not exist
        assert state_manager._is_process_alive(99999) is False

    @patch("subprocess.run")
    def test_cleanup_orphaned_volumes_alive_process(
        self, mock_run, state_manager, sample_allocation
    ):
        """Test cleanup doesn't remove LVs from alive processes."""
        # Register allocation
        state_manager.register_allocation(sample_allocation)

        # Cleanup (should not remove because PID is alive)
        cleaned = state_manager.cleanup_orphaned_volumes("default")

        assert len(cleaned) == 0

        # Verify allocation still in state
        state = state_manager._load_state()
        assert len(state["allocations"]) == 1

        # Verify lvremove was NOT called
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(VolumeStateManager, "_is_process_alive")
    def test_cleanup_orphaned_volumes_dead_process(
        self, mock_alive, mock_run, state_manager, sample_allocation
    ):
        """Test cleanup removes LVs from dead processes."""
        # Register allocation
        state_manager.register_allocation(sample_allocation)

        # Mock process as dead
        mock_alive.return_value = False

        # Mock lvremove success
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Cleanup
        cleaned = state_manager.cleanup_orphaned_volumes("default")

        assert len(cleaned) == 1
        assert cleaned[0] == sample_allocation.lv_name

        # Verify allocation removed from state
        state = state_manager._load_state()
        assert len(state["allocations"]) == 0

        # Verify lvremove was called
        mock_run.assert_called_once()
        assert "lvremove" in str(mock_run.call_args)
        assert sample_allocation.lv_path in str(mock_run.call_args)

    @patch("subprocess.run")
    @patch.object(VolumeStateManager, "_is_process_alive")
    def test_cleanup_orphaned_volumes_wrong_pool(
        self, mock_alive, mock_run, state_manager, sample_allocation
    ):
        """Test cleanup only affects specified pool."""
        # Register allocation
        state_manager.register_allocation(sample_allocation)

        # Mock process as dead
        mock_alive.return_value = False

        # Cleanup different pool
        cleaned = state_manager.cleanup_orphaned_volumes("other-pool")

        assert len(cleaned) == 0

        # Verify allocation still in state (not cleaned from different pool)
        state = state_manager._load_state()
        assert len(state["allocations"]) == 1

        # Verify lvremove was NOT called
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(VolumeStateManager, "_is_process_alive")
    def test_cleanup_orphaned_volumes_lvremove_fails(
        self, mock_alive, mock_run, state_manager, sample_allocation
    ):
        """Test cleanup handles lvremove failures gracefully."""
        # Register allocation
        state_manager.register_allocation(sample_allocation)

        # Mock process as dead
        mock_alive.return_value = False

        # Mock lvremove failure
        mock_result = Mock()
        mock_result.returncode = 1
        mock_run.side_effect = Exception("lvremove failed")

        # Cleanup
        cleaned = state_manager.cleanup_orphaned_volumes("default")

        # Should not crash, just report 0 cleaned
        assert len(cleaned) == 0

        # Verify allocation KEPT in state (because removal failed)
        state = state_manager._load_state()
        assert len(state["allocations"]) == 1

    def test_state_file_persistence(self, state_manager, sample_allocation):
        """Test state persists across manager instances."""
        # Register with first manager
        state_manager.register_allocation(sample_allocation)

        # Create new manager instance
        manager2 = VolumeStateManager(state_dir=state_manager.state_dir)

        # Load state from new manager
        state = manager2._load_state()
        assert len(state["allocations"]) == 1
        assert state["allocations"][0]["lv_name"] == sample_allocation.lv_name

    def test_atomic_save(self, state_manager, sample_allocation):
        """Test state saves are atomic (use temp file)."""
        state_manager.register_allocation(sample_allocation)

        # Verify no .tmp file left behind
        tmp_file = state_manager.state_file.with_suffix(".tmp")
        assert not tmp_file.exists()

        # Verify state file exists and is valid
        assert state_manager.state_file.exists()
        with open(state_manager.state_file) as f:
            data = json.load(f)
            assert "allocations" in data


class TestVolumeAllocation:
    """Test VolumeAllocation dataclass."""

    def test_volume_allocation_creation(self, sample_allocation):
        """Test creating VolumeAllocation."""
        assert sample_allocation.lv_name == "kdev-20251115103045-a3f9d2-test"
        assert sample_allocation.pool_name == "default"
        assert sample_allocation.session_id == "session-a3f9d2"
        assert sample_allocation.pid == os.getpid()
        assert sample_allocation.volume_spec.name == "test"

    def test_volume_allocation_has_all_fields(self, sample_allocation):
        """Test VolumeAllocation has all required fields."""
        required_fields = [
            "lv_path",
            "lv_name",
            "pool_name",
            "vg_name",
            "volume_spec",
            "pid",
            "allocated_at",
            "session_id",
        ]

        for field in required_fields:
            assert hasattr(sample_allocation, field)
            assert getattr(sample_allocation, field) is not None
