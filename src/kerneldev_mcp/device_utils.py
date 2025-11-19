"""
Shared utilities for device operations across kerneldev-mcp.

This module consolidates device management functions that were previously
duplicated across boot_manager.py and device_manager.py.
"""

import logging
import re
import stat
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


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
