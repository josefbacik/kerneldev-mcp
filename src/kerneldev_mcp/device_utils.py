"""
Shared utilities for device operations across kerneldev-mcp.

This module consolidates device management functions that were previously
duplicated across boot_manager.py and device_manager.py.
"""

import logging
import os
import re
import stat
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class DeviceBacking(Enum):
    """Device backing type for created devices.

    Determines how memory-based devices are backed:
    - DISK: Loop device backed by disk sparse file (slowest, universal)
    - TMPFS: Loop device backed by tmpfs (fast, memory-backed)
    - NULL_BLK: null_blk kernel device (fastest, memory-only, requires kernel support)
    """

    DISK = "disk"
    TMPFS = "tmpfs"
    NULL_BLK = "null_blk"


# null_blk configuration
CONFIGFS_ROOT = Path("/sys/kernel/config")
NULLB_CONFIGFS = CONFIGFS_ROOT / "nullb"

# Memory limits for null_blk devices (configurable via environment variables)
# Default to 32GB per device, 70GB total if environment variables are invalid
try:
    MAX_NULL_BLK_DEVICE_GB = int(os.getenv("KERNELDEV_NULL_BLK_MAX_SIZE", "32"))
except (ValueError, TypeError):
    logger.warning("Invalid KERNELDEV_NULL_BLK_MAX_SIZE environment variable, using default 32GB")
    MAX_NULL_BLK_DEVICE_GB = 32

try:
    MAX_NULL_BLK_TOTAL_GB = int(os.getenv("KERNELDEV_NULL_BLK_TOTAL", "70"))
except (ValueError, TypeError):
    logger.warning("Invalid KERNELDEV_NULL_BLK_TOTAL environment variable, using default 70GB")
    MAX_NULL_BLK_TOTAL_GB = 70


def create_loop_device(
    size: str, name: str, backing_dir: Optional[Path] = None
) -> Tuple[Optional[str], Optional[Path]]:
    """Create a loop device with sparse file backing.

    Args:
        size: Device size (e.g., "10G", "512M", "1024K")
        name: Name for backing file (e.g., "test", "scratch")
        backing_dir: Directory for backing file (default: /var/tmp/kerneldev-loop)

    Returns:
        (device_path, backing_file_path) or (None, None) on failure

    Example:
        >>> loop_dev, backing = create_loop_device("10G", "test")
        >>> if loop_dev:
        ...     print(f"Created {loop_dev} backed by {backing}")
    """
    work_dir = backing_dir or Path("/var/tmp/kerneldev-loop")
    work_dir.mkdir(parents=True, exist_ok=True)
    backing_file = work_dir / f"{name}.img"

    try:
        # Create sparse file
        subprocess.run(
            ["truncate", "-s", size, str(backing_file)],
            check=True,
            capture_output=True,
            text=True,
        )

        # Setup loop device
        result = subprocess.run(
            ["sudo", "losetup", "-f", "--show", str(backing_file)],
            check=True,
            capture_output=True,
            text=True,
        )

        loop_dev = result.stdout.strip()

        # Change permissions so current user can access the loop device
        # This is needed because virtme-ng (QEMU) runs as the current user
        try:
            subprocess.run(
                ["sudo", "chmod", "666", loop_dev],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            # If chmod fails, cleanup and return error
            subprocess.run(["sudo", "losetup", "-d", loop_dev], capture_output=True)
            if backing_file.exists():
                backing_file.unlink()
            return None, None

        return loop_dev, backing_file

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create loop device for {name}: {e.stderr if e.stderr else str(e)}")
        # Cleanup backing file if loop setup failed
        if backing_file.exists():
            try:
                backing_file.unlink()
            except OSError:
                pass
        return None, None


def cleanup_loop_device(device_path: str, backing_file: Optional[Path] = None) -> bool:
    """Clean up a loop device and optionally its backing file.

    Args:
        device_path: Path to loop device (e.g., "/dev/loop0")
        backing_file: Optional backing file to remove

    Returns:
        True if cleanup succeeded, False otherwise
    """
    success = True

    # Detach loop device
    try:
        subprocess.run(
            ["sudo", "losetup", "-d", device_path],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        success = False
        # Try to force detach if normal detach fails
        try:
            subprocess.run(
                ["sudo", "losetup", "-D"],  # Detach all unused loop devices
                capture_output=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    # Remove backing file if specified
    if backing_file and backing_file.exists():
        try:
            backing_file.unlink()
        except OSError:
            success = False

    return success


def check_null_blk_support() -> Tuple[bool, str]:
    """Check if null_blk is supported and available on this system.

    Returns:
        (is_supported, error_message)

    Checks:
        1. Kernel module available (modprobe null_blk or built-in)
        2. configfs mounted at /sys/kernel/config
        3. /sys/kernel/config/nullb/ directory exists
        4. Can create test directory (write permission)
    """
    # Check if null_blk module is already loaded
    if not Path("/sys/module/null_blk").exists():
        # Try to load the module
        try:
            subprocess.run(
                ["sudo", "modprobe", "null_blk"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            # Wait briefly for module to initialize
            time.sleep(0.1)
        except subprocess.CalledProcessError as e:
            return (
                False,
                f"null_blk kernel module not available: {e.stderr.decode() if e.stderr else 'unknown error'}",
            )
        except Exception as e:
            return False, f"Failed to load null_blk module: {e}"

    # Check if configfs is mounted
    if not CONFIGFS_ROOT.exists():
        return False, f"configfs not mounted at {CONFIGFS_ROOT}"

    # Check if nullb directory exists
    if not NULLB_CONFIGFS.exists():
        return False, f"{NULLB_CONFIGFS} does not exist (kernel too old or module not loaded)"

    # Check write permission by attempting to create test directory
    test_dir = NULLB_CONFIGFS / "nullb_test_probe"
    try:
        subprocess.run(
            ["sudo", "mkdir", str(test_dir)],
            check=True,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["sudo", "rmdir", str(test_dir)],
            capture_output=True,
            timeout=5,
        )
        return True, "null_blk available"
    except subprocess.CalledProcessError as e:
        return (
            False,
            f"No permission to create null_blk devices: {e.stderr.decode() if e.stderr else 'permission denied'}",
        )
    except Exception as e:
        return False, f"Cannot create null_blk devices: {e}"


def _allocate_null_blk_index() -> Optional[int]:
    """Atomically allocate next available null_blk index.

    Uses directory creation atomicity in configfs - first mkdir success wins.

    Returns:
        Device index (0-1023) or None if all indices are in use
    """
    for idx in range(1024):  # null_blk supports indices 0-1023
        device_dir = NULLB_CONFIGFS / f"nullb{idx}"
        try:
            subprocess.run(
                ["sudo", "mkdir", str(device_dir)],
                check=True,
                capture_output=True,
                timeout=5,
            )
            return idx
        except subprocess.CalledProcessError:
            # Index already taken, try next
            continue
        except Exception as e:
            logger.error(f"Failed to create {device_dir}: {e}")
            return None

    logger.error("All null_blk device indices (0-1023) are in use!")
    return None


def _parse_size_to_mb(size: str) -> Tuple[bool, str, int]:
    """Parse size string to megabytes for null_blk.

    Args:
        size: Size string (e.g., "10G", "512M", "1024K")

    Returns:
        (is_valid, error_message, size_in_mb)
    """
    match = re.match(r"^(\d+)(G|M|K)?$", size, re.IGNORECASE)
    if not match:
        return False, f"Invalid size format: {size}. Use format like '10G', '512M', or '1024K'", 0

    size_num = int(match.group(1))
    size_unit = (match.group(2) or "M").upper()

    # Validate size is not zero
    if size_num == 0:
        return False, "Device size cannot be zero", 0

    size_mb = {
        "K": max(1, size_num // 1024),  # Round up to at least 1MB
        "M": size_num,
        "G": size_num * 1024,
    }[size_unit]

    return True, "", size_mb


def create_null_blk_device(size: str, name: str) -> Tuple[Optional[str], Optional[int]]:
    """Create a null_blk device using configfs.

    null_blk provides memory-backed block devices for high-performance testing.
    Devices are created via the configfs interface for reliability.

    Args:
        size: Device size (e.g., "10G", "512M", "1024K")
        name: Device name for logging (not used in device path)

    Returns:
        (device_path, nullb_index) or (None, None) on failure

        device_path: Path to block device (e.g., "/dev/nullb0")
        nullb_index: Index for cleanup (0-1023)

    Example:
        >>> dev, idx = create_null_blk_device("10G", "test")
        >>> if dev:
        ...     print(f"Created {dev}")
        ...     # Use device...
        ...     cleanup_null_blk_device(dev, idx)

    See Also:
        - cleanup_null_blk_device: Cleanup function
        - check_null_blk_support: Availability check
        - create_loop_device: Alternative using loop devices
    """
    # Parse size
    valid, error, size_mb = _parse_size_to_mb(size)
    if not valid:
        logger.error(error)
        return None, None

    # Allocate index atomically
    idx = _allocate_null_blk_index()
    if idx is None:
        logger.error("Failed to allocate null_blk device index")
        return None, None

    device_dir = NULLB_CONFIGFS / f"nullb{idx}"
    device_path = f"/dev/nullb{idx}"

    try:
        # Set device attributes via configfs
        # Size in MB
        subprocess.run(
            ["sudo", "bash", "-c", f"echo {size_mb} > {device_dir}/size"],
            check=True,
            capture_output=True,
            timeout=5,
        )

        # Enable memory backing (required for filesystem testing)
        subprocess.run(
            ["sudo", "bash", "-c", f"echo 1 > {device_dir}/memory_backed"],
            check=True,
            capture_output=True,
            timeout=5,
        )

        # Set optimal performance parameters
        for param, value in [
            ("blocksize", "4096"),  # 4K blocks (modern standard)
            ("hw_queue_depth", "128"),  # Large queue for throughput
            ("irqmode", "0"),  # No IRQ overhead
            ("completion_nsec", "0"),  # Zero latency
        ]:
            try:
                subprocess.run(
                    ["sudo", "bash", "-c", f"echo {value} > {device_dir}/{param}"],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            except subprocess.CalledProcessError:
                # Some parameters might not be available on all kernels
                logger.debug(f"Could not set {param}={value} (kernel may not support it)")

        # Activate device by setting power=1
        subprocess.run(
            ["sudo", "bash", "-c", f"echo 1 > {device_dir}/power"],
            check=True,
            capture_output=True,
            timeout=5,
        )

        # Wait for device to appear
        for _ in range(50):  # 5 second timeout
            if Path(device_path).exists():
                break
            time.sleep(0.1)
        else:
            logger.error(f"Device {device_path} did not appear after activation")
            # Cleanup - deactivate first, then remove directory
            try:
                subprocess.run(
                    ["sudo", "bash", "-c", f"echo 0 > {device_dir}/power"],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
            subprocess.run(
                ["sudo", "rmdir", str(device_dir)],
                capture_output=True,
            )
            return None, None

        # Set permissions so current user can access (needed for QEMU)
        try:
            subprocess.run(
                ["sudo", "chmod", "666", device_path],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Failed to set permissions on {device_path}: {e.stderr.decode() if e.stderr else 'unknown error'}"
            )
            # Cleanup
            cleanup_null_blk_device(device_path, idx)
            return None, None

        logger.info(f"✓ Created null_blk device: {device_path} ({name}, {size})")
        return device_path, idx

    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to create null_blk device: {e.stderr.decode() if e.stderr else 'unknown error'}"
        )
        # Cleanup on failure - deactivate first, then remove directory
        try:
            subprocess.run(
                ["sudo", "bash", "-c", f"echo 0 > {device_dir}/power"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
        try:
            subprocess.run(
                ["sudo", "rmdir", str(device_dir)],
                capture_output=True,
            )
        except Exception:
            pass
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error creating null_blk device {name}: {e}")
        return None, None


def cleanup_null_blk_device(device_path: str, nullb_idx: int) -> bool:
    """Clean up a null_blk device.

    Args:
        device_path: Device path (e.g., "/dev/nullb0")
        nullb_idx: null_blk index (0-1023)

    Returns:
        True if cleanup succeeded, False otherwise
    """
    device_dir = NULLB_CONFIGFS / f"nullb{nullb_idx}"
    success = True

    try:
        # Deactivate device by setting power=0
        if device_dir.exists():
            try:
                subprocess.run(
                    ["sudo", "bash", "-c", f"echo 0 > {device_dir}/power"],
                    capture_output=True,
                    timeout=5,
                )
            except subprocess.CalledProcessError:
                logger.warning(f"Failed to deactivate {device_path}")
                success = False

            # Remove configfs directory
            try:
                subprocess.run(
                    ["sudo", "rmdir", str(device_dir)],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Failed to remove configfs directory {device_dir}: {e.stderr.decode() if e.stderr else 'unknown error'}"
                )
                success = False

        # Verify device is gone
        for _ in range(10):
            if not Path(device_path).exists():
                break
            time.sleep(0.1)
        else:
            logger.warning(f"Device {device_path} still exists after cleanup")
            success = False

        if success:
            logger.info(f"✓ Cleaned up null_blk device: {device_path}")

        return success

    except Exception as e:
        logger.error(f"Error cleaning up null_blk device {device_path}: {e}")
        return False


def cleanup_orphaned_null_blk_devices(staleness_seconds: int = 60) -> int:
    """Clean up orphaned null_blk devices from previous crashed sessions.

    Scans /sys/kernel/config/nullb/ for devices and removes any that exist
    and haven't been modified recently (to avoid race conditions with concurrent
    processes creating devices).

    Args:
        staleness_seconds: Only clean devices older than this many seconds (default: 60)
                          This prevents race conditions where multiple processes might
                          try to clean the same device, or clean devices being created.

    Returns:
        Number of devices cleaned up
    """
    if not NULLB_CONFIGFS.exists():
        return 0

    cleaned = 0
    current_time = time.time()

    try:
        for device_dir in NULLB_CONFIGFS.iterdir():
            if device_dir.is_dir() and device_dir.name.startswith("nullb"):
                try:
                    # Extract index from directory name
                    idx_match = re.match(r"nullb(\d+)", device_dir.name)
                    if not idx_match:
                        continue

                    idx = int(idx_match.group(1))
                    device_path = f"/dev/nullb{idx}"

                    # Check directory modification time for staleness
                    # This prevents race conditions with concurrent processes
                    try:
                        mtime = device_dir.stat().st_mtime
                        age_seconds = current_time - mtime

                        if age_seconds < staleness_seconds:
                            logger.debug(
                                f"Skipping {device_dir.name} (age: {age_seconds:.1f}s, "
                                f"threshold: {staleness_seconds}s)"
                            )
                            continue
                    except OSError:
                        # If we can't stat it, skip it (might be being deleted)
                        logger.debug(f"Cannot stat {device_dir.name}, skipping")
                        continue

                    # Try to cleanup stale device
                    if cleanup_null_blk_device(device_path, idx):
                        cleaned += 1
                        logger.info(
                            f"Cleaned up orphaned null_blk device: {device_path} (age: {age_seconds:.1f}s)"
                        )

                except Exception as e:
                    logger.warning(f"Failed to cleanup orphaned device {device_dir.name}: {e}")

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} orphaned null_blk device(s)")

    except Exception as e:
        logger.error(f"Error scanning for orphaned null_blk devices: {e}")

    return cleaned


def validate_block_device(
    path: str, readonly: bool = False, require_empty: bool = False
) -> Tuple[bool, str]:
    """Validate an existing block device is safe to use.

    Args:
        path: Device path (e.g., "/dev/nvme0n1p5")
        readonly: If True, allow mounted devices
        require_empty: If True, fail if device has filesystem

    Returns:
        (is_valid, error_message)

    Checks:
        - Device exists and is a block device
        - Not mounted (unless readonly=True)
        - No filesystem signature (if require_empty=True)
        - Not a whole disk device (unless readonly=True)
    """
    device_path = Path(path)

    # Check exists
    if not device_path.exists():
        return False, f"Device does not exist: {path}"

    # Check is block device
    try:
        if not stat.S_ISBLK(device_path.stat().st_mode):
            return False, f"Not a block device: {path}"
    except Exception as e:
        return False, f"Cannot stat device {path}: {e}"

    # Check if mounted
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            mount_point = result.stdout.strip()
            if mount_point and not readonly:
                return (
                    False,
                    f"Device {path} is mounted at {mount_point}. Unmount it first or use readonly=True.",
                )
    except Exception as e:
        logger.warning(f"Could not check if {path} is mounted: {e}")

    # Check for filesystem signature if required
    if require_empty:
        try:
            result = subprocess.run(
                ["sudo", "blkid", "-p", path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                fs_type = result.stdout
                return (
                    False,
                    f"Device {path} has filesystem signature: {fs_type.strip()}. Set require_empty=False to override.",
                )
        except Exception as e:
            logger.warning(f"Could not check filesystem signature on {path}: {e}")

    # Check not whole disk (unless readonly)
    # Patterns: /dev/sda, /dev/nvme0n1, /dev/vda, /dev/hda
    if re.match(r"^/dev/(sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z])$", path):
        if not readonly:
            return (
                False,
                f"Whole disk device '{path}' requires readonly=True for safety. Use a partition instead (e.g., {path}1) or set readonly=True.",
            )

    return True, ""
