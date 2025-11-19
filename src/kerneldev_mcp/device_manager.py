"""
Device management for fstests - handles loop devices, filesystem creation, and mounting.
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

from .device_utils import create_loop_device, cleanup_loop_device


@dataclass
class DeviceConfig:
    """Configuration for a test or scratch device."""

    device_path: str
    mount_point: Path
    filesystem_type: str
    size: Optional[str] = None  # Size for loop devices (e.g., "10G")
    mount_options: Optional[str] = None
    mkfs_options: Optional[str] = None
    is_loop_device: bool = False
    backing_file: Optional[Path] = None  # For loop devices


@dataclass
class DeviceSetupResult:
    """Result of device setup operation."""

    success: bool
    test_device: Optional[DeviceConfig] = None
    scratch_device: Optional[DeviceConfig] = None
    pool_devices: Optional[List[DeviceConfig]] = None
    message: str = ""
    cleanup_needed: bool = False


class DeviceManager:
    """Manages test and scratch devices for fstests."""

    def __init__(self, work_dir: Optional[Path] = None):
        """Initialize device manager.

        Args:
            work_dir: Working directory for loop device images (default: /var/tmp/kerneldev-fstests)
        """
        self.work_dir = work_dir or Path("/var/tmp/kerneldev-fstests")
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Track created loop devices for cleanup
        self._created_loop_devices: List[str] = []
        self._created_mounts: List[Path] = []

    def find_free_loop_device(self) -> Optional[str]:
        """Find a free loop device.

        Returns:
            Path to free loop device or None if none available
        """
        try:
            result = subprocess.run(["losetup", "-f"], capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def create_loop_device(
        self, size: str, name: str = "device"
    ) -> Tuple[Optional[str], Optional[Path]]:
        """Create a loop device from an image file.

        Args:
            size: Size of device (e.g., "10G", "5G")
            name: Name for the image file

        Returns:
            Tuple of (loop_device_path, backing_file_path) or (None, None) on failure
        """
        # Delegate to device_utils
        loop_dev, backing_file = create_loop_device(size, name, self.work_dir)

        if loop_dev:
            # Track for cleanup
            self._created_loop_devices.append(loop_dev)

        return loop_dev, backing_file

    def validate_device(self, device_path: str) -> bool:
        """Validate that a device exists and is accessible.

        Args:
            device_path: Path to device

        Returns:
            True if device is valid
        """
        device = Path(device_path)

        # Check if device exists
        if not device.exists():
            return False

        # Check if it's a block device
        try:
            result = subprocess.run(["test", "-b", device_path], capture_output=True)
            return result.returncode == 0
        except subprocess.CalledProcessError:
            return False

    def get_device_size(self, device_path: str) -> Optional[int]:
        """Get device size in bytes.

        Args:
            device_path: Path to device

        Returns:
            Size in bytes or None on failure
        """
        try:
            result = subprocess.run(
                ["sudo", "blockdev", "--getsize64", device_path],
                capture_output=True,
                text=True,
                check=True,
            )
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return None

    def create_filesystem(
        self, device_path: str, fstype: str, mkfs_options: Optional[str] = None
    ) -> bool:
        """Create a filesystem on a device.

        Args:
            device_path: Path to device
            fstype: Filesystem type (ext4, btrfs, xfs, etc.)
            mkfs_options: Additional mkfs options

        Returns:
            True if successful
        """
        # Build mkfs command
        cmd = ["sudo", f"mkfs.{fstype}"]

        # Add options if provided
        if mkfs_options:
            cmd.extend(mkfs_options.split())

        # Force creation (overwrite existing)
        if fstype == "ext4":
            cmd.append("-F")
        elif fstype == "xfs":
            cmd.append("-f")
        elif fstype == "btrfs":
            cmd.append("-f")

        cmd.append(device_path)

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def mount_device(
        self, device_path: str, mount_point: Path, mount_options: Optional[str] = None
    ) -> bool:
        """Mount a device.

        Args:
            device_path: Path to device
            mount_point: Where to mount
            mount_options: Mount options

        Returns:
            True if successful
        """
        # Create mount point if it doesn't exist
        mount_point.mkdir(parents=True, exist_ok=True)

        # Build mount command
        cmd = ["sudo", "mount"]

        if mount_options:
            cmd.extend(["-o", mount_options])

        cmd.extend([device_path, str(mount_point)])

        try:
            subprocess.run(cmd, check=True, capture_output=True)

            # Track for cleanup
            self._created_mounts.append(mount_point)

            return True
        except subprocess.CalledProcessError:
            return False

    def umount_device(self, mount_point: Path) -> bool:
        """Unmount a device.

        Args:
            mount_point: Mount point to unmount

        Returns:
            True if successful
        """
        try:
            subprocess.run(["sudo", "umount", str(mount_point)], check=True, capture_output=True)

            # Remove from tracking
            if mount_point in self._created_mounts:
                self._created_mounts.remove(mount_point)

            return True
        except subprocess.CalledProcessError:
            return False

    def detach_loop_device(self, loop_device: str) -> bool:
        """Detach a loop device.

        Args:
            loop_device: Path to loop device

        Returns:
            True if successful
        """
        # Delegate to device_utils (without backing file cleanup, that's handled separately)
        success = cleanup_loop_device(loop_device, None)

        # Remove from tracking
        if loop_device in self._created_loop_devices:
            self._created_loop_devices.remove(loop_device)

        return success

    def setup_loop_devices(
        self,
        test_size: str = "10G",
        scratch_size: str = "10G",
        fstype: str = "ext4",
        test_mount: Optional[Path] = None,
        scratch_mount: Optional[Path] = None,
        mkfs_options: Optional[str] = None,
        mount_options: Optional[str] = None,
        pool_count: int = 0,
        pool_size: str = "10G",
    ) -> DeviceSetupResult:
        """Setup test and scratch devices using loop devices, optionally with pool devices.

        Args:
            test_size: Size of test device
            scratch_size: Size of scratch device
            fstype: Filesystem type
            test_mount: Mount point for test device (default: /mnt/test)
            scratch_mount: Mount point for scratch device (default: /mnt/scratch)
            mkfs_options: Options for mkfs
            mount_options: Options for mount
            pool_count: Number of pool devices to create for SCRATCH_DEV_POOL (default: 0)
            pool_size: Size of each pool device (default: 10G)

        Returns:
            DeviceSetupResult with device configurations including pool devices
        """
        test_mount = test_mount or Path("/mnt/test")
        scratch_mount = scratch_mount or Path("/mnt/scratch")

        # Create test device
        test_dev, test_backing = self.create_loop_device(test_size, "test")
        if not test_dev:
            return DeviceSetupResult(success=False, message="Failed to create test loop device")

        # Create scratch device
        scratch_dev, scratch_backing = self.create_loop_device(scratch_size, "scratch")
        if not scratch_dev:
            # Cleanup test device
            self.detach_loop_device(test_dev)
            return DeviceSetupResult(success=False, message="Failed to create scratch loop device")

        # Format test device
        if not self.create_filesystem(test_dev, fstype, mkfs_options):
            self.cleanup_all()
            return DeviceSetupResult(
                success=False, message=f"Failed to create {fstype} filesystem on test device"
            )

        # Mount test device
        if not self.mount_device(test_dev, test_mount, mount_options):
            self.cleanup_all()
            return DeviceSetupResult(success=False, message="Failed to mount test device")

        # Note: We don't format or mount scratch device - fstests does that

        test_config = DeviceConfig(
            device_path=test_dev,
            mount_point=test_mount,
            filesystem_type=fstype,
            size=test_size,
            mount_options=mount_options,
            mkfs_options=mkfs_options,
            is_loop_device=True,
            backing_file=test_backing,
        )

        scratch_config = DeviceConfig(
            device_path=scratch_dev,
            mount_point=scratch_mount,
            filesystem_type=fstype,
            size=scratch_size,
            mount_options=mount_options,
            mkfs_options=mkfs_options,
            is_loop_device=True,
            backing_file=scratch_backing,
        )

        # Create pool devices if requested
        pool_configs = []
        if pool_count > 0:
            for i in range(pool_count):
                pool_dev, pool_backing = self.create_loop_device(pool_size, f"pool{i + 1}")
                if not pool_dev:
                    self.cleanup_all()
                    return DeviceSetupResult(
                        success=False, message=f"Failed to create pool device {i + 1}/{pool_count}"
                    )

                # Pool devices are NOT formatted - tests format them as needed
                pool_config = DeviceConfig(
                    device_path=pool_dev,
                    mount_point=Path("/mnt"),  # Not mounted
                    filesystem_type=fstype,
                    size=pool_size,
                    mount_options=mount_options,
                    mkfs_options=mkfs_options,
                    is_loop_device=True,
                    backing_file=pool_backing,
                )
                pool_configs.append(pool_config)

        message = f"Successfully setup loop devices: test={test_dev}, scratch={scratch_dev}"
        if pool_configs:
            pool_devs = ", ".join([pc.device_path for pc in pool_configs])
            message += f", pool=[{pool_devs}]"

        return DeviceSetupResult(
            success=True,
            test_device=test_config,
            scratch_device=scratch_config,
            pool_devices=pool_configs if pool_configs else None,
            message=message,
            cleanup_needed=True,
        )

    def setup_existing_devices(
        self,
        test_dev: str,
        scratch_dev: str,
        fstype: str,
        test_mount: Optional[Path] = None,
        scratch_mount: Optional[Path] = None,
        format_test: bool = True,
        mkfs_options: Optional[str] = None,
        mount_options: Optional[str] = None,
        pool_devs: Optional[List[str]] = None,
    ) -> DeviceSetupResult:
        """Setup test and scratch devices using existing devices, optionally with pool devices.

        Args:
            test_dev: Path to test device
            scratch_dev: Path to scratch device
            fstype: Filesystem type
            test_mount: Mount point for test device
            scratch_mount: Mount point for scratch device
            format_test: Whether to format test device
            mkfs_options: Options for mkfs
            mount_options: Options for mount
            pool_devs: List of pool device paths for SCRATCH_DEV_POOL (optional)

        Returns:
            DeviceSetupResult with device configurations including pool devices
        """
        test_mount = test_mount or Path("/mnt/test")
        scratch_mount = scratch_mount or Path("/mnt/scratch")

        # Validate test device
        if not self.validate_device(test_dev):
            return DeviceSetupResult(
                success=False, message=f"Test device {test_dev} is not valid or doesn't exist"
            )

        # Validate scratch device
        if not self.validate_device(scratch_dev):
            return DeviceSetupResult(
                success=False, message=f"Scratch device {scratch_dev} is not valid or doesn't exist"
            )

        # Format test device if requested
        if format_test:
            if not self.create_filesystem(test_dev, fstype, mkfs_options):
                return DeviceSetupResult(
                    success=False, message=f"Failed to create {fstype} filesystem on test device"
                )

        # Mount test device
        if not self.mount_device(test_dev, test_mount, mount_options):
            return DeviceSetupResult(success=False, message="Failed to mount test device")

        test_config = DeviceConfig(
            device_path=test_dev,
            mount_point=test_mount,
            filesystem_type=fstype,
            mount_options=mount_options,
            mkfs_options=mkfs_options,
            is_loop_device=False,
        )

        scratch_config = DeviceConfig(
            device_path=scratch_dev,
            mount_point=scratch_mount,
            filesystem_type=fstype,
            mount_options=mount_options,
            mkfs_options=mkfs_options,
            is_loop_device=False,
        )

        # Validate and setup pool devices if provided
        pool_configs = []
        if pool_devs:
            for i, pool_dev in enumerate(pool_devs):
                if not self.validate_device(pool_dev):
                    return DeviceSetupResult(
                        success=False,
                        message=f"Pool device {pool_dev} is not valid or doesn't exist",
                    )

                # Pool devices are not formatted or mounted
                pool_config = DeviceConfig(
                    device_path=pool_dev,
                    mount_point=Path("/mnt"),  # Not mounted
                    filesystem_type=fstype,
                    mount_options=mount_options,
                    mkfs_options=mkfs_options,
                    is_loop_device=False,
                )
                pool_configs.append(pool_config)

        message = f"Successfully setup existing devices: test={test_dev}, scratch={scratch_dev}"
        if pool_configs:
            pool_paths = ", ".join([pc.device_path for pc in pool_configs])
            message += f", pool=[{pool_paths}]"

        return DeviceSetupResult(
            success=True,
            test_device=test_config,
            scratch_device=scratch_config,
            pool_devices=pool_configs if pool_configs else None,
            message=message,
            cleanup_needed=False,
        )

    def cleanup_all(self):
        """Cleanup all created mounts and loop devices."""
        # Unmount all created mounts
        for mount_point in list(self._created_mounts):
            self.umount_device(mount_point)

        # Detach all created loop devices
        for loop_dev in list(self._created_loop_devices):
            self.detach_loop_device(loop_dev)

        # Remove backing files
        if self.work_dir.exists():
            for img_file in self.work_dir.glob("*.img"):
                try:
                    img_file.unlink()
                except OSError:
                    pass
