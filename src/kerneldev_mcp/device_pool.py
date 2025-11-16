"""
Device Pool Management for kerneldev-mcp

Provides infrastructure for managing physical device pools (partitions or LVM)
to replace slow loop devices with fast physical storage for kernel testing.

Design: docs/implementation/device-pool-design.md
"""

import json
import logging
import os
import pwd
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum


# Configure logging
logger = logging.getLogger(__name__)


# Pool strategy is always LVM - provides flexibility (snapshots, resizing)
# while maintaining good performance (~5% overhead vs raw device)
# All LVM operations use sudo - no special permissions needed.
# VG name is persistent across reboots (LVM auto-discovers VGs).


class ValidationLevel(Enum):
    """Severity level for validation results."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ValidationResult:
    """Result from device safety validation."""

    level: ValidationLevel
    message: str
    details: Optional[Dict[str, Any]] = None

    @property
    def is_safe(self) -> bool:
        """Check if validation passed (OK or WARNING)."""
        return self.level in (ValidationLevel.OK, ValidationLevel.WARNING)

    @property
    def is_error(self) -> bool:
        """Check if validation failed with error."""
        return self.level == ValidationLevel.ERROR


@dataclass
class VolumeConfig:
    """Configuration for a single volume in the pool."""

    name: str  # Volume name (e.g., "test", "pool1")
    size: str  # Size string (e.g., "10G")
    path: Optional[str] = None  # Device path (optional - set after LV creation)
    order: int = 0  # Order for device attachment (0=first)
    env_var: Optional[str] = None  # Environment variable name (e.g., "TEST_DEV")
    partition_number: Optional[int] = None  # For partition strategy


@dataclass
class LVMPoolConfig:
    """Configuration specific to LVM-based pools."""

    pv: str  # Physical volume path
    vg_name: str  # Volume group name
    lv_prefix: str = "kdev"  # Prefix for logical volume names
    thin_provisioning: bool = False  # Enable thin provisioning


@dataclass
class PoolConfig:
    """
    Complete configuration for a device pool (always LVM-based).

    Note: Pool only contains VG metadata. LVs are created on-demand
    with unique names per Claude instance and auto-deleted after use.

    All LVM operations use sudo - no special permissions needed.
    VG name is persistent across reboots (LVM auto-discovers VGs).
    """

    pool_name: str
    device: str  # Physical device path (can be /dev/disk/by-id/ for persistence)
    created_at: str  # ISO timestamp
    created_by: str  # Username
    lvm_config: Optional[LVMPoolConfig] = None
    # Note: volumes field removed - LVs are ephemeral, created on-demand
    # Note: permissions field removed - all operations use sudo

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PoolConfig":
        """Create PoolConfig from dictionary."""
        # Remove old fields if present (backward compatibility)
        if "volumes" in data:
            del data["volumes"]
        if "permissions" in data:
            del data["permissions"]

        # Convert LVM config
        if "lvm_config" in data and data["lvm_config"]:
            data["lvm_config"] = LVMPoolConfig(**data["lvm_config"])

        return PoolConfig(**data)


@dataclass
class VolumeAllocation:
    """Tracks an active LV allocation."""

    lv_path: str  # Full LV path (e.g., /dev/vg/kdev-xxx-test)
    lv_name: str  # LV name only (e.g., kdev-xxx-test)
    pool_name: str  # Pool name
    vg_name: str  # Volume group name
    volume_spec: VolumeConfig  # Original volume specification
    pid: int  # Process ID that allocated this
    allocated_at: str  # ISO timestamp
    session_id: str  # Unique session identifier


class VolumeStateManager:
    """
    Manages LV allocation state across multiple MCP instances.

    Uses a shared JSON file with file locking to coordinate between
    independent MCP server processes.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        """Initialize state manager."""
        if state_dir is None:
            state_dir = Path.home() / ".kerneldev-mcp"

        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "lv-state.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> Dict[str, Any]:
        """Load state file with locking."""
        import fcntl

        if not self.state_file.exists():
            return {"allocations": []}

        # Open with shared lock for reading
        with open(self.state_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return data

    def _save_state(self, state: Dict[str, Any]) -> None:
        """Save state file with locking."""
        import fcntl

        # Write atomically with exclusive lock
        tmp_file = self.state_file.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        tmp_file.replace(self.state_file)

    def register_allocation(self, allocation: VolumeAllocation) -> None:
        """Register a new LV allocation."""
        state = self._load_state()

        allocations = state.get("allocations", [])
        allocations.append(
            {
                "lv_path": allocation.lv_path,
                "lv_name": allocation.lv_name,
                "pool_name": allocation.pool_name,
                "vg_name": allocation.vg_name,
                "volume_spec": {
                    "name": allocation.volume_spec.name,
                    "size": allocation.volume_spec.size,
                    "env_var": allocation.volume_spec.env_var,
                    "order": allocation.volume_spec.order,
                },
                "pid": allocation.pid,
                "allocated_at": allocation.allocated_at,
                "session_id": allocation.session_id,
            }
        )

        state["allocations"] = allocations
        self._save_state(state)
        logger.info(f"Registered allocation: {allocation.lv_name} (PID {allocation.pid})")

    def unregister_allocation(self, lv_name: str) -> None:
        """Unregister an LV allocation."""
        state = self._load_state()

        allocations = state.get("allocations", [])
        allocations = [a for a in allocations if a["lv_name"] != lv_name]

        state["allocations"] = allocations
        self._save_state(state)
        logger.info(f"Unregistered allocation: {lv_name}")

    def get_allocations_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all allocations for a session."""
        state = self._load_state()
        allocations = state.get("allocations", [])
        return [a for a in allocations if a["session_id"] == session_id]

    def cleanup_orphaned_volumes(self, pool_name: str) -> List[str]:
        """
        Find and clean up LVs from dead processes.

        Args:
            pool_name: Pool to clean up

        Returns:
            List of cleaned up LV names
        """
        state = self._load_state()
        allocations = state.get("allocations", [])

        cleaned = []
        remaining = []

        for alloc in allocations:
            if alloc["pool_name"] != pool_name:
                remaining.append(alloc)
                continue

            pid = alloc["pid"]

            # Check if process is still alive
            if self._is_process_alive(pid):
                remaining.append(alloc)
            else:
                # Process dead, clean up LV
                lv_path = alloc["lv_path"]
                logger.info(f"Cleaning up orphaned LV {alloc['lv_name']} from dead process {pid}")

                try:
                    subprocess.run(
                        ["sudo", "lvremove", "-f", lv_path], capture_output=True, timeout=30
                    )
                    cleaned.append(alloc["lv_name"])
                except Exception as e:
                    logger.error(f"Failed to remove orphaned LV {lv_path}: {e}")
                    remaining.append(alloc)  # Keep in state if cleanup failed

        # Update state
        state["allocations"] = remaining
        self._save_state(state)

        return cleaned

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)  # Signal 0 just checks if process exists
            return True
        except OSError:
            return False


class ConfigManager:
    """Manages device pool configuration storage."""

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize configuration manager.

        Args:
            config_dir: Directory for config storage (default: ~/.kerneldev-mcp)
        """
        if config_dir is None:
            config_dir = Path.home() / ".kerneldev-mcp"

        self.config_dir = Path(config_dir)
        self.config_file = self.config_dir / "device-pool.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load_pools(self) -> Dict[str, PoolConfig]:
        """
        Load all pool configurations.

        Returns:
            Dictionary mapping pool names to PoolConfig objects
        """
        if not self.config_file.exists():
            logger.debug(f"Config file not found: {self.config_file}")
            return {}

        try:
            with open(self.config_file, "r") as f:
                data = json.load(f)

            version = data.get("version", "1.0")
            if version != "1.0":
                logger.warning(f"Unknown config version: {version}")

            pools = {}
            for name, pool_data in data.get("pools", {}).items():
                pool_data["pool_name"] = name
                pools[name] = PoolConfig.from_dict(pool_data)

            logger.info(f"Loaded {len(pools)} pool(s) from {self.config_file}")
            return pools

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def save_pool(self, pool: PoolConfig) -> None:
        """
        Save a pool configuration.

        Args:
            pool: PoolConfig to save
        """
        # Load existing pools
        pools = self.load_pools()

        # Update with new/modified pool
        pools[pool.pool_name] = pool

        # Save all pools
        data = {"version": "1.0", "pools": {name: pool.to_dict() for name, pool in pools.items()}}

        # Write atomically using a temporary file
        tmp_file = self.config_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self.config_file)
            logger.info(f"Saved pool '{pool.pool_name}' to {self.config_file}")
        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink()
            logger.error(f"Failed to save config: {e}")
            raise

    def get_pool(self, pool_name: str) -> Optional[PoolConfig]:
        """
        Get a specific pool configuration.

        Args:
            pool_name: Name of the pool

        Returns:
            PoolConfig if found, None otherwise
        """
        pools = self.load_pools()
        return pools.get(pool_name)

    def delete_pool(self, pool_name: str) -> bool:
        """
        Delete a pool configuration.

        Args:
            pool_name: Name of the pool to delete

        Returns:
            True if pool was deleted, False if not found
        """
        pools = self.load_pools()

        if pool_name not in pools:
            logger.warning(f"Pool '{pool_name}' not found")
            return False

        del pools[pool_name]

        # Save updated pools
        data = {"version": "1.0", "pools": {name: pool.to_dict() for name, pool in pools.items()}}

        with open(self.config_file, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Deleted pool '{pool_name}'")
        return True


class SafetyValidator:
    """
    Comprehensive device safety validation.

    Implements the 10-point safety checklist from the design document to prevent
    accidental data destruction.
    """

    def __init__(self):
        """Initialize safety validator."""
        self.checks: List[Tuple[str, callable]] = [
            ("Device exists and is block device", self._check_exists_and_is_block_device),
            ("Device is not mounted", self._check_not_mounted),
            ("Device is not in /etc/fstab", self._check_not_in_fstab),
            ("Device is not a system disk", self._check_not_system_disk),
            ("Device is not a RAID member", self._check_not_raid_member),
            ("Device is not an LVM physical volume", self._check_not_lvm_pv),
            ("Device is not encrypted", self._check_not_encrypted),
            ("Device has no open file handles", self._check_no_open_handles),
            ("Check filesystem signatures", self._check_filesystem_signatures),
            ("Check partition table", self._check_partition_table),
        ]

    def validate_device(self, device: str, allow_existing_lvm: bool = False) -> ValidationResult:
        """
        Perform comprehensive safety validation on a device.

        Args:
            device: Device path to validate (e.g., "/dev/nvme1n1")
            allow_existing_lvm: If True, skip LVM PV check (for LVM pool creation)

        Returns:
            ValidationResult with status and details
        """
        logger.info(f"Validating device safety: {device}")

        details = {}
        highest_level = ValidationLevel.OK
        messages = []

        for check_name, check_func in self.checks:
            # Skip LVM check if creating LVM pool
            if check_name == "Device is not an LVM physical volume" and allow_existing_lvm:
                continue

            try:
                result = check_func(device)
                details[check_name] = result

                if result.level == ValidationLevel.ERROR:
                    highest_level = ValidationLevel.ERROR
                    messages.append(f"❌ {check_name}: {result.message}")
                elif result.level == ValidationLevel.WARNING:
                    if highest_level == ValidationLevel.OK:
                        highest_level = ValidationLevel.WARNING
                    messages.append(f"⚠️  {check_name}: {result.message}")
                else:
                    messages.append(f"✅ {check_name}")

            except Exception as e:
                logger.error(f"Check '{check_name}' failed with exception: {e}")
                details[check_name] = ValidationResult(
                    ValidationLevel.ERROR, f"Check failed: {str(e)}"
                )
                highest_level = ValidationLevel.ERROR
                messages.append(f"❌ {check_name}: Check failed ({e})")

        summary = "\n".join(messages)

        if highest_level == ValidationLevel.ERROR:
            message = f"Device {device} FAILED safety validation:\n{summary}"
        elif highest_level == ValidationLevel.WARNING:
            message = f"Device {device} passed with warnings:\n{summary}"
        else:
            message = f"Device {device} passed all safety checks:\n{summary}"

        return ValidationResult(level=highest_level, message=message, details=details)

    def _check_exists_and_is_block_device(self, device: str) -> ValidationResult:
        """Check if device exists and is a block device."""
        if not os.path.exists(device):
            return ValidationResult(ValidationLevel.ERROR, f"Device {device} does not exist")

        import stat

        try:
            st = os.stat(device)
            if not stat.S_ISBLK(st.st_mode):
                return ValidationResult(ValidationLevel.ERROR, f"{device} is not a block device")
        except Exception as e:
            return ValidationResult(ValidationLevel.ERROR, f"Failed to stat device: {e}")

        return ValidationResult(ValidationLevel.OK, "Device exists and is a block device")

    def _check_not_mounted(self, device: str) -> ValidationResult:
        """Check if device or its partitions are mounted."""
        try:
            # Check the device itself
            result = subprocess.run(
                ["findmnt", "-n", "-o", "TARGET", "-S", device],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and result.stdout.strip():
                mountpoints = result.stdout.strip().split("\n")
                return ValidationResult(
                    ValidationLevel.ERROR, f"Device is mounted at: {', '.join(mountpoints)}"
                )

            # Check for partitions (e.g., /dev/sda1, /dev/nvme0n1p1)
            device_base = os.path.basename(device)
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE,TARGET"], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        source, target = parts
                        # Check if source is a partition of our device
                        if source.startswith(device) or device_base in source:
                            return ValidationResult(
                                ValidationLevel.ERROR, f"Partition {source} is mounted at {target}"
                            )

            return ValidationResult(ValidationLevel.OK, "Device is not mounted")

        except subprocess.TimeoutExpired:
            return ValidationResult(ValidationLevel.ERROR, "Timeout checking mount status")
        except Exception as e:
            return ValidationResult(ValidationLevel.WARNING, f"Could not verify mount status: {e}")

    def _check_not_in_fstab(self, device: str) -> ValidationResult:
        """Check if device is referenced in /etc/fstab."""
        try:
            with open("/etc/fstab", "r") as f:
                fstab_content = f.read()

            device_base = os.path.basename(device)

            # Check for direct device path or basename
            if device in fstab_content or device_base in fstab_content:
                return ValidationResult(
                    ValidationLevel.ERROR, f"Device is referenced in /etc/fstab"
                )

            # Check for UUID or LABEL references
            try:
                result = subprocess.run(
                    ["blkid", "-s", "UUID", "-s", "LABEL", "-o", "value", device],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.returncode == 0:
                    for identifier in result.stdout.strip().split("\n"):
                        if identifier and identifier in fstab_content:
                            return ValidationResult(
                                ValidationLevel.ERROR,
                                f"Device identifier '{identifier}' found in /etc/fstab",
                            )
            except Exception:
                pass  # blkid may fail if device has no filesystem

            return ValidationResult(ValidationLevel.OK, "Device not in /etc/fstab")

        except FileNotFoundError:
            return ValidationResult(ValidationLevel.WARNING, "/etc/fstab not found")
        except Exception as e:
            return ValidationResult(ValidationLevel.WARNING, f"Could not check /etc/fstab: {e}")

    def _check_not_system_disk(self, device: str) -> ValidationResult:
        """Check if device contains system partitions."""
        system_mounts = ["/", "/boot", "/boot/efi", "/home", "/var", "/usr", "/opt"]

        try:
            for mount in system_mounts:
                result = subprocess.run(
                    ["findmnt", "-n", "-o", "SOURCE", mount],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.returncode == 0:
                    source = result.stdout.strip()
                    # Check if source is on our device
                    if source.startswith(device):
                        return ValidationResult(
                            ValidationLevel.ERROR, f"Device contains system partition for {mount}"
                        )

            return ValidationResult(ValidationLevel.OK, "Device is not a system disk")

        except Exception as e:
            return ValidationResult(
                ValidationLevel.WARNING, f"Could not verify system disk status: {e}"
            )

    def _check_not_raid_member(self, device: str) -> ValidationResult:
        """Check if device is part of a RAID array."""
        try:
            # Check using mdadm
            result = subprocess.run(
                ["mdadm", "--examine", device], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                return ValidationResult(ValidationLevel.ERROR, f"Device is a RAID member")

            return ValidationResult(ValidationLevel.OK, "Device is not a RAID member")

        except FileNotFoundError:
            # mdadm not installed, skip check
            return ValidationResult(
                ValidationLevel.WARNING, "mdadm not found, cannot verify RAID status"
            )
        except Exception as e:
            return ValidationResult(ValidationLevel.WARNING, f"Could not check RAID status: {e}")

    def _check_not_lvm_pv(self, device: str) -> ValidationResult:
        """Check if device is an LVM physical volume."""
        try:
            result = subprocess.run(
                ["pvdisplay", device], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                return ValidationResult(
                    ValidationLevel.ERROR, f"Device is already an LVM physical volume"
                )

            return ValidationResult(ValidationLevel.OK, "Device is not an LVM PV")

        except FileNotFoundError:
            # LVM tools not installed
            return ValidationResult(
                ValidationLevel.WARNING, "LVM tools not found, cannot verify PV status"
            )
        except Exception as e:
            return ValidationResult(ValidationLevel.WARNING, f"Could not check LVM PV status: {e}")

    def _check_not_encrypted(self, device: str) -> ValidationResult:
        """Check if device is encrypted."""
        try:
            result = subprocess.run(
                ["cryptsetup", "isLuks", device], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                return ValidationResult(ValidationLevel.ERROR, f"Device is LUKS encrypted")

            return ValidationResult(ValidationLevel.OK, "Device is not encrypted")

        except FileNotFoundError:
            # cryptsetup not installed
            return ValidationResult(
                ValidationLevel.WARNING, "cryptsetup not found, cannot verify encryption status"
            )
        except Exception as e:
            return ValidationResult(
                ValidationLevel.WARNING, f"Could not check encryption status: {e}"
            )

    def _check_no_open_handles(self, device: str) -> ValidationResult:
        """Check if device has open file handles."""
        try:
            result = subprocess.run(["lsof", device], capture_output=True, text=True, timeout=5)

            if result.stdout.strip():
                return ValidationResult(
                    ValidationLevel.ERROR, f"Device has open file handles:\n{result.stdout}"
                )

            return ValidationResult(ValidationLevel.OK, "No open file handles")

        except FileNotFoundError:
            # lsof not installed
            return ValidationResult(
                ValidationLevel.WARNING, "lsof not found, cannot verify open handles"
            )
        except Exception as e:
            return ValidationResult(ValidationLevel.WARNING, f"Could not check open handles: {e}")

    def _check_filesystem_signatures(self, device: str) -> ValidationResult:
        """Check for filesystem signatures (data will be destroyed)."""
        try:
            result = subprocess.run(
                ["blkid", "-p", device], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                return ValidationResult(
                    ValidationLevel.WARNING,
                    f"Device has filesystem/partition signatures (will be destroyed): {result.stdout.strip()}",
                )

            return ValidationResult(ValidationLevel.OK, "No filesystem signatures detected")

        except FileNotFoundError:
            return ValidationResult(
                ValidationLevel.WARNING, "blkid not found, cannot check filesystem signatures"
            )
        except Exception as e:
            return ValidationResult(
                ValidationLevel.WARNING, f"Could not check filesystem signatures: {e}"
            )

    def _check_partition_table(self, device: str) -> ValidationResult:
        """Check for existing partition table."""
        try:
            # Use sgdisk to check for GPT
            result = subprocess.run(
                ["sgdisk", "-p", device], capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                # Has partition table
                return ValidationResult(
                    ValidationLevel.WARNING,
                    "Device has existing partition table (will be destroyed)",
                )

            return ValidationResult(ValidationLevel.OK, "No partition table detected")

        except FileNotFoundError:
            # sgdisk not installed, try parted
            try:
                result = subprocess.run(
                    ["parted", "-s", device, "print"], capture_output=True, text=True, timeout=5
                )

                if "Partition Table:" in result.stdout:
                    return ValidationResult(
                        ValidationLevel.WARNING,
                        "Device has existing partition table (will be destroyed)",
                    )

                return ValidationResult(ValidationLevel.OK, "No partition table detected")

            except FileNotFoundError:
                return ValidationResult(
                    ValidationLevel.WARNING, "sgdisk/parted not found, cannot check partition table"
                )
            except Exception as e:
                return ValidationResult(
                    ValidationLevel.WARNING, f"Could not check partition table: {e}"
                )

        except Exception as e:
            return ValidationResult(
                ValidationLevel.WARNING, f"Could not check partition table: {e}"
            )


class TransactionalDeviceSetup:
    """
    Context manager for transactional device setup with rollback on failure.

    Usage:
        with TransactionalDeviceSetup(device) as txn:
            txn.create_partitions()
            txn.setup_lvm()
            # If any exception occurs, changes are rolled back
    """

    def __init__(self, device: str):
        """
        Initialize transactional setup.

        Args:
            device: Device path
        """
        self.device = device
        self.backup_partition_table: Optional[bytes] = None
        self.created_pvs: List[str] = []
        self.created_vgs: List[str] = []
        self.created_lvs: List[str] = []
        self.operations: List[str] = []

    def __enter__(self) -> "TransactionalDeviceSetup":
        """Enter transaction context."""
        logger.info(f"Starting transactional setup for {self.device}")

        # Backup partition table
        try:
            self.backup_partition_table = self._save_partition_table()
            logger.debug("Partition table backed up")
        except Exception as e:
            logger.warning(f"Could not backup partition table: {e}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit transaction context, rollback on error."""
        if exc_type is not None:
            logger.error(f"Transaction failed: {exc_val}")
            self._rollback()
            return False  # Re-raise exception

        logger.info("Transaction completed successfully")
        return True

    def _save_partition_table(self) -> Optional[bytes]:
        """Save current partition table."""
        try:
            result = subprocess.run(
                ["sudo", "sgdisk", "--backup=/dev/stdout", self.device],
                capture_output=True,
                timeout=10,
            )

            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.warning(f"Failed to backup partition table: {e}")

        return None

    def _restore_partition_table(self) -> None:
        """Restore partition table from backup."""
        if self.backup_partition_table is None:
            logger.warning("No partition table backup available")
            return

        try:
            # Write backup to temporary file
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(self.backup_partition_table)
                backup_file = f.name

            # Restore partition table
            subprocess.run(
                ["sudo", "sgdisk", f"--load-backup={backup_file}", self.device],
                capture_output=True,
                timeout=10,
                check=True,
            )

            os.unlink(backup_file)
            logger.info("Partition table restored")

        except Exception as e:
            logger.error(f"Failed to restore partition table: {e}")

    def _rollback(self) -> None:
        """Rollback all changes made during transaction."""
        logger.info("Rolling back changes...")

        # Remove logical volumes (in reverse order)
        for lv in reversed(self.created_lvs):
            try:
                subprocess.run(["sudo", "lvremove", "-f", lv], capture_output=True, timeout=10)
                logger.info(f"Removed LV: {lv}")
            except Exception as e:
                logger.error(f"Failed to remove LV {lv}: {e}")

        # Remove volume groups (in reverse order)
        for vg in reversed(self.created_vgs):
            try:
                subprocess.run(["sudo", "vgremove", "-f", vg], capture_output=True, timeout=10)
                logger.info(f"Removed VG: {vg}")
            except Exception as e:
                logger.error(f"Failed to remove VG {vg}: {e}")

        # Remove physical volumes (in reverse order)
        for pv in reversed(self.created_pvs):
            try:
                subprocess.run(["sudo", "pvremove", "-f", pv], capture_output=True, timeout=10)
                logger.info(f"Removed PV: {pv}")
            except Exception as e:
                logger.error(f"Failed to remove PV {pv}: {e}")

        # Restore partition table
        if self.backup_partition_table:
            self._restore_partition_table()

        logger.info("Rollback complete")

    def record_pv(self, pv: str) -> None:
        """Record created physical volume for rollback."""
        self.created_pvs.append(pv)

    def record_vg(self, vg: str) -> None:
        """Record created volume group for rollback."""
        self.created_vgs.append(vg)

    def record_lv(self, lv: str) -> None:
        """Record created logical volume for rollback."""
        self.created_lvs.append(lv)


class DevicePoolManager(ABC):
    """
    Abstract base class for device pool management.

    Implementations: PartitionPoolManager, LVMPoolManager
    """

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """
        Initialize device pool manager.

        Args:
            config_manager: Configuration manager (default: create new one)
        """
        self.config_manager = config_manager or ConfigManager()
        self.safety_validator = SafetyValidator()

    @abstractmethod
    def setup_pool(self, device: str, pool_name: str, **options) -> PoolConfig:
        """
        Create a new device pool (PV + VG only, no LVs).

        Args:
            device: Physical device path
            pool_name: Pool identifier
            **options: Pool-specific options

        Returns:
            PoolConfig for the created pool
        """
        pass

    @abstractmethod
    def teardown_pool(self, pool_name: str, wipe_data: bool = False) -> bool:
        """
        Remove a device pool (VG + PV).

        Args:
            pool_name: Pool identifier
            wipe_data: If True, overwrite with zeros

        Returns:
            True if pool was removed
        """
        pass

    @abstractmethod
    def allocate_volumes(
        self, pool_name: str, volume_specs: List[VolumeConfig], session_id: str
    ) -> List[VolumeAllocation]:
        """
        Allocate (create) volumes with unique names for a session.

        Args:
            pool_name: Pool identifier
            volume_specs: Volume specifications (name, size, env_var, order)
            session_id: Unique session identifier

        Returns:
            List of VolumeAllocation objects
        """
        pass

    @abstractmethod
    def release_volumes(self, pool_name: str, session_id: str, keep_volumes: bool = False) -> bool:
        """
        Release (delete) volumes allocated for a session.

        Args:
            pool_name: Pool identifier
            session_id: Unique session identifier
            keep_volumes: If True, don't delete LVs (for debugging)

        Returns:
            True if release succeeded
        """
        pass

    def validate_pool(self, pool_name: str) -> ValidationResult:
        """
        Validate pool health (VG exists and is accessible).

        Args:
            pool_name: Pool identifier

        Returns:
            ValidationResult
        """
        pool = self.config_manager.get_pool(pool_name)

        if pool is None:
            return ValidationResult(ValidationLevel.ERROR, f"Pool '{pool_name}' not found")

        if pool.lvm_config is None:
            return ValidationResult(
                ValidationLevel.ERROR, f"Pool '{pool_name}' has no LVM configuration"
            )

        vg_name = pool.lvm_config.vg_name

        # Check if VG exists
        try:
            result = subprocess.run(
                ["vgs", "--noheadings", "-o", "vg_name", vg_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return ValidationResult(
                    ValidationLevel.ERROR, f"Volume group '{vg_name}' does not exist"
                )

        except Exception as e:
            return ValidationResult(ValidationLevel.ERROR, f"Failed to check VG status: {e}")

        return ValidationResult(ValidationLevel.OK, f"Pool '{pool_name}' VG '{vg_name}' is healthy")


def _grant_user_lv_access(lv_path: str) -> bool:
    """Grant user read/write access to LV device by changing ownership.

    This is sufficient for ephemeral LVs that are deleted after each run.
    For persistent access, user should be added to 'disk' group.

    Args:
        lv_path: Full LV path (e.g., /dev/vg/lv-name)

    Returns:
        True if access granted successfully
    """
    import pwd

    # Get username safely (check SUDO_USER first, then USER, then fallback to getpwuid)
    username = (
        os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    )

    # Wait for device to appear and settle (udev processing)
    device_path = Path(lv_path)
    for attempt in range(20):  # Wait up to 2 seconds
        if device_path.exists():
            break
        time.sleep(0.1)
    else:
        logger.error(f"Device {lv_path} did not appear after creation")
        return False

    # Wait for udev to finish processing the device
    try:
        subprocess.run(
            ["sudo", "udevadm", "settle", "--timeout=5"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError:
        # Non-fatal - proceed anyway
        logger.debug("udevadm settle failed, proceeding anyway")
    except FileNotFoundError:
        # udevadm not available
        logger.debug("udevadm not found, proceeding without settle")

    # Resolve symlinks to actual device (LVM creates both /dev/vg/lv and /dev/mapper/vg-lv)
    actual_path = device_path.resolve()
    if actual_path != device_path:
        logger.debug(f"Resolved {lv_path} to {actual_path}")

    # Change ownership to user (disk group for compatibility)
    try:
        subprocess.run(
            ["sudo", "chown", f"{username}:disk", str(actual_path)],
            capture_output=True,
            check=True,
            timeout=5,
        )
        # Also chown the symlink if different
        if actual_path != device_path:
            subprocess.run(
                ["sudo", "chown", "-h", f"{username}:disk", lv_path],
                capture_output=True,
                check=True,
                timeout=5,
            )
        logger.debug(f"Changed ownership of {lv_path} to {username}:disk")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to change ownership of {lv_path}: {e.stderr.decode()}")
        return False

    # Ensure permissions are correct (660)
    try:
        subprocess.run(
            ["sudo", "chmod", "660", str(actual_path)], capture_output=True, check=True, timeout=5
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to chmod {lv_path}: {e.stderr.decode()}")
        return False

    # Log actual permissions for debugging
    try:
        stat_info = os.stat(lv_path)
        logger.debug(
            f"Device {lv_path}: uid={stat_info.st_uid}, "
            f"gid={stat_info.st_gid}, mode={oct(stat_info.st_mode)}"
        )
    except Exception as e:
        logger.debug(f"Could not stat {lv_path}: {e}")

    # Verify we can actually access the device with read/write permissions
    try:
        fd = os.open(lv_path, os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)
        logger.info(f"✓ Granted {username} read/write access to {lv_path}")
        return True
    except PermissionError:
        # Check if user is in disk group
        logger.error(
            f"Cannot access {lv_path} even after ownership change.\n"
            f"You may need to add yourself to the 'disk' group:\n"
            f"  sudo usermod -a -G disk {username}\n"
            f"Then logout and login again.\n"
            f"WARNING: This grants access to ALL block devices on the system."
        )
        return False
    except Exception as e:
        logger.error(f"Failed to verify access to {lv_path}: {e}")
        return False


class LVMPoolManager(DevicePoolManager):
    """
    LVM-based device pool manager (the only pool manager).

    Creates LVM structure: Physical Volume -> Volume Group -> Logical Volumes.
    Supports snapshots, resizing, and thin provisioning for maximum flexibility.
    """

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """Initialize LVM pool manager."""
        super().__init__(config_manager)
        self.state_manager = VolumeStateManager()

    def setup_pool(self, device: str, pool_name: str, **options) -> PoolConfig:
        """
        Create LVM-based pool (PV + VG only, no LVs).

        LVs are created on-demand with unique names when tests run.

        Args:
            device: Physical device path
            pool_name: Pool identifier
            **options: vg_name, lv_prefix, user

        Returns:
            PoolConfig for created pool
        """
        import pwd

        logger.info(f"Creating LVM pool '{pool_name}' on {device} (PV + VG only)")

        # Validate device safety
        validation = self.safety_validator.validate_device(device, allow_existing_lvm=False)
        if not validation.is_safe:
            raise RuntimeError(f"Device validation failed: {validation.message}")

        # Get options
        vg_name = options.get("vg_name", f"kerneldev-{pool_name}-vg")
        lv_prefix = options.get("lv_prefix", "kdev")
        user = options.get("user", pwd.getpwuid(os.getuid()).pw_name)

        # Create pool using transactional setup
        with TransactionalDeviceSetup(device) as txn:
            # Create physical volume
            logger.info(f"Creating physical volume on {device}...")
            subprocess.run(
                ["sudo", "pvcreate", "-f", device], capture_output=True, check=True, timeout=30
            )
            txn.record_pv(device)

            # Create volume group
            logger.info(f"Creating volume group '{vg_name}'...")
            subprocess.run(
                ["sudo", "vgcreate", vg_name, device], capture_output=True, check=True, timeout=30
            )
            txn.record_vg(vg_name)

            # Create LVM config
            lvm_config = LVMPoolConfig(
                pv=device,
                vg_name=vg_name,
                lv_prefix=lv_prefix,
                thin_provisioning=False,  # Not yet implemented
            )

            # Create pool config (no volumes - they're created on-demand)
            # No permissions needed - all LVM operations use sudo
            pool_config = PoolConfig(
                pool_name=pool_name,
                device=device,
                created_at=datetime.now().isoformat(),
                created_by=user,
                lvm_config=lvm_config,
            )

            # Save configuration
            self.config_manager.save_pool(pool_config)

            logger.info(f"Pool '{pool_name}' created successfully (VG ready for on-demand LVs)")
            return pool_config

    def teardown_pool(self, pool_name: str, wipe_data: bool = False) -> bool:
        """
        Remove LVM-based pool (VG + PV).

        Cleans up any orphaned LVs first, then removes VG and PV.

        Args:
            pool_name: Pool identifier
            wipe_data: If True, overwrite with zeros (slow)

        Returns:
            True if pool was removed
        """
        logger.info(f"Tearing down pool '{pool_name}'")

        # Load pool config
        pool = self.config_manager.get_pool(pool_name)
        if pool is None:
            logger.error(f"Pool '{pool_name}' not found")
            return False

        if pool.lvm_config is None:
            logger.error(f"Pool '{pool_name}' has no LVM configuration")
            return False

        vg_name = pool.lvm_config.vg_name

        # Clean up orphaned volumes first
        logger.info("Cleaning up orphaned volumes...")
        cleaned = self.state_manager.cleanup_orphaned_volumes(pool_name)
        if cleaned:
            logger.info(f"Cleaned {len(cleaned)} orphaned volume(s)")

        # Remove volume group (will fail if active LVs exist)
        logger.info(f"Removing volume group '{vg_name}'...")
        result = subprocess.run(
            ["sudo", "vgremove", "-f", vg_name], capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Failed to remove VG: {result.stderr}")
            logger.error("There may be active LVs. Check with: sudo lvs")
            return False

        # Remove physical volume
        logger.info(f"Removing physical volume on {pool.device}...")
        subprocess.run(
            ["sudo", "pvremove", "-f", pool.device], capture_output=True, check=True, timeout=30
        )

        # Optionally wipe data
        if wipe_data:
            logger.warning(f"Wiping data on {pool.device} (this may take a while)...")
            subprocess.run(
                ["sudo", "dd", "if=/dev/zero", f"of={pool.device}", "bs=1M", "count=100"],
                capture_output=True,
                timeout=300,
            )

        # Delete pool configuration
        self.config_manager.delete_pool(pool_name)

        logger.info(f"Pool '{pool_name}' removed successfully")
        return True

    def allocate_volumes(
        self, pool_name: str, volume_specs: List[VolumeConfig], session_id: str
    ) -> List[VolumeAllocation]:
        """
        Allocate (create) volumes with unique names for this session.

        Names are: {lv_prefix}-{timestamp}-{random}-{volume_name}
        Example: kdev-20251115103045-a3f9d2-test

        Args:
            pool_name: Pool identifier
            volume_specs: Volume specifications
            session_id: Unique session identifier

        Returns:
            List of VolumeAllocation objects
        """
        import secrets
        import time

        pool = self.config_manager.get_pool(pool_name)
        if pool is None:
            raise ValueError(f"Pool '{pool_name}' not found")

        if pool.lvm_config is None:
            raise ValueError(f"Pool '{pool_name}' has no LVM configuration")

        vg_name = pool.lvm_config.vg_name
        lv_prefix = pool.lvm_config.lv_prefix
        pid = os.getpid()

        # Generate unique suffix: timestamp + random
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = secrets.token_hex(3)  # 6 hex chars
        unique_prefix = f"{lv_prefix}-{timestamp}-{random_suffix}"

        logger.info(f"Allocating {len(volume_specs)} volume(s) with prefix: {unique_prefix}")

        allocations = []
        try:
            for vol_spec in volume_specs:
                lv_name = f"{unique_prefix}-{vol_spec.name}"
                lv_path = f"/dev/{vg_name}/{lv_name}"

                logger.info(f"Creating LV '{lv_name}' ({vol_spec.size})...")

                subprocess.run(
                    ["sudo", "lvcreate", "-y", "-L", vol_spec.size, "-n", lv_name, vg_name],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )

                # Grant user access to LV device
                success = _grant_user_lv_access(lv_path)
                if not success:
                    raise RuntimeError(f"Failed to grant access to {lv_path}")

                # Create allocation record
                allocation = VolumeAllocation(
                    lv_path=lv_path,
                    lv_name=lv_name,
                    pool_name=pool_name,
                    vg_name=vg_name,
                    volume_spec=vol_spec,
                    pid=pid,
                    allocated_at=datetime.now().isoformat(),
                    session_id=session_id,
                )

                allocations.append(allocation)

                # Register in state file
                self.state_manager.register_allocation(allocation)

            logger.info(f"Allocated {len(allocations)} volume(s) for session {session_id}")
            return allocations

        except Exception as e:
            # Rollback - remove any LVs we created
            logger.error(f"Failed to allocate volumes: {e}")
            for alloc in allocations:
                try:
                    subprocess.run(
                        ["sudo", "lvremove", "-f", alloc.lv_path], capture_output=True, timeout=30
                    )
                    self.state_manager.unregister_allocation(alloc.lv_name)
                except Exception:
                    pass
            raise

    def release_volumes(self, pool_name: str, session_id: str, keep_volumes: bool = False) -> bool:
        """
        Release (delete) volumes allocated for a session.

        Args:
            pool_name: Pool identifier
            session_id: Unique session identifier
            keep_volumes: If True, don't delete LVs (for debugging)

        Returns:
            True if release succeeded
        """
        logger.info(f"Releasing volumes for session {session_id} (keep={keep_volumes})")

        # Get allocations for this session
        allocations = self.state_manager.get_allocations_for_session(session_id)

        if not allocations:
            logger.warning(f"No allocations found for session {session_id}")
            return True

        # Remove LVs unless keep_volumes is True
        if not keep_volumes:
            for alloc in allocations:
                try:
                    logger.info(f"Removing LV {alloc['lv_name']}...")
                    subprocess.run(
                        ["sudo", "lvremove", "-f", alloc["lv_path"]],
                        capture_output=True,
                        timeout=30,
                    )
                except Exception as e:
                    logger.error(f"Failed to remove LV {alloc['lv_name']}: {e}")

        # Unregister from state (even if keep_volumes - no longer tracked)
        for alloc in allocations:
            self.state_manager.unregister_allocation(alloc["lv_name"])

        if keep_volumes:
            logger.info(f"Kept {len(allocations)} volume(s) for debugging")
        else:
            logger.info(f"Released {len(allocations)} volume(s)")

        return True

    def cleanup_orphaned_volumes(self, pool_name: str) -> List[str]:
        """
        Clean up LVs from dead processes.

        Args:
            pool_name: Pool identifier

        Returns:
            List of cleaned up LV names
        """
        return self.state_manager.cleanup_orphaned_volumes(pool_name)

    def resize_volume(self, pool_name: str, lv_name: str, new_size: str) -> bool:
        """
        Resize a logical volume by its full LV name.

        Args:
            pool_name: Pool identifier
            lv_name: Full LV name (e.g., kdev-20251115-a3f9d2-test)
            new_size: New size (e.g., "20G" or "+10G")

        Returns:
            True if resize succeeded
        """
        logger.info(f"Resizing LV '{lv_name}' in pool '{pool_name}' to {new_size}")

        # Load pool config
        pool = self.config_manager.get_pool(pool_name)
        if pool is None:
            logger.error(f"Pool '{pool_name}' not found")
            return False

        if pool.lvm_config is None:
            logger.error(f"Pool '{pool_name}' has no LVM configuration")
            return False

        vg_name = pool.lvm_config.vg_name
        lv_path = f"/dev/{vg_name}/{lv_name}"

        # Resize LV
        logger.info(f"Resizing {lv_path} to {new_size}...")
        subprocess.run(
            ["sudo", "lvresize", "-L", new_size, lv_path],
            capture_output=True,
            check=True,
            timeout=60,
        )

        logger.info(f"LV '{lv_name}' resized successfully")
        return True

    def create_snapshot(
        self, pool_name: str, lv_name: str, snapshot_name: str, snapshot_size: str = "1G"
    ) -> bool:
        """
        Create LVM snapshot of a volume.

        Args:
            pool_name: Pool identifier
            lv_name: Source LV name (e.g., kdev-20251115-a3f9d2-test)
            snapshot_name: Snapshot name
            snapshot_size: Snapshot size (default: 1G)

        Returns:
            True if snapshot created
        """
        logger.info(f"Creating snapshot '{snapshot_name}' of '{lv_name}' in pool '{pool_name}'")

        # Load pool config
        pool = self.config_manager.get_pool(pool_name)
        if pool is None:
            logger.error(f"Pool '{pool_name}' not found")
            return False

        if pool.lvm_config is None:
            logger.error(f"Pool '{pool_name}' has no LVM configuration")
            return False

        vg_name = pool.lvm_config.vg_name
        lv_path = f"/dev/{vg_name}/{lv_name}"

        # Create snapshot
        logger.info(f"Creating snapshot {snapshot_name} of {lv_path}...")
        subprocess.run(
            ["sudo", "lvcreate", "-L", snapshot_size, "-s", "-n", snapshot_name, lv_path],
            capture_output=True,
            check=True,
            timeout=30,
        )

        logger.info(f"Snapshot '{snapshot_name}' created successfully")
        return True

    def delete_snapshot(self, pool_name: str, snapshot_name: str) -> bool:
        """
        Delete LVM snapshot.

        Args:
            pool_name: Pool identifier
            snapshot_name: Snapshot name to delete

        Returns:
            True if snapshot deleted
        """
        logger.info(f"Deleting snapshot '{snapshot_name}' in pool '{pool_name}'")

        # Load pool config
        pool = self.config_manager.get_pool(pool_name)
        if pool is None:
            logger.error(f"Pool '{pool_name}' not found")
            return False

        if pool.lvm_config is None:
            logger.error(f"Pool '{pool_name}' has no LVM configuration")
            return False

        vg_name = pool.lvm_config.vg_name
        snapshot_path = f"/dev/{vg_name}/{snapshot_name}"

        # Delete snapshot
        logger.info(f"Deleting snapshot {snapshot_path}...")
        subprocess.run(
            ["sudo", "lvremove", "-f", snapshot_path], capture_output=True, check=True, timeout=30
        )

        logger.info(f"Snapshot '{snapshot_name}' deleted successfully")
        return True


def allocate_pool_volumes(
    pool_name: str,
    volume_specs: List[VolumeConfig],
    session_id: str,
    config_dir: Optional[Path] = None,
) -> Optional[List[Any]]:
    """
    Allocate volumes from a pool and convert to DeviceSpec objects.

    This creates unique LVs for this session and returns them as DeviceSpec
    objects ready for VM attachment.

    Args:
        pool_name: Name of pool to use
        volume_specs: Volume specifications (name, size, env_var, order)
        session_id: Unique session identifier
        config_dir: Optional config directory

    Returns:
        List of DeviceSpec objects or None if pool not found

    Note:
        Returns Any to avoid circular import with boot_manager.
        Caller should treat return value as List[DeviceSpec].
        Caller MUST call release_pool_volumes() after use!
    """
    config_manager = ConfigManager(config_dir)
    manager = LVMPoolManager(config_manager)

    # Allocate volumes
    try:
        allocations = manager.allocate_volumes(pool_name, volume_specs, session_id)
    except Exception as e:
        logger.error(f"Failed to allocate volumes from pool '{pool_name}': {e}")
        return None

    # Import DeviceSpec here to avoid circular import
    try:
        from .boot_manager import DeviceSpec
    except ImportError:
        logger.error("Failed to import DeviceSpec from boot_manager")
        # Cleanup - release volumes we just allocated
        manager.release_volumes(pool_name, session_id, keep_volumes=False)
        return None

    # Convert allocations to DeviceSpec objects
    device_specs = []
    for alloc in allocations:
        spec = DeviceSpec(
            path=alloc.lv_path,
            name=alloc.volume_spec.name,
            order=alloc.volume_spec.order,
            env_var=alloc.volume_spec.env_var,
            readonly=False,  # Device pool volumes are always read-write
        )
        device_specs.append(spec)

    logger.info(
        f"Allocated {len(device_specs)} volume(s) from pool '{pool_name}' for session {session_id}"
    )
    return device_specs


def release_pool_volumes(
    pool_name: str, session_id: str, keep_volumes: bool = False, config_dir: Optional[Path] = None
) -> bool:
    """
    Release volumes allocated for a session.

    Args:
        pool_name: Name of pool
        session_id: Session identifier
        keep_volumes: If True, don't delete LVs (for debugging)
        config_dir: Optional config directory

    Returns:
        True if release succeeded
    """
    config_manager = ConfigManager(config_dir)
    manager = LVMPoolManager(config_manager)

    return manager.release_volumes(pool_name, session_id, keep_volumes)
