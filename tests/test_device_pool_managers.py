"""
Tests for PartitionPoolManager and LVMPoolManager.

These are unit tests using mocks since actual device operations require
root privileges and physical devices.
"""

import pytest
from unittest.mock import Mock, patch

from kerneldev_mcp.device_pool import LVMPoolManager, ConfigManager, ValidationLevel


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create temporary config directory."""
    return tmp_path / "test-config"


@pytest.fixture
def lvm_manager(temp_config_dir):
    """Create LVMPoolManager with temp config."""
    config_mgr = ConfigManager(config_dir=temp_config_dir)
    return LVMPoolManager(config_mgr)


class TestLVMPoolManager:
    """Test LVMPoolManager functionality."""

    def test_lvm_manager_initialization(self, lvm_manager):
        """Test LVMPoolManager is initialized correctly."""
        from kerneldev_mcp.device_pool import VolumeStateManager

        assert isinstance(lvm_manager, LVMPoolManager)
        assert lvm_manager.config_manager is not None
        assert lvm_manager.safety_validator is not None
        assert lvm_manager.state_manager is not None
        assert isinstance(lvm_manager.state_manager, VolumeStateManager)


class TestPoolManagerValidation:
    """Test validation methods for LVM manager."""

    @patch.object(ConfigManager, "get_pool")
    def test_validate_pool_not_found(self, mock_get_pool, lvm_manager):
        """Test validation fails for nonexistent pool."""
        mock_get_pool.return_value = None

        result = lvm_manager.validate_pool("nonexistent")

        assert result.level == ValidationLevel.ERROR
        assert "not found" in result.message.lower()

    @patch("subprocess.run")
    @patch.object(ConfigManager, "get_pool")
    def test_validate_pool_vg_missing(self, mock_get_pool, mock_run, lvm_manager):
        """Test validation fails when VG doesn't exist."""
        from kerneldev_mcp.device_pool import PoolConfig, LVMPoolConfig
        from datetime import datetime

        lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg")

        pool = PoolConfig(
            pool_name="test-pool",
            device="/dev/sdb",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
            lvm_config=lvm_config,
        )

        mock_get_pool.return_value = pool

        # Mock vgs command to return error (VG doesn't exist)
        mock_result = Mock()
        mock_result.returncode = 5  # VG not found
        mock_run.return_value = mock_result

        result = lvm_manager.validate_pool("test-pool")

        assert result.level == ValidationLevel.ERROR
        assert "does not exist" in result.message.lower()

    @patch("subprocess.run")
    @patch.object(ConfigManager, "get_pool")
    def test_validate_pool_success(self, mock_get_pool, mock_run, lvm_manager):
        """Test validation succeeds when VG exists."""
        from kerneldev_mcp.device_pool import PoolConfig, LVMPoolConfig
        from datetime import datetime

        lvm_config = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg")

        pool = PoolConfig(
            pool_name="test-pool",
            device="/dev/sdb",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
            lvm_config=lvm_config,
        )

        mock_get_pool.return_value = pool

        # Mock vgs command to succeed
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = lvm_manager.validate_pool("test-pool")

        assert result.level == ValidationLevel.OK
        assert "healthy" in result.message.lower()
