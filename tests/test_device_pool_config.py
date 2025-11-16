"""
Tests for ConfigManager and configuration data classes in device_pool module.

These tests verify configuration storage, serialization, and management.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from kerneldev_mcp.device_pool import ConfigManager, PoolConfig, VolumeConfig, LVMPoolConfig


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create temporary config directory."""
    return tmp_path / "test-config"


@pytest.fixture
def config_manager(temp_config_dir):
    """Create ConfigManager with temporary directory."""
    return ConfigManager(config_dir=temp_config_dir)


@pytest.fixture
def sample_pool_config():
    """Create sample PoolConfig for testing."""
    lvm_config = LVMPoolConfig(
        pv="/dev/sdb", vg_name="test-vg", lv_prefix="kdev", thin_provisioning=False
    )

    return PoolConfig(
        pool_name="test-pool",
        device="/dev/sdb",
        created_at=datetime.now().isoformat(),
        created_by="testuser",
        lvm_config=lvm_config,
    )


class TestVolumeConfig:
    """Test VolumeConfig dataclass."""

    def test_volume_config_creation(self):
        """Test creating VolumeConfig."""
        vol = VolumeConfig(name="test", size="10G", path="/dev/vg/lv", order=0, env_var="TEST_DEV")

        assert vol.name == "test"
        assert vol.size == "10G"
        assert vol.path == "/dev/vg/lv"
        assert vol.order == 0
        assert vol.env_var == "TEST_DEV"

    def test_volume_config_defaults(self):
        """Test VolumeConfig default values."""
        vol = VolumeConfig(name="test", size="10G", path="/dev/vg/lv")

        assert vol.order == 0
        assert vol.env_var is None
        assert vol.partition_number is None


class TestPoolConfig:
    """Test PoolConfig dataclass and serialization."""

    def test_pool_config_creation(self, sample_pool_config):
        """Test creating PoolConfig."""
        pool = sample_pool_config

        assert pool.pool_name == "test-pool"
        assert pool.device == "/dev/sdb"
        assert pool.created_by == "testuser"
        assert pool.lvm_config is not None
        assert pool.lvm_config.vg_name == "test-vg"

    def test_pool_config_to_dict(self, sample_pool_config):
        """Test PoolConfig serialization to dict."""
        pool = sample_pool_config
        data = pool.to_dict()

        assert isinstance(data, dict)
        assert data["pool_name"] == "test-pool"
        assert data["device"] == "/dev/sdb"
        assert data["lvm_config"]["vg_name"] == "test-vg"
        assert data["lvm_config"]["lv_prefix"] == "kdev"

    def test_pool_config_from_dict(self, sample_pool_config):
        """Test PoolConfig deserialization from dict."""
        original = sample_pool_config
        data = original.to_dict()

        # Round-trip: dict -> PoolConfig
        restored = PoolConfig.from_dict(data)

        assert restored.pool_name == original.pool_name
        assert restored.device == original.device
        assert restored.created_by == original.created_by
        assert restored.lvm_config.vg_name == original.lvm_config.vg_name
        assert restored.lvm_config.lv_prefix == original.lvm_config.lv_prefix


class TestConfigManager:
    """Test ConfigManager functionality."""

    def test_config_manager_init(self, temp_config_dir):
        """Test ConfigManager initialization."""
        manager = ConfigManager(config_dir=temp_config_dir)

        assert manager.config_dir == temp_config_dir
        assert manager.config_file == temp_config_dir / "device-pool.json"
        assert temp_config_dir.exists()

    def test_config_manager_default_dir(self):
        """Test ConfigManager with default directory."""
        manager = ConfigManager()

        expected_dir = Path.home() / ".kerneldev-mcp"
        assert manager.config_dir == expected_dir

    def test_load_pools_no_config(self, config_manager):
        """Test loading pools when config file doesn't exist."""
        pools = config_manager.load_pools()

        assert pools == {}

    def test_save_and_load_pool(self, config_manager, sample_pool_config):
        """Test saving and loading a pool."""
        pool = sample_pool_config

        # Save pool
        config_manager.save_pool(pool)

        # Verify file exists
        assert config_manager.config_file.exists()

        # Load pools
        loaded_pools = config_manager.load_pools()

        assert len(loaded_pools) == 1
        assert "test-pool" in loaded_pools

        loaded_pool = loaded_pools["test-pool"]
        assert loaded_pool.pool_name == pool.pool_name
        assert loaded_pool.device == pool.device
        assert loaded_pool.lvm_config.vg_name == pool.lvm_config.vg_name
        assert loaded_pool.lvm_config.lv_prefix == pool.lvm_config.lv_prefix

    def test_save_multiple_pools(self, config_manager, sample_pool_config):
        """Test saving multiple pools."""
        pool1 = sample_pool_config

        pool2 = PoolConfig(
            pool_name="pool2",
            device="/dev/sdc",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
        )

        # Save both pools
        config_manager.save_pool(pool1)
        config_manager.save_pool(pool2)

        # Load pools
        loaded_pools = config_manager.load_pools()

        assert len(loaded_pools) == 2
        assert "test-pool" in loaded_pools
        assert "pool2" in loaded_pools

    def test_save_pool_updates_existing(self, config_manager, sample_pool_config):
        """Test saving a pool updates existing configuration."""
        pool = sample_pool_config

        # Save initial version
        config_manager.save_pool(pool)

        # Modify and save again
        pool.created_by = "newuser"
        config_manager.save_pool(pool)

        # Load and verify update
        loaded_pools = config_manager.load_pools()
        loaded_pool = loaded_pools["test-pool"]

        assert loaded_pool.created_by == "newuser"

    def test_get_pool_exists(self, config_manager, sample_pool_config):
        """Test getting a specific pool that exists."""
        config_manager.save_pool(sample_pool_config)

        pool = config_manager.get_pool("test-pool")

        assert pool is not None
        assert pool.pool_name == "test-pool"

    def test_get_pool_not_exists(self, config_manager):
        """Test getting a pool that doesn't exist."""
        pool = config_manager.get_pool("nonexistent")

        assert pool is None

    def test_delete_pool_exists(self, config_manager, sample_pool_config):
        """Test deleting a pool that exists."""
        config_manager.save_pool(sample_pool_config)

        # Verify pool exists
        assert config_manager.get_pool("test-pool") is not None

        # Delete pool
        result = config_manager.delete_pool("test-pool")

        assert result is True
        assert config_manager.get_pool("test-pool") is None

    def test_delete_pool_not_exists(self, config_manager):
        """Test deleting a pool that doesn't exist."""
        result = config_manager.delete_pool("nonexistent")

        assert result is False

    def test_delete_pool_preserves_others(self, config_manager, sample_pool_config):
        """Test deleting one pool preserves others."""
        pool1 = sample_pool_config

        pool2 = PoolConfig(
            pool_name="pool2",
            device="/dev/sdc",
            created_at=datetime.now().isoformat(),
            created_by="testuser",
        )

        # Save both pools
        config_manager.save_pool(pool1)
        config_manager.save_pool(pool2)

        # Delete pool1
        config_manager.delete_pool("test-pool")

        # Verify pool2 still exists
        loaded_pools = config_manager.load_pools()
        assert len(loaded_pools) == 1
        assert "pool2" in loaded_pools

    def test_config_file_format(self, config_manager, sample_pool_config):
        """Test config file has correct format."""
        config_manager.save_pool(sample_pool_config)

        # Read raw file
        with open(config_manager.config_file, "r") as f:
            data = json.load(f)

        assert "version" in data
        assert data["version"] == "1.0"
        assert "pools" in data
        assert isinstance(data["pools"], dict)
        assert "test-pool" in data["pools"]

    def test_atomic_save(self, config_manager, sample_pool_config):
        """Test save operation is atomic (uses temporary file)."""
        # This test verifies the code path, actual atomicity is OS-dependent
        config_manager.save_pool(sample_pool_config)

        # Verify no .tmp file left behind
        tmp_file = config_manager.config_file.with_suffix(".tmp")
        assert not tmp_file.exists()

        # Verify config file exists and is valid
        assert config_manager.config_file.exists()
        pools = config_manager.load_pools()
        assert len(pools) == 1


class TestLVMPoolConfig:
    """Test LVMPoolConfig dataclass."""

    def test_lvm_config_creation(self):
        """Test creating LVMPoolConfig."""
        lvm = LVMPoolConfig(
            pv="/dev/sdb", vg_name="test-vg", lv_prefix="kdev", thin_provisioning=True
        )

        assert lvm.pv == "/dev/sdb"
        assert lvm.vg_name == "test-vg"
        assert lvm.lv_prefix == "kdev"
        assert lvm.thin_provisioning is True

    def test_lvm_config_defaults(self):
        """Test LVMPoolConfig default values."""
        lvm = LVMPoolConfig(pv="/dev/sdb", vg_name="test-vg")

        assert lvm.lv_prefix == "kdev"
        assert lvm.thin_provisioning is False
