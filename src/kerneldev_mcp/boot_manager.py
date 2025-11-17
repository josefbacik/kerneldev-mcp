"""
Kernel boot testing and validation using virtme-ng.
"""

import asyncio
import datetime
import logging
import os
import pty
import random
import re
import select
import signal
import string
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .device_pool import VolumeConfig, allocate_pool_volumes, release_pool_volumes

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .config_manager import CrossCompileConfig


@dataclass
class DmesgMessage:
    """Represents a single dmesg message."""

    timestamp: Optional[float]  # Seconds since boot
    level: str  # emerg, alert, crit, err, warn, notice, info, debug
    subsystem: Optional[str]  # e.g., "BTRFS", "EXT4", etc.
    message: str

    def __str__(self) -> str:
        if self.timestamp is not None:
            return f"[{self.timestamp:>8.6f}] {self.level}: {self.message}"
        return f"{self.level}: {self.message}"


@dataclass
class BootResult:
    """Result of a kernel boot test."""

    success: bool
    duration: float  # seconds
    boot_completed: bool  # Did boot complete successfully
    kernel_version: Optional[str] = None

    # Dmesg analysis
    errors: List[DmesgMessage] = field(default_factory=list)
    warnings: List[DmesgMessage] = field(default_factory=list)
    panics: List[DmesgMessage] = field(default_factory=list)
    oops: List[DmesgMessage] = field(default_factory=list)

    # Full dmesg output
    dmesg_output: str = ""

    # Execution details
    exit_code: int = 0
    timeout_occurred: bool = False
    log_file_path: Optional[Path] = None  # Path to saved boot log
    progress_log: List[str] = field(default_factory=list)  # Progress messages during execution

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def panic_count(self) -> int:
        return len(self.panics)

    @property
    def oops_count(self) -> int:
        return len(self.oops)

    @property
    def has_critical_issues(self) -> bool:
        """Check if boot had critical issues (panics or oops)."""
        return len(self.panics) > 0 or len(self.oops) > 0

    def summary(self) -> str:
        """Get a human-readable summary."""
        if not self.boot_completed:
            return f"✗ Boot failed or timed out after {self.duration:.1f}s"

        if self.has_critical_issues:
            return f"✗ Boot completed with CRITICAL issues: {self.panic_count} panics, {self.oops_count} oops"

        if self.error_count > 0:
            return f"⚠ Boot completed with {self.error_count} errors, {self.warning_count} warnings"

        if self.warning_count > 0:
            return f"✓ Boot successful with {self.warning_count} warnings ({self.duration:.1f}s)"

        return f"✓ Boot successful, no issues detected ({self.duration:.1f}s)"


# Resource limits for custom device attachment
MAX_CUSTOM_DEVICES = 20
MAX_DEVICE_SIZE_GB = 100
MAX_TMPFS_TOTAL_GB = 50


def _parse_device_size_to_gb(size: str) -> Tuple[bool, str, float]:
    """Parse device size string to GB.

    Args:
        size: Size string (e.g., "10G", "512M", "1024K")

    Returns:
        Tuple of (is_valid, error_message, size_in_gb)
    """
    match = re.match(r"^(\d+)(G|M|K)?$", size, re.IGNORECASE)
    if not match:
        return False, f"Invalid size format: {size}. Use format like '10G', '512M'", 0.0

    size_num = int(match.group(1))
    size_unit = (match.group(2) or "M").upper()

    size_gb = {"K": size_num / (1024 * 1024), "M": size_num / 1024, "G": size_num}[size_unit]

    return True, "", size_gb


@dataclass
class DeviceSpec:
    """Specification for a device to attach to VM.

    Devices appear in VM as /dev/vda, /dev/vdb, etc. in order specified.
    virtme-ng auto-assigns device names; use 'order' parameter to control ordering.

    Examples:
        DeviceSpec(size="10G", name="test", env_var="TEST_DEV")
        DeviceSpec(path="/dev/nvme0n1p5", readonly=True)
    """

    # Device source (mutually exclusive)
    path: Optional[str] = None  # Existing block device path
    size: Optional[str] = None  # Size for loop device creation (e.g., "10G")

    # Device configuration
    name: Optional[str] = None  # Descriptive name for logging
    order: int = 0  # Order in device list (lower = earlier)
    use_tmpfs: bool = False  # Use tmpfs backing for loop device

    # VM environment
    env_var: Optional[str] = None  # Export as env var (e.g., "TEST_DEV")
    env_var_index: Optional[int] = None  # Device index for env var (e.g., /dev/vda = index 0)

    # Safety options
    readonly: bool = False  # Attach as read-only device
    require_empty: bool = False  # Fail if device has filesystem signature

    def validate(self) -> Tuple[bool, str]:
        """Validate device specification.

        Returns:
            (is_valid, error_message)
        """
        import stat

        if (self.path is None) == (self.size is None):
            return False, "Exactly one of 'path' or 'size' must be specified"

        if self.size:
            valid, error, size_gb = _parse_device_size_to_gb(self.size)
            if not valid:
                return False, error

            if size_gb > MAX_DEVICE_SIZE_GB:
                return False, f"Device size {self.size} exceeds maximum {MAX_DEVICE_SIZE_GB}G"

        if self.path:
            device_path = Path(self.path)
            if not device_path.exists():
                return False, f"Device does not exist: {self.path}"

            try:
                if not stat.S_ISBLK(device_path.stat().st_mode):
                    return False, f"Not a block device: {self.path}"
            except Exception as e:
                return False, f"Cannot stat device {self.path}: {e}"

            # Safety: disallow whole disk devices without readonly
            # Patterns: /dev/sda, /dev/nvme0n1, /dev/vda, /dev/hda
            if re.match(r"^/dev/(sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z])$", self.path):
                if not self.readonly:
                    return False, (
                        f"Whole disk device '{self.path}' requires readonly=True for safety. "
                        f"Use a partition instead (e.g., {self.path}1) or set readonly=True."
                    )

        return True, ""


@dataclass
class DeviceProfile:
    """Predefined device configurations for common use cases."""

    name: str
    description: str
    devices: List[DeviceSpec]

    @staticmethod
    def get_profile(name: str, use_tmpfs: bool = False) -> Optional["DeviceProfile"]:
        """Get a predefined device profile.

        Args:
            name: Profile name
            use_tmpfs: Override use_tmpfs setting for all devices

        Returns:
            DeviceProfile or None if not found
        """
        base_devices = [
            DeviceSpec(size="10G", name="test", env_var="TEST_DEV", order=0),
            DeviceSpec(size="10G", name="pool1", order=1),
            DeviceSpec(size="10G", name="pool2", order=2),
            DeviceSpec(size="10G", name="pool3", order=3),
            DeviceSpec(size="10G", name="pool4", order=4),
            DeviceSpec(size="10G", name="pool5", order=5),
            DeviceSpec(size="10G", name="logwrites", env_var="LOGWRITES_DEV", order=6),
        ]

        profiles = {
            "fstests_default": DeviceProfile(
                name="fstests_default",
                description="Default 7 devices for fstests (1 TEST + 5 POOL + 1 LOGWRITES)",
                devices=[
                    DeviceSpec(
                        size=d.size,
                        name=d.name,
                        env_var=d.env_var,
                        order=d.order,
                        use_tmpfs=use_tmpfs,
                    )
                    for d in base_devices
                ],
            ),
            "fstests_small": DeviceProfile(
                name="fstests_small",
                description="Smaller fstests devices (5G each) for faster setup",
                devices=[
                    DeviceSpec(
                        size="5G",
                        name=d.name,
                        env_var=d.env_var,
                        order=d.order,
                        use_tmpfs=use_tmpfs,
                    )
                    for d in base_devices
                ],
            ),
            "fstests_large": DeviceProfile(
                name="fstests_large",
                description="Larger fstests devices (50G each) for extensive testing",
                devices=[
                    DeviceSpec(
                        size="50G",
                        name=d.name,
                        env_var=d.env_var,
                        order=d.order,
                        use_tmpfs=use_tmpfs,
                    )
                    for d in base_devices
                ],
            ),
        }
        return profiles.get(name)

    @staticmethod
    def list_profiles() -> List[Tuple[str, str]]:
        """List available profiles.

        Returns:
            List of (name, description) tuples
        """
        return [
            ("fstests_default", "Default 7 devices for fstests (10G each)"),
            ("fstests_small", "Smaller devices (5G each) for faster setup"),
            ("fstests_large", "Larger devices (50G each) for extensive testing"),
        ]


class VMDeviceManager:
    """Manages device setup and cleanup for VM boots.

    Integrates with existing loop device infrastructure to provide
    flexible device attachment for VMs.
    """

    def __init__(self):
        self.created_loop_devices: List[Tuple[str, Optional[Path]]] = []
        self.attached_block_devices: List[str] = []
        self.device_specs: List[DeviceSpec] = []
        self.tmpfs_setup = False

    async def setup_devices(self, device_specs: List[DeviceSpec]) -> Tuple[bool, str, List[str]]:
        """Setup devices from specifications.

        Args:
            device_specs: List of DeviceSpec to setup

        Returns:
            (success, error_message, device_paths_in_order)
        """
        if len(device_specs) > MAX_CUSTOM_DEVICES:
            return (
                False,
                f"Too many devices: {len(device_specs)} exceeds maximum {MAX_CUSTOM_DEVICES}",
                [],
            )

        for i, spec in enumerate(device_specs):
            valid, error = spec.validate()
            if not valid:
                return False, f"Device {i} ({spec.name or 'unnamed'}): {error}", []

        tmpfs_total = 0.0
        for spec in device_specs:
            if spec.use_tmpfs and spec.size:
                valid, _, size_gb = _parse_device_size_to_gb(spec.size)
                if valid:
                    tmpfs_total += size_gb

        if tmpfs_total > MAX_TMPFS_TOTAL_GB:
            return (
                False,
                f"Total tmpfs size {tmpfs_total:.1f}G exceeds maximum {MAX_TMPFS_TOTAL_GB}G",
                [],
            )

        self.device_specs = sorted(device_specs, key=lambda s: s.order)
        device_paths = []

        try:
            if any(spec.use_tmpfs for spec in self.device_specs if spec.size):
                if not _setup_tmpfs_for_loop_devices():
                    return False, "Failed to setup tmpfs for loop devices", []
                self.tmpfs_setup = True
                logger.info(f"✓ Setup tmpfs at {HOST_LOOP_TMPFS_DIR}")

            for spec in self.device_specs:
                if spec.path:
                    success, error, device_path = await self._validate_existing_device(spec)
                    if not success:
                        await self.cleanup()
                        return False, error, []
                    device_paths.append(device_path)
                    self.attached_block_devices.append(device_path)
                    logger.info(
                        f"✓ Validated existing device: {device_path} ({spec.name or 'unnamed'})"
                    )

                elif spec.size:
                    backing_dir = (
                        HOST_LOOP_TMPFS_DIR if (spec.use_tmpfs and self.tmpfs_setup) else None
                    )
                    loop_dev, backing_file = _create_host_loop_device(
                        spec.size,
                        spec.name or f"custom-{len(self.created_loop_devices)}",
                        backing_dir,
                    )
                    if not loop_dev:
                        await self.cleanup()
                        return (
                            False,
                            f"Failed to create loop device for {spec.name or 'unnamed'}",
                            [],
                        )

                    self.created_loop_devices.append((loop_dev, backing_file))
                    device_paths.append(loop_dev)
                    logger.info(
                        f"✓ Created loop device: {loop_dev} ({spec.name or 'unnamed'}, {spec.size})"
                    )

            return True, "", device_paths

        except Exception as e:
            await self.cleanup()
            return False, f"Device setup failed: {str(e)}", []

    async def _validate_existing_device(self, spec: DeviceSpec) -> Tuple[bool, str, str]:
        """Validate existing device is safe to use.

        Args:
            spec: DeviceSpec for existing device

        Returns:
            (success, error_message, device_path)
        """
        device_path = spec.path

        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "TARGET", device_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                mount_point = result.stdout.strip()
                if mount_point and not spec.readonly:
                    return (
                        False,
                        (
                            f"Device {device_path} is mounted at {mount_point}. "
                            f"Unmount it first or use readonly=True."
                        ),
                        "",
                    )
        except Exception as e:
            logger.warning(f"Could not check if {device_path} is mounted: {e}")

        if spec.require_empty:
            try:
                result = subprocess.run(
                    ["sudo", "blkid", "-p", device_path], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    fs_type = result.stdout
                    return (
                        False,
                        (
                            f"Device {device_path} has filesystem signature: {fs_type.strip()}. "
                            f"Set require_empty=False to override."
                        ),
                        "",
                    )
            except Exception as e:
                logger.warning(f"Could not check filesystem signature on {device_path}: {e}")

        try:
            with open(device_path, "rb") as f:
                f.read(512)
            logger.debug(f"✓ Can access {device_path} directly")
        except PermissionError:
            try:
                result = subprocess.run(
                    ["sudo", "-n", "dd", f"if={device_path}", "of=/dev/null", "count=1", "bs=512"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    return (
                        False,
                        (
                            f"No permission to access {device_path}. "
                            f"Run with appropriate permissions."
                        ),
                        "",
                    )
                logger.warning(f"⚠ Can access {device_path} only with sudo")
            except Exception as e:
                return False, f"Cannot access {device_path}: {str(e)}", ""
        except Exception as e:
            return False, f"Cannot read from {device_path}: {str(e)}", ""

        return True, "", device_path

    def cleanup(self):
        """Cleanup all created devices."""
        for loop_dev, backing_file in self.created_loop_devices:
            _cleanup_host_loop_device(loop_dev, backing_file)

        if self.tmpfs_setup:
            _cleanup_tmpfs_for_loop_devices()

        self.created_loop_devices = []
        self.attached_block_devices = []
        self.device_specs = []
        self.tmpfs_setup = False

    def get_vng_disk_args(self) -> List[str]:
        """Get --disk arguments for vng command.

        Devices will appear in VM as /dev/vda, /dev/vdb, /dev/vdc...
        in the order they are added here (respecting order parameter).

        Returns:
            List of arguments to pass to vng (e.g., ["--disk", "/dev/loop0", "--disk", "/dev/loop1"])
        """
        args = []
        all_devices = [d for d, _ in self.created_loop_devices] + self.attached_block_devices

        for device in all_devices:
            args.extend(["--disk", device])

        return args

    def get_vm_env_script(self) -> str:
        """Get bash script to export environment variables in VM.

        Returns:
            Bash script snippet to export env vars
        """
        script_lines = []
        script_lines.append("# Device environment variables")

        for i, spec in enumerate(self.device_specs):
            if spec.env_var:
                index = spec.env_var_index if spec.env_var_index is not None else i
                vm_dev = f"/dev/vd{chr(ord('a') + index)}"
                script_lines.append(f"export {spec.env_var}={vm_dev}")

        return "\n".join(script_lines) if len(script_lines) > 1 else ""


class DmesgParser:
    """Parse and analyze dmesg output."""

    LOG_LEVELS = {
        0: "emerg",  # System is unusable
        1: "alert",  # Action must be taken immediately
        2: "crit",  # Critical conditions
        3: "err",  # Error conditions
        4: "warn",  # Warning conditions
        5: "notice",  # Normal but significant
        6: "info",  # Informational
        7: "debug",
    }

    PANIC_PATTERNS = [
        re.compile(r"Kernel panic", re.IGNORECASE),
        re.compile(r"BUG: unable to handle", re.IGNORECASE),
        re.compile(r"general protection fault", re.IGNORECASE),
    ]

    OOPS_PATTERNS = [
        re.compile(r"BUG:", re.IGNORECASE),
        re.compile(r"Oops:", re.IGNORECASE),
        re.compile(r"unable to handle kernel", re.IGNORECASE),
    ]

    ERROR_PATTERNS = [
        re.compile(r"\berror\b", re.IGNORECASE),
        re.compile(r"\bfailed\b", re.IGNORECASE),
        re.compile(r"\bfailure\b", re.IGNORECASE),
    ]

    ERROR_EXCLUSIONS = [
        re.compile(r"ignoring", re.IGNORECASE),  # "failed...ignoring" is not an error
        re.compile(
            r"virtme-ng-init:.*(?:Failed|Permission denied)", re.IGNORECASE
        ),  # userspace init issues
        re.compile(
            r"PCI: Fatal: No config space access function found", re.IGNORECASE
        ),  # expected in virtme
        re.compile(r"Permission denied", re.IGNORECASE),  # userspace permission issues
        re.compile(
            r"Failed to read.*tmpfiles\.d", re.IGNORECASE
        ),  # systemd-tmpfile userspace issues
        re.compile(
            r"Failed to create directory.*Permission denied", re.IGNORECASE
        ),  # userspace directory creation
        re.compile(r"Failed to opendir\(\)", re.IGNORECASE),  # userspace directory access
    ]

    WARNING_PATTERNS = [
        re.compile(r"\bwarning\b", re.IGNORECASE),
        re.compile(r"\bWARN", re.IGNORECASE),
    ]

    USERSPACE_PREFIXES = [
        "virtme-ng-init:",
        "systemd-tmpfile",
    ]

    @staticmethod
    def parse_dmesg_line(line: str) -> Optional[DmesgMessage]:
        """Parse a single dmesg line.

        Supports multiple formats:
        - [timestamp] message
        - <level>message
        - [timestamp] subsystem: message
        """
        line = line.strip()
        if not line:
            return None

        timestamp = None
        level = "info"
        subsystem = None
        message = line

        # Try to parse timestamp: [12.345678]
        timestamp_match = re.match(r"\[\s*(\d+\.\d+)\]\s*(.*)", line)
        if timestamp_match:
            timestamp = float(timestamp_match.group(1))
            message = timestamp_match.group(2)

        # Try to parse log level: <3> or similar
        level_match = re.match(r"<(\d)>\s*(.*)", message)
        if level_match:
            level_num = int(level_match.group(1))
            level = DmesgParser.LOG_LEVELS.get(level_num, "info")
            message = level_match.group(2)

        # Try to parse subsystem: SUBSYSTEM: message
        subsystem_match = re.match(r"([A-Z][A-Z0-9_]+):\s*(.*)", message)
        if subsystem_match:
            subsystem = subsystem_match.group(1)
            message = subsystem_match.group(2)

        # Classify by content if level not explicitly set
        if level == "info":
            for pattern in DmesgParser.PANIC_PATTERNS:
                if pattern.search(message):
                    level = "emerg"
                    break

            if level == "info":
                for pattern in DmesgParser.OOPS_PATTERNS:
                    if pattern.search(message):
                        level = "crit"
                        break

            if level == "info":
                is_error = False
                for pattern in DmesgParser.ERROR_PATTERNS:
                    if pattern.search(message):
                        is_excluded = any(
                            excl.search(message) for excl in DmesgParser.ERROR_EXCLUSIONS
                        )
                        if not is_excluded:
                            is_error = True
                            break

                if is_error:
                    level = "err"

            if level == "info":
                for pattern in DmesgParser.WARNING_PATTERNS:
                    if pattern.search(message):
                        level = "warn"
                        break

        return DmesgMessage(timestamp=timestamp, level=level, subsystem=subsystem, message=message)

    @staticmethod
    def analyze_dmesg(
        dmesg_text: str,
    ) -> Tuple[List[DmesgMessage], List[DmesgMessage], List[DmesgMessage], List[DmesgMessage]]:
        """Analyze dmesg output and categorize messages.

        Returns:
            Tuple of (errors, warnings, panics, oops)
        """
        errors = []
        warnings = []
        panics = []
        oops = []

        for line in dmesg_text.splitlines():
            if any(prefix in line for prefix in DmesgParser.USERSPACE_PREFIXES):
                continue

            stripped = line.strip()
            if stripped and not stripped.startswith("[") and not stripped.startswith("<"):
                continue

            msg = DmesgParser.parse_dmesg_line(line)
            if not msg:
                continue

            for pattern in DmesgParser.PANIC_PATTERNS:
                if pattern.search(msg.message):
                    panics.append(msg)
                    break

            for pattern in DmesgParser.OOPS_PATTERNS:
                if pattern.search(msg.message):
                    oops.append(msg)
                    break

            if msg.level in ("emerg", "alert", "crit", "err"):
                errors.append(msg)
            elif msg.level == "warn":
                warnings.append(msg)

        return errors, warnings, panics, oops


# Boot log management
BOOT_LOG_DIR = Path("/tmp/kerneldev-boot-logs")

# Host loop device management for fstests
HOST_LOOP_WORK_DIR = Path("/var/tmp/kerneldev-loop-devices")
HOST_LOOP_TMPFS_DIR = Path("/var/tmp/kerneldev-loop-tmpfs")
# tmpfs size: 7 devices x 10G each + overhead
TMPFS_SIZE_GB = 80

# PID tracking for launched VMs (so we can kill only our VMs)
# Use server PID in filename so each MCP server instance has its own tracking file
# This prevents multiple Claude sessions from killing each other's VMs
_MCP_SERVER_PID = os.getpid()
VM_PID_TRACKING_FILE = Path(f"/tmp/kerneldev-mcp-vm-pids-{_MCP_SERVER_PID}.json")


def _track_vm_process(
    pid: int, pgid: int, description: str = "", log_file_path: Optional[Path] = None
):
    """Track a VM process so it can be killed later if needed.

    Args:
        pid: Process ID
        pgid: Process group ID
        description: Human-readable description (e.g., "fstests on kernel X.Y.Z")
        log_file_path: Path to the log file where VM output is being written
    """
    import json
    import time

    # Read existing tracking data
    tracking_data = {}
    if VM_PID_TRACKING_FILE.exists():
        try:
            with open(VM_PID_TRACKING_FILE, "r") as f:
                tracking_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            tracking_data = {}

    tracking_data[str(pid)] = {
        "pid": pid,
        "pgid": pgid,
        "description": description,
        "started_at": time.time(),
        "log_file_path": str(log_file_path) if log_file_path else None,
    }

    try:
        with open(VM_PID_TRACKING_FILE, "w") as f:
            json.dump(tracking_data, f, indent=2)
    except OSError as e:
        logger.warning(f"Failed to track VM process {pid}: {e}")


def _untrack_vm_process(pid: int):
    """Remove a VM process from tracking (called when process exits).

    Args:
        pid: Process ID to untrack
    """
    import json

    if not VM_PID_TRACKING_FILE.exists():
        return

    try:
        with open(VM_PID_TRACKING_FILE, "r") as f:
            tracking_data = json.load(f)

        if str(pid) in tracking_data:
            del tracking_data[str(pid)]

        if tracking_data:
            with open(VM_PID_TRACKING_FILE, "w") as f:
                json.dump(tracking_data, f, indent=2)
        else:
            VM_PID_TRACKING_FILE.unlink()
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to untrack VM process {pid}: {e}")


def _get_tracked_vm_processes() -> Dict[int, Dict[str, any]]:
    """Get all currently tracked VM processes.

    Returns:
        Dictionary mapping PID to process info
    """
    import json

    if not VM_PID_TRACKING_FILE.exists():
        return {}

    try:
        with open(VM_PID_TRACKING_FILE, "r") as f:
            tracking_data = json.load(f)

        # Convert string keys back to ints and filter out dead processes
        result = {}
        for pid_str, info in tracking_data.items():
            pid = int(pid_str)
            # Check if process is still alive
            try:
                os.kill(pid, 0)  # Signal 0 checks if process exists
                result[pid] = info
            except (OSError, ProcessLookupError):
                # Process is dead, skip it (will be cleaned up next time)
                pass

        return result
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _cleanup_dead_tracked_processes():
    """Remove dead processes from tracking file."""
    import json

    if not VM_PID_TRACKING_FILE.exists():
        return

    try:
        with open(VM_PID_TRACKING_FILE, "r") as f:
            tracking_data = json.load(f)

        # Filter out dead processes
        alive_data = {}
        for pid_str, info in tracking_data.items():
            try:
                pid = int(pid_str)
                os.kill(pid, 0)  # Check if alive
                alive_data[pid_str] = info
            except (OSError, ProcessLookupError, ValueError):
                pass

        # Write back or delete if empty
        if alive_data:
            with open(VM_PID_TRACKING_FILE, "w") as f:
                json.dump(alive_data, f, indent=2)
        else:
            VM_PID_TRACKING_FILE.unlink()
    except (json.JSONDecodeError, OSError):
        pass


def _setup_tmpfs_for_loop_devices() -> bool:
    """Setup tmpfs mount for loop device backing files.

    Returns:
        True if tmpfs was successfully mounted, False otherwise
    """
    # Create mount point if it doesn't exist
    HOST_LOOP_TMPFS_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already mounted
    result = subprocess.run(["mountpoint", "-q", str(HOST_LOOP_TMPFS_DIR)], capture_output=True)
    if result.returncode == 0:
        logger.info(f"✓ tmpfs already mounted at {HOST_LOOP_TMPFS_DIR}")
        return True

    # Mount tmpfs with size limit for 7 devices (sparse files)
    try:
        subprocess.run(
            [
                "sudo",
                "mount",
                "-t",
                "tmpfs",
                "-o",
                f"size={TMPFS_SIZE_GB}G",
                "tmpfs",
                str(HOST_LOOP_TMPFS_DIR),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"✓ Mounted tmpfs at {HOST_LOOP_TMPFS_DIR} (size={TMPFS_SIZE_GB}G)")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"✗ Failed to mount tmpfs: {e.stderr}")
        return False


def _cleanup_tmpfs_for_loop_devices() -> bool:
    """Cleanup tmpfs mount for loop device backing files.

    Returns:
        True if tmpfs was successfully unmounted, False otherwise
    """
    # Check if mounted
    result = subprocess.run(["mountpoint", "-q", str(HOST_LOOP_TMPFS_DIR)], capture_output=True)
    if result.returncode != 0:
        logger.info(f"tmpfs not mounted at {HOST_LOOP_TMPFS_DIR}, nothing to cleanup")
        return True

    # Unmount tmpfs
    try:
        subprocess.run(
            ["sudo", "umount", str(HOST_LOOP_TMPFS_DIR)], check=True, capture_output=True, text=True
        )
        logger.info(f"✓ Unmounted tmpfs from {HOST_LOOP_TMPFS_DIR}")

        # Remove directory if empty
        try:
            HOST_LOOP_TMPFS_DIR.rmdir()
        except OSError as e:
            logger.debug(f"Could not remove tmpfs directory: {e}")

        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"✗ Failed to unmount tmpfs: {e.stderr}")
        return False


def _create_host_loop_device(
    size: str, name: str, backing_dir: Optional[Path] = None
) -> Tuple[Optional[str], Optional[Path]]:
    """Create a loop device on the host for passing to VM.

    Args:
        size: Size of device (e.g., "10G")
        name: Name for the backing file
        backing_dir: Optional directory for backing files (defaults to HOST_LOOP_WORK_DIR)

    Returns:
        Tuple of (loop_device_path, backing_file_path) or (None, None) on failure
    """
    work_dir = backing_dir if backing_dir else HOST_LOOP_WORK_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    backing_file = work_dir / f"{name}.img"

    try:
        # Create sparse file
        subprocess.run(
            ["truncate", "-s", size, str(backing_file)], check=True, capture_output=True, text=True
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
                ["sudo", "chmod", "666", loop_dev], check=True, capture_output=True, text=True
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


def _get_available_schedulers(device: str) -> Optional[List[str]]:
    """Get available IO schedulers for a block device.

    Args:
        device: Block device path (e.g., "/dev/loop0")

    Returns:
        List of available scheduler names, or None on error
    """
    # Extract device name (e.g., "loop0" from "/dev/loop0")
    device_name = Path(device).name
    scheduler_file = Path(f"/sys/block/{device_name}/queue/scheduler")

    if not scheduler_file.exists():
        logger.warning(f"Scheduler file not found for {device}: {scheduler_file}")
        return None

    try:
        content = scheduler_file.read_text().strip()
        # Format is like: "[none] mq-deadline kyber bfq"
        # Extract all schedulers (remove brackets from current one)
        schedulers = []
        for sched in content.split():
            schedulers.append(sched.strip("[]"))
        return schedulers
    except Exception as e:
        logger.warning(f"Failed to read schedulers for {device}: {e}")
        return None


def _set_io_scheduler(device: str, scheduler: str) -> bool:
    """Set IO scheduler for a block device.

    Args:
        device: Block device path (e.g., "/dev/loop0")
        scheduler: Scheduler name (e.g., "mq-deadline", "none", "bfq", "kyber")

    Returns:
        True if successful, False otherwise
    """
    # Extract device name (e.g., "loop0" from "/dev/loop0")
    device_name = Path(device).name
    scheduler_file = Path(f"/sys/block/{device_name}/queue/scheduler")

    if not scheduler_file.exists():
        logger.error(f"Scheduler file not found for {device}: {scheduler_file}")
        return False

    # Verify scheduler is available
    available = _get_available_schedulers(device)
    if available is None:
        logger.error(f"Cannot determine available schedulers for {device}")
        return False

    if scheduler not in available:
        logger.error(f"Scheduler '{scheduler}' not available for {device}")
        logger.error(f"Available schedulers: {', '.join(available)}")
        return False

    try:
        # Write scheduler name to sysfs file
        scheduler_file.write_text(scheduler)
        logger.info(f"✓ Set IO scheduler for {device}: {scheduler}")
        return True
    except Exception as e:
        logger.error(f"Failed to set IO scheduler for {device}: {e}")
        return False


def _cleanup_host_loop_device(loop_device: str, backing_file: Optional[Path] = None):
    """Cleanup a host loop device and its backing file.

    Args:
        loop_device: Path to loop device (e.g., "/dev/loop0")
        backing_file: Optional path to backing file to remove
    """
    # Detach loop device
    try:
        subprocess.run(["sudo", "losetup", "-d", loop_device], capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
            pass


def _ensure_log_directory() -> Path:
    """Ensure boot log directory exists.

    Returns:
        Path to boot log directory
    """
    BOOT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return BOOT_LOG_DIR


def _cleanup_old_logs(max_age_days: int = 7):
    """Delete boot logs older than specified days.

    Args:
        max_age_days: Maximum age of logs to keep in days
    """
    if not BOOT_LOG_DIR.exists():
        return

    import time

    current_time = time.time()
    max_age_seconds = max_age_days * 24 * 60 * 60

    try:
        for log_file in BOOT_LOG_DIR.glob("boot-*.log"):
            if log_file.is_file():
                file_age = current_time - log_file.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        log_file.unlink()
                    except OSError:
                        # Ignore errors during cleanup
                        pass
    except Exception:
        # Don't fail if cleanup fails
        pass


def _save_boot_log(output: str, success: bool) -> Path:
    """Save boot log to timestamped file.

    Args:
        output: Boot console output
        success: Whether boot was successful

    Returns:
        Path to saved log file
    """
    _ensure_log_directory()

    # Create timestamped filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    status = "success" if success else "failure"
    log_file = BOOT_LOG_DIR / f"boot-{timestamp}-{status}.log"

    # Save log
    try:
        log_file.write_text(output, encoding="utf-8")
    except Exception as e:
        # If saving fails, create a minimal error log
        try:
            log_file.write_text(f"Error saving boot log: {e}\n\n{output[:1000]}", encoding="utf-8")
        except Exception:
            pass

    return log_file


def _run_with_pty(
    cmd: List[str], cwd: Path, timeout: int, emit_output: bool = False, description: str = ""
) -> Tuple[int, str, List[str], Path]:
    """Run a command with a pseudo-terminal.

    This is needed for virtme-ng which requires a valid PTS.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        timeout: Timeout in seconds
        emit_output: If True, emit output in real-time to logger (for long operations)
        description: Description of what's running (for tracking)

    Returns:
        Tuple of (exit_code, output, progress_messages, log_file_path)
    """
    # Create log file for this run (before starting the process)
    _ensure_log_directory()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file_path = BOOT_LOG_DIR / f"boot-{timestamp}-running.log"
    log_file_handle = None

    try:
        # Open log file for writing
        log_file_handle = open(log_file_path, "w", encoding="utf-8", buffering=1)  # Line buffered
        log_file_handle.write("=== VM Boot Log ===\n")
        log_file_handle.write(f"Description: {description}\n")
        log_file_handle.write(f"Started: {datetime.datetime.now().isoformat()}\n")
        log_file_handle.write(f"Command: {' '.join(cmd)}\n")
        log_file_handle.write("=" * 80 + "\n\n")
        log_file_handle.flush()
    except Exception as e:
        logger.warning(f"Failed to create log file {log_file_path}: {e}")
        if log_file_handle:
            try:
                log_file_handle.close()
            except Exception:
                pass
            log_file_handle = None

    # Create a pseudo-terminal
    master_fd, slave_fd = pty.openpty()
    process = None

    try:
        # Start the process with the slave PTY
        # Use start_new_session=True to create a new process group
        # This ensures we can kill all child processes (including QEMU)
        process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,  # Create new process group
        )

        # Track this VM process so we can kill it later if needed
        pgid = os.getpgid(process.pid)
        _track_vm_process(process.pid, pgid, description, log_file_path=log_file_path)

        # Close the slave FD in the parent process
        os.close(slave_fd)

        # Read output with timeout
        output = []
        progress_messages = []  # Accumulate progress for return to caller
        start_time = time.time()
        last_progress_log = start_time

        # Line buffering: accumulate partial lines properly
        line_buffer = ""  # Accumulates characters until we see a newline
        complete_lines_since_last_log = []  # Complete lines for "interesting" detection
        complete_lines_for_verbose = []  # Complete lines for verbose output logging

        while True:
            # Check if process is still running
            if process.poll() is not None:
                break

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout:
                # Kill the entire process group to ensure child processes (QEMU) are also killed
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    # Process already died
                    pass
                process.wait()
                raise subprocess.TimeoutExpired(cmd, timeout, b"".join(output))

            # Emit progress log every 10 seconds if emit_output is enabled
            if emit_output and (time.time() - last_progress_log) > 10:
                progress_msg = (
                    f"[{elapsed:.0f}s] Still running ({timeout - elapsed:.0f}s remaining)"
                )
                logger.info(f"  {progress_msg}")
                progress_messages.append(progress_msg)

                # Log recent output lines (more verbose logging to file)
                if complete_lines_for_verbose:
                    # Log last 20 complete lines to file
                    for line in complete_lines_for_verbose[-20:]:
                        if line.strip():  # Only log non-empty lines
                            logger.info(f"    OUT: {line[:200]}")
                    complete_lines_for_verbose.clear()

                # Also log any interesting lines we've seen (to both log and progress)
                if complete_lines_since_last_log:
                    # Look for lines with "===", "ERROR", "FAIL", or test names
                    interesting_lines = []
                    for line in complete_lines_since_last_log[-10:]:  # Last 10 lines
                        if any(
                            marker in line
                            for marker in [
                                "===",
                                "ERROR",
                                "FAIL",
                                "btrfs/",
                                "generic/",
                                "xfs/",
                                "ext4/",
                                "FSTYP",
                                "Passed",
                                "Failed",
                            ]
                        ):
                            interesting_lines.append(line[:150])

                    if interesting_lines:
                        for line in interesting_lines:
                            logger.info(f"    {line}")
                            progress_messages.append(f"  {line}")

                    complete_lines_since_last_log.clear()
                last_progress_log = time.time()
                # Explicitly flush logs
                for handler in logger.handlers:
                    handler.flush()

            # Try to read with a short timeout
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        output.append(data)

                        # Write to log file in real-time
                        if log_file_handle:
                            try:
                                log_file_handle.write(data.decode("utf-8", errors="replace"))
                                log_file_handle.flush()
                            except Exception:
                                pass  # Don't fail if log writing fails

                        # Track lines for progress logging
                        if emit_output:
                            try:
                                text = data.decode("utf-8", errors="replace")
                                # Accumulate into line buffer and extract complete lines
                                line_buffer += text

                                # Split on newlines but keep incomplete last line in buffer
                                if "\n" in line_buffer:
                                    parts = line_buffer.split("\n")
                                    # All but last part are complete lines
                                    complete_lines = parts[:-1]
                                    # Last part is incomplete (or empty if ended with \n)
                                    line_buffer = parts[-1]

                                    # Add complete lines to our tracking lists
                                    complete_lines_since_last_log.extend(complete_lines)
                                    complete_lines_for_verbose.extend(complete_lines)
                            except Exception:
                                pass
                except OSError:
                    break

        # Get any remaining output
        while True:
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                output.append(data)
                # Write remaining data to log file
                if log_file_handle:
                    try:
                        log_file_handle.write(data.decode("utf-8", errors="replace"))
                        log_file_handle.flush()
                    except Exception:
                        pass
            except OSError:
                break

        # Wait for process to finish
        exit_code = process.wait()

        # Untrack the process since it's finished
        _untrack_vm_process(process.pid)

        # Decode output
        output_str = b"".join(output).decode("utf-8", errors="replace")

        # Rename log file to indicate success/failure
        final_log_path = log_file_path
        if log_file_path.exists():
            try:
                status = "success" if exit_code == 0 else "failure"
                final_log_path = log_file_path.parent / log_file_path.name.replace(
                    "-running.log", f"-{status}.log"
                )
                log_file_path.rename(final_log_path)
            except Exception as e:
                logger.warning(f"Failed to rename log file: {e}")
                final_log_path = log_file_path

        return exit_code, output_str, progress_messages, final_log_path

    finally:
        # Close log file if it's open
        if log_file_handle:
            try:
                log_file_handle.write("\n\n=== VM Process Terminated ===\n")
                log_file_handle.write(f"Ended: {datetime.datetime.now().isoformat()}\n")
                log_file_handle.close()
            except Exception:
                pass

        # Ensure cleanup of process group if process is still alive
        if process and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Process already died or we don't have permissions
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # Untrack the process if it exists (in case of exception path)
        if process:
            _untrack_vm_process(process.pid)

        try:
            os.close(master_fd)
        except OSError:
            pass


async def _run_with_pty_async(
    cmd: List[str], cwd: Path, timeout: int, emit_output: bool = False, description: str = ""
) -> Tuple[int, str, List[str], Path]:
    """Async version: Run a command with a pseudo-terminal without blocking the event loop.

    This is needed for virtme-ng which requires a valid PTS, and to allow other
    async operations (like tool calls) to run concurrently.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        timeout: Timeout in seconds
        emit_output: If True, emit output in real-time to logger (for long operations)
        description: Description of what's running (for tracking)

    Returns:
        Tuple of (exit_code, output, progress_messages, log_file_path)
    """
    # Create log file for this run (before starting the process)
    _ensure_log_directory()
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file_path = BOOT_LOG_DIR / f"boot-{timestamp}-running.log"
    log_file_handle = None

    try:
        # Open log file for writing (append mode in case we want to write header)
        log_file_handle = open(log_file_path, "w", encoding="utf-8", buffering=1)  # Line buffered
        log_file_handle.write("=== VM Boot Log ===\n")
        log_file_handle.write(f"Description: {description}\n")
        log_file_handle.write(f"Started: {datetime.datetime.now().isoformat()}\n")
        log_file_handle.write(f"Command: {' '.join(cmd)}\n")
        log_file_handle.write("=" * 80 + "\n\n")
        log_file_handle.flush()
    except Exception as e:
        logger.warning(f"Failed to create log file {log_file_path}: {e}")
        # Continue without log file if it fails
        if log_file_handle:
            try:
                log_file_handle.close()
            except Exception:
                pass
            log_file_handle = None

    # Create a pseudo-terminal
    master_fd, slave_fd = pty.openpty()

    # Set non-blocking mode on master_fd so we can use it with asyncio
    os.set_blocking(master_fd, False)

    process = None
    output = []
    progress_messages = []

    try:
        # Start the process with the slave PTY
        # Note: asyncio.create_subprocess_exec doesn't support passing fd directly,
        # so we use the low-level subprocess with asyncio monitoring
        process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,  # Create new process group
        )

        # Track this VM process so we can kill it later if needed
        pgid = os.getpgid(process.pid)
        _track_vm_process(process.pid, pgid, description, log_file_path=log_file_path)

        # Close the slave FD in the parent process
        os.close(slave_fd)

        # Read output with timeout (async)
        start_time = time.time()
        last_progress_log = start_time

        # Line buffering
        line_buffer = ""
        complete_lines_since_last_log = []
        complete_lines_for_verbose = []

        # Get the event loop
        loop = asyncio.get_event_loop()

        # Create a future for reading data
        read_queue = asyncio.Queue()

        def read_ready():
            """Callback when data is available to read (registered with event loop)."""
            try:
                data = os.read(master_fd, 4096)
                if data:
                    # Schedule putting data in queue (thread-safe)
                    asyncio.create_task(read_queue.put(data))
            except (OSError, BlockingIOError):
                pass  # No data available or error

        # Register the master_fd for reading
        loop.add_reader(master_fd, read_ready)

        try:
            while True:
                # Check if process is still running
                if process.poll() is not None:
                    break

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    # Kill the entire process group
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.sleep(0.1)  # Give it a moment

                    # Write timeout message to log
                    timeout_msg = f"\n\nERROR: Process timed out after {timeout}s\n"
                    if log_file_handle:
                        try:
                            log_file_handle.write(timeout_msg)
                            log_file_handle.flush()
                        except Exception:
                            pass

                    # Untrack the process since we're killing it
                    _untrack_vm_process(process.pid)

                    # Decode output
                    output_str = b"".join(output).decode("utf-8", errors="replace")
                    output_str += timeout_msg

                    # Return with timeout error code
                    # Log file will be renamed to "failure" since exit_code != 0
                    final_log_path = log_file_path
                    if log_file_path.exists():
                        try:
                            final_log_path = log_file_path.parent / log_file_path.name.replace(
                                "-running.log", "-timeout.log"
                            )
                            log_file_path.rename(final_log_path)
                        except Exception:
                            pass

                    return -1, output_str, progress_messages, final_log_path

                # Emit progress log every 10 seconds if emit_output is enabled
                if emit_output and (time.time() - last_progress_log) > 10:
                    progress_msg = (
                        f"[{elapsed:.0f}s] Still running ({timeout - elapsed:.0f}s remaining)"
                    )
                    logger.info(f"  {progress_msg}")
                    progress_messages.append(progress_msg)

                    # Log recent output lines
                    if complete_lines_for_verbose:
                        for line in complete_lines_for_verbose[-20:]:
                            if line.strip():
                                logger.info(f"    OUT: {line[:200]}")
                        complete_lines_for_verbose.clear()

                    # Log interesting lines
                    if complete_lines_since_last_log:
                        interesting_lines = []
                        for line in complete_lines_since_last_log[-10:]:
                            if any(
                                marker in line
                                for marker in [
                                    "===",
                                    "ERROR",
                                    "FAIL",
                                    "btrfs/",
                                    "generic/",
                                    "xfs/",
                                    "ext4/",
                                    "FSTYP",
                                    "Passed",
                                    "Failed",
                                ]
                            ):
                                interesting_lines.append(line[:150])

                        if interesting_lines:
                            for line in interesting_lines:
                                logger.info(f"    {line}")
                                progress_messages.append(f"  {line}")

                        complete_lines_since_last_log.clear()
                    last_progress_log = time.time()

                    # Flush logs
                    for handler in logger.handlers:
                        handler.flush()

                # Try to read data from queue (non-blocking with short timeout)
                try:
                    data = await asyncio.wait_for(read_queue.get(), timeout=0.1)
                    output.append(data)

                    # Write to log file in real-time
                    if log_file_handle:
                        try:
                            log_file_handle.write(data.decode("utf-8", errors="replace"))
                            log_file_handle.flush()
                        except Exception:
                            pass  # Don't fail if log writing fails

                    # Track lines for progress logging
                    if emit_output:
                        try:
                            text = data.decode("utf-8", errors="replace")
                            line_buffer += text

                            if "\n" in line_buffer:
                                parts = line_buffer.split("\n")
                                complete_lines = parts[:-1]
                                line_buffer = parts[-1]

                                complete_lines_since_last_log.extend(complete_lines)
                                complete_lines_for_verbose.extend(complete_lines)
                        except Exception:
                            pass
                except asyncio.TimeoutError:
                    # No data available, continue loop
                    await asyncio.sleep(0.01)  # Small yield to event loop

        finally:
            # Unregister the reader
            loop.remove_reader(master_fd)

        # Get any remaining output (with small timeout)
        for _ in range(10):  # Try a few times
            try:
                data = os.read(master_fd, 4096)
                if data:
                    output.append(data)
                    # Write remaining data to log file
                    if log_file_handle:
                        try:
                            log_file_handle.write(data.decode("utf-8", errors="replace"))
                            log_file_handle.flush()
                        except Exception:
                            pass
                else:
                    break
            except (OSError, BlockingIOError):
                break
            await asyncio.sleep(0.01)

        # Wait for process to finish
        exit_code = process.wait()

        # Untrack the process since it's finished
        _untrack_vm_process(process.pid)

        # Decode output
        output_str = b"".join(output).decode("utf-8", errors="replace")

        # Rename log file to indicate success/failure
        final_log_path = log_file_path
        if log_file_path.exists():
            try:
                status = "success" if exit_code == 0 else "failure"
                final_log_path = log_file_path.parent / log_file_path.name.replace(
                    "-running.log", f"-{status}.log"
                )
                log_file_path.rename(final_log_path)
            except Exception as e:
                logger.warning(f"Failed to rename log file: {e}")
                final_log_path = log_file_path

        return exit_code, output_str, progress_messages, final_log_path

    finally:
        # Close log file if it's open
        if log_file_handle:
            try:
                log_file_handle.write("\n\n=== VM Process Terminated ===\n")
                log_file_handle.write(f"Ended: {datetime.datetime.now().isoformat()}\n")
                log_file_handle.close()
            except Exception:
                pass

        # Ensure cleanup of process group if process is still alive
        if process and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # Untrack the process if it exists (in case of exception path)
        if process:
            _untrack_vm_process(process.pid)

        try:
            os.close(master_fd)
        except OSError:
            pass


class BootManager:
    """Manages kernel boot testing with virtme-ng."""

    def __init__(self, kernel_path: Path):
        """Initialize boot manager.

        Args:
            kernel_path: Path to kernel source tree
        """
        self.kernel_path = Path(kernel_path)
        if not self.kernel_path.exists():
            raise ValueError(f"Kernel path does not exist: {kernel_path}")

        # Storage for last fstests result (for comparison tool)
        self._last_fstests_result = None

    def check_virtme_ng(self) -> bool:
        """Check if virtme-ng is installed.

        Returns:
            True if virtme-ng is available
        """
        try:
            result = subprocess.run(["vng", "--version"], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def check_qemu(self, arch: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Check if QEMU is installed for the target architecture.

        Args:
            arch: Target architecture (e.g., "x86_64", "arm64"). If None, checks for x86_64.

        Returns:
            Tuple of (is_available, qemu_binary_path or error_message)
        """
        # Map architecture names to QEMU binary names
        arch_to_qemu = {
            "x86_64": "qemu-system-x86_64",
            "x86": "qemu-system-i386",
            "arm64": "qemu-system-aarch64",
            "arm": "qemu-system-arm",
            "riscv": "qemu-system-riscv64",
            "powerpc": "qemu-system-ppc64",
            "mips": "qemu-system-mips64",
        }

        # Default to x86_64 if no arch specified
        target_arch = arch or "x86_64"
        qemu_binary = arch_to_qemu.get(target_arch, f"qemu-system-{target_arch}")

        try:
            result = subprocess.run(
                [qemu_binary, "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Extract QEMU version for informational purposes
                version_line = result.stdout.splitlines()[0] if result.stdout else ""
                return True, version_line
            return False, f"QEMU binary '{qemu_binary}' exists but returned error"
        except FileNotFoundError:
            return False, f"QEMU binary '{qemu_binary}' not found in PATH"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return False, f"Error checking QEMU: {str(e)}"

    def detect_kernel_architecture(self, vmlinux_path: Optional[Path] = None) -> Optional[str]:
        """Detect the target architecture of a compiled kernel.

        Args:
            vmlinux_path: Path to vmlinux binary. If None, uses kernel_path/vmlinux.

        Returns:
            Architecture string compatible with virtme-ng (e.g., "x86_64", "arm64", "riscv")
            or None if detection fails.
        """
        if vmlinux_path is None:
            vmlinux_path = self.kernel_path / "vmlinux"

        if not vmlinux_path.exists():
            logger.warning(f"vmlinux not found at {vmlinux_path}, cannot detect architecture")
            return None

        try:
            # Use 'file' command to detect ELF architecture
            result = subprocess.run(
                ["file", str(vmlinux_path)], capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"Failed to run 'file' command on {vmlinux_path}")
                return None

            output = result.stdout.lower()

            # Map file output to virtme-ng architecture names
            if "x86-64" in output or "x86_64" in output:
                return "x86_64"
            elif "x86" in output or "80386" in output or "i386" in output:
                return "x86"
            elif "aarch64" in output or "arm64" in output:
                return "arm64"
            elif "arm" in output:
                return "arm"
            elif "riscv" in output:
                # Detect whether it's 32-bit or 64-bit RISC-V
                if "64-bit" in output:
                    return "riscv"
                else:
                    return "riscv32"
            elif "powerpc" in output or "ppc64" in output:
                return "powerpc"
            elif "mips" in output:
                return "mips"

            logger.warning(f"Could not determine architecture from: {output}")
            return None

        except FileNotFoundError:
            logger.warning("'file' command not found, cannot detect kernel architecture")
            return None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Error detecting kernel architecture: {e}")
            return None

    def _resolve_target_architecture(
        self, cross_compile: Optional["CrossCompileConfig"], use_host_kernel: bool = False
    ) -> Optional[str]:
        """Resolve target architecture from cross_compile config or auto-detection.

        Args:
            cross_compile: Cross-compilation configuration
            use_host_kernel: Whether using host kernel (skips detection if True)

        Returns:
            Target architecture string if different from host, None if same as host
            or if detection fails.
        """
        if cross_compile:
            return cross_compile.arch

        if use_host_kernel:
            return None

        # Try to detect architecture from vmlinux
        detected_arch = self.detect_kernel_architecture()
        if not detected_arch:
            logger.warning("Could not auto-detect kernel architecture, assuming host architecture")
            return None

        import platform

        host_arch = platform.machine()
        # Normalize host arch names
        if host_arch == "amd64":
            host_arch = "x86_64"
        elif host_arch == "aarch64":
            host_arch = "arm64"

        if detected_arch != host_arch:
            logger.info(f"✓ Auto-detected kernel architecture: {detected_arch}")
            logger.info(f"  (different from host: {host_arch})")
            return detected_arch
        else:
            logger.info(f"✓ Kernel architecture matches host: {detected_arch}")
            return None

    def _generate_pool_session_id(self) -> str:
        """Generate unique session ID for device pool allocation.

        Returns:
            Session ID in format: timestamp-random (e.g., "20251115103045-a3f9d2")
        """
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{timestamp}-{random_suffix}"

    def _try_allocate_from_pool(self, use_tmpfs: bool) -> Optional[List[DeviceSpec]]:
        """Try to allocate devices from device pool.

        Args:
            use_tmpfs: Ignored for pool devices (only affects loop devices)

        Returns:
            List of DeviceSpec objects if pool found and allocation succeeded, None otherwise
        """
        from .device_pool import ConfigManager, VolumeConfig, allocate_pool_volumes

        config_dir = Path.home() / ".kerneldev-mcp"
        config_file = config_dir / "device-pool.json"

        if not config_file.exists():
            logger.debug("No device pool configuration found")
            return None

        try:
            config_manager = ConfigManager(config_dir)
            pools = config_manager.load_pools()
        except Exception as e:
            logger.warning(f"Failed to load device pool config: {e}")
            return None

        if "default" not in pools:
            logger.debug("No 'default' device pool configured")
            return None

        volume_specs = [
            VolumeConfig(name="test", size="10G", env_var="TEST_DEV", order=0),
            VolumeConfig(name="pool1", size="10G", order=1),
            VolumeConfig(name="pool2", size="10G", order=2),
            VolumeConfig(name="pool3", size="10G", order=3),
            VolumeConfig(name="pool4", size="10G", order=4),
            VolumeConfig(name="pool5", size="10G", order=5),
            VolumeConfig(name="logwrites", size="10G", env_var="LOGWRITES_DEV", order=6),
        ]

        session_id = self._generate_pool_session_id()

        try:
            device_specs = allocate_pool_volumes(
                pool_name="default",
                volume_specs=volume_specs,
                session_id=session_id,
                config_dir=config_dir,
            )

            if device_specs is None:
                logger.warning("Device pool allocation failed")
                return None

            self._pool_session_id = session_id
            return device_specs

        except Exception as e:
            logger.warning(f"Failed to allocate from device pool: {e}")
            return None

    async def boot_test(
        self,
        command: Optional[str] = None,
        script_file: Optional[Path] = None,
        timeout: int = 60,
        memory: str = "2G",
        cpus: int = 2,
        devices: Optional[List[DeviceSpec]] = None,
        cross_compile: Optional["CrossCompileConfig"] = None,
        extra_args: Optional[List[str]] = None,
        use_host_kernel: bool = False,
        device_pool_name: Optional[str] = None,
        device_pool_volumes: Optional[List[Dict[str, any]]] = None,
    ) -> BootResult:
        """Boot kernel and test it with optional custom command/script.

        By default, validates successful boot via dmesg. Optionally run custom
        test commands or scripts for more sophisticated testing.

        Args:
            command: Optional shell command to execute for testing.
                    If None and script_file is None, runs dmesg validation (default).
            script_file: Optional path to local script file to upload and execute.
                        Cannot be specified together with command.
            timeout: Boot timeout in seconds
            memory: Memory size for VM (e.g., "2G")
            cpus: Number of CPUs for VM
            devices: Optional list of DeviceSpec for custom device attachment.
                     If provided, device environment variables are exported (TEST_DEV, etc.).
                     Cannot be used together with device_pool_name.
            cross_compile: Cross-compilation configuration
            extra_args: Additional arguments to pass to vng
            use_host_kernel: Use host kernel instead of building from source
            device_pool_name: Optional name of device pool to allocate volumes from.
                            If specified, volumes are allocated from the pool and
                            automatically cleaned up after the VM exits.
                            Cannot be used together with devices parameter.
            device_pool_volumes: Volume specifications for device pool allocation.
                               Each dict should contain: name, size, optional env_var, optional order.
                               Example: [{"name": "test", "size": "10G", "env_var": "TEST_DEV"}]
                               If not specified and device_pool_name is set, defaults to
                               two 10G volumes (test and scratch).

        Returns:
            BootResult with boot status and analysis

        Note:
            This tool does NOT set up fstests infrastructure (no filesystem formatting,
            no mount points, no fstests config). For filesystem testing with fstests
            environment, use fstests_vm_boot_custom instead.
        """
        # Validation: cannot specify both command and script_file
        if command and script_file:
            raise ValueError("Cannot specify both 'command' and 'script_file' parameters")

        # Validation: cannot specify both devices and device_pool_name
        if devices and device_pool_name:
            raise ValueError(
                "Cannot specify both 'devices' and 'device_pool_name' parameters. "
                "Use either custom devices or device pool, not both."
            )

        # Generate unique session ID for device pool allocation
        session_id = str(uuid.uuid4())
        pool_allocated = False

        # Allocate volumes from device pool if specified
        if device_pool_name:
            logger.info(f"Allocating volumes from device pool '{device_pool_name}'...")

            # Use provided volume specs or defaults
            if device_pool_volumes:
                volume_specs = [
                    VolumeConfig(
                        name=vol["name"],
                        size=vol["size"],
                        env_var=vol.get("env_var"),
                        order=vol.get("order", 0),
                    )
                    for vol in device_pool_volumes
                ]
            else:
                # Default: two 10G volumes (test and scratch)
                volume_specs = [
                    VolumeConfig(name="test", size="10G", env_var="TEST_DEV", order=0),
                    VolumeConfig(name="scratch", size="10G", env_var="SCRATCH_DEV", order=1),
                ]

            # Allocate volumes
            device_specs = allocate_pool_volumes(device_pool_name, volume_specs, session_id)
            if device_specs is None:
                return BootResult(
                    success=False,
                    duration=0.0,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Failed to allocate volumes from device pool '{device_pool_name}'",
                    exit_code=-1,
                )

            # Use allocated volumes as devices
            devices = device_specs
            pool_allocated = True
            logger.info(f"✓ Allocated {len(devices)} volume(s) from pool '{device_pool_name}'")

        logger.info("=" * 60)
        logger.info(f"Starting kernel boot test: {self.kernel_path}")
        logger.info(f"Config: memory={memory}, cpus={cpus}, timeout={timeout}s")
        if command:
            logger.info(f"Test command: {command}")
        elif script_file:
            logger.info(f"Test script: {script_file}")
        else:
            logger.info("Test mode: dmesg validation (default)")
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if use_host_kernel:
            logger.info("Using host kernel (not building from source)")

        start_time = time.time()

        # Cleanup old boot logs
        _cleanup_old_logs()

        # Check script file exists if provided
        if script_file:
            script_file = Path(script_file)
            if not script_file.exists():
                logger.error(f"✗ Script file not found: {script_file}")
                logger.info("=" * 60)
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Script file not found: {script_file}",
                    exit_code=-1,
                )

        # Check virtme-ng is available
        if not self.check_virtme_ng():
            logger.error("✗ virtme-ng not found")
            logger.info("=" * 60)
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output="ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng",
                exit_code=-1,
            )

        # Auto-detect kernel architecture if not explicitly specified and not using host kernel
        target_arch = self._resolve_target_architecture(cross_compile, use_host_kernel)

        # Check QEMU is available for target architecture
        qemu_available, qemu_info = self.check_qemu(target_arch)
        if not qemu_available:
            logger.error(f"✗ QEMU not found: {qemu_info}")
            logger.info("=" * 60)
            install_instructions = (
                "Install QEMU for your distribution:\n"
                "  Fedora/RHEL: sudo dnf install qemu-system-x86\n"
                "  Ubuntu/Debian: sudo apt-get install qemu-system-x86\n"
                "  Arch: sudo pacman -S qemu-system-x86"
            )
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: {qemu_info}\n\n{install_instructions}",
                exit_code=-1,
            )
        else:
            logger.info(f"✓ QEMU available: {qemu_info}")

        # Check if kernel is built (vmlinux exists) unless using host kernel
        if not use_host_kernel:
            vmlinux = self.kernel_path / "vmlinux"
            if not vmlinux.exists():
                logger.error(f"✗ Kernel not built: vmlinux not found at {vmlinux}")
                logger.info("=" * 60)
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Kernel not built. vmlinux not found at {vmlinux}\nBuild the kernel first or set use_host_kernel=True",
                    exit_code=-1,
                )

        # Setup custom devices if specified
        device_manager = None
        if devices:
            device_manager = VMDeviceManager()
            success, error, device_paths = await device_manager.setup_devices(devices)
            if not success:
                logger.error(f"✗ Device setup failed: {error}")
                logger.info("=" * 60)
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Device setup failed: {error}",
                    exit_code=-1,
                )
            logger.info(f"✓ Setup {len(device_paths)} custom device(s)")

        # Build vng command
        cmd = ["vng", "--verbose"]  # --verbose is critical to capture serial console output
        logger.info(f"Boot command: {' '.join(cmd[:5])}...")  # Don't log full command (may be long)

        # Use --run for host kernel
        if use_host_kernel:
            cmd.append("--run")

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add architecture if specified or auto-detected
        if target_arch:
            cmd.extend(["--arch", target_arch])

        # Add custom device disk arguments
        if device_manager:
            disk_args = device_manager.get_vng_disk_args()
            cmd.extend(disk_args)

        # Add any extra arguments
        if extra_args:
            cmd.extend(extra_args)

        # Determine what command to execute
        if command or script_file:
            # Create wrapper script that sets up environment and runs the command/script
            wrapper_script = "#!/bin/bash\nset -e\n\n"

            # Export device environment variables if devices are present
            if device_manager:
                env_script = device_manager.get_vm_env_script()
                if env_script:
                    wrapper_script += env_script + "\n\n"

            # Add the user's command or script
            if script_file:
                script_contents = script_file.read_text()
                wrapper_script += f"# Execute uploaded script: {script_file.name}\n"
                wrapper_script += script_contents + "\n"
            elif command:
                wrapper_script += "# Execute custom command\n"
                wrapper_script += command + "\n"

            # Write wrapper script to temp file
            vm_script_file = Path("/tmp/boot-test-wrapper.sh")
            vm_script_file.write_text(wrapper_script)
            vm_script_file.chmod(0o755)

            # Execute the wrapper script
            cmd.extend(["--", "bash", str(vm_script_file)])
            logger.info("Booting kernel and running test command/script...")
        else:
            # Default: Execute dmesg command
            cmd.extend(["--", "dmesg"])
            logger.info("Booting kernel... (this may take a minute)")

        # Run boot test with PTY (virtme-ng requires a valid PTS)
        try:
            mode_desc = "script" if script_file else ("command" if command else "dmesg")
            description = f"boot_kernel_test ({mode_desc}): {self.kernel_path.name}"
            exit_code, dmesg_output, _, log_file = await _run_with_pty_async(
                cmd, self.kernel_path, timeout, description=description
            )

            duration = time.time() - start_time

            # Parse dmesg
            errors, warnings, panics, oops = DmesgParser.analyze_dmesg(dmesg_output)

            # Extract kernel version if available
            kernel_version = None
            for line in dmesg_output.splitlines():
                if "Linux version" in line:
                    # Extract version string
                    match = re.search(r"Linux version ([\d\.\-\w]+)", line)
                    if match:
                        kernel_version = match.group(1)
                        logger.info(f"Booted kernel version: {kernel_version}")
                    break

            # Log file was already written during execution by _run_with_pty_async
            boot_success = exit_code == 0 and len(panics) == 0

            # Log result
            if boot_success:
                logger.info(f"✓ Boot completed successfully in {duration:.1f}s")
                logger.info(f"  Errors: {len(errors)}, Warnings: {len(warnings)}")
            else:
                logger.error(f"✗ Boot failed after {duration:.1f}s")
                logger.error(f"  Panics: {len(panics)}, Oops: {len(oops)}")
                logger.error(f"  Errors: {len(errors)}, Warnings: {len(warnings)}")
                logger.error(f"  Exit code: {exit_code}")
            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)

            # Check if this was a timeout (exit_code == -1 from _run_with_pty_async timeout handling)
            timeout_occurred = exit_code == -1 and "timed out" in dmesg_output.lower()

            return BootResult(
                success=boot_success,
                duration=duration,
                boot_completed=(exit_code == 0),
                kernel_version=kernel_version,
                errors=errors,
                warnings=warnings,
                panics=panics,
                oops=oops,
                dmesg_output=dmesg_output,
                exit_code=exit_code,
                timeout_occurred=timeout_occurred,
                log_file_path=log_file,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot failed with exception: {e}")
            logger.info("=" * 60)

            # Try to find the most recent log file (might have been created before exception)
            # Otherwise create a new error log
            log_file = None
            try:
                if BOOT_LOG_DIR.exists():
                    recent_logs = sorted(
                        BOOT_LOG_DIR.glob("boot-*-running.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if recent_logs and (time.time() - recent_logs[0].stat().st_mtime) < 60:
                        # If there's a running log from the last minute, use it
                        log_file = recent_logs[0]
                        # Rename it to error
                        try:
                            error_log = log_file.parent / log_file.name.replace(
                                "-running.log", "-error.log"
                            )
                            log_file.rename(error_log)
                            log_file = error_log
                        except Exception:
                            pass
            except Exception:
                pass

            # Create error log if we couldn't find one
            if not log_file:
                error_output = f"ERROR: {str(e)}"
                log_file = _save_boot_log(error_output, success=False)

            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=f"ERROR: {str(e)}",
                exit_code=-1,
                log_file_path=log_file,
            )

        finally:
            # Cleanup devices
            if device_manager:
                device_manager.cleanup()

            # Release device pool volumes if allocated
            if pool_allocated and device_pool_name:
                logger.info(f"Releasing volumes from device pool '{device_pool_name}'...")
                try:
                    release_pool_volumes(device_pool_name, session_id, keep_volumes=False)
                    logger.info(f"✓ Released volumes from pool '{device_pool_name}'")
                except Exception as e:
                    logger.error(f"Failed to release pool volumes: {e}")

    async def boot_with_fstests(
        self,
        fstests_path: Path,
        tests: List[str],
        fstype: str = "ext4",
        timeout: int = 300,
        memory: str = "4G",
        cpus: int = 4,
        custom_devices: Optional[List[DeviceSpec]] = None,
        use_default_devices: bool = True,
        cross_compile: Optional["CrossCompileConfig"] = None,
        force_9p: bool = False,
        io_scheduler: str = "mq-deadline",
        use_tmpfs: bool = False,
        extra_args: Optional[List[str]] = None,
    ) -> Tuple[BootResult, Optional[object]]:
        """Boot kernel and run fstests inside VM.

        Args:
            fstests_path: Path to fstests installation
            tests: Tests to run (e.g., ["-g", "quick"])
            fstype: Filesystem type to test (e.g., "ext4", "btrfs", "xfs")
            timeout: Total timeout in seconds
            memory: Memory size for VM
            cpus: Number of CPUs
            custom_devices: Custom device specifications. If provided, use_default_devices is ignored.
            use_default_devices: If True and custom_devices is None, use 7 default fstests devices.
                                 If False and custom_devices is None, no devices are attached.
            cross_compile: Cross-compilation configuration
            force_9p: Force use of 9p filesystem instead of virtio-fs
            io_scheduler: IO scheduler to use for block devices (default: "mq-deadline")
                         Valid values: "mq-deadline", "none", "bfq", "kyber"
            use_tmpfs: Only affects default devices (when custom_devices is None).
                      Use tmpfs for loop device backing files (faster but uses more RAM)
            extra_args: Additional arguments to pass to vng

        Returns:
            Tuple of (BootResult, FstestsRunResult or None)
        """
        # Import here to avoid circular dependency
        from .fstests_manager import FstestsManager

        logger.info("=" * 60)
        logger.info(f"Starting kernel boot with fstests: {self.kernel_path}")
        logger.info(f"Config: fstype={fstype}, memory={memory}, cpus={cpus}, timeout={timeout}s")
        test_args = " ".join(tests) if tests else "-g quick"
        logger.info(f"Tests: {test_args}")
        logger.info(f"IO scheduler: {io_scheduler}")
        if use_tmpfs:
            logger.info("Using tmpfs for loop device backing files (faster but uses more RAM)")
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if force_9p:
            logger.info("Using 9p filesystem (virtio-fs disabled)")

        # Validate test arguments
        if tests:
            is_valid, error_msg = FstestsManager.validate_test_args(tests)
            if not is_valid:
                logger.error(f"✗ Invalid test arguments: {error_msg}")
                logger.info("=" * 60)
                return (
                    BootResult(
                        success=False,
                        duration=0.0,
                        boot_completed=False,
                        dmesg_output=f"ERROR: {error_msg}",
                        exit_code=-1,
                    ),
                    None,
                )

        start_time = time.time()

        # Track pool usage for cleanup
        pool_name = None
        pool_session_id = None

        # Determine which devices to use
        device_specs = None
        if custom_devices is not None:
            # Use custom devices
            device_specs = custom_devices
            logger.info(f"Using {len(custom_devices)} custom device(s)")
        elif use_default_devices:
            device_specs = self._try_allocate_from_pool(use_tmpfs)
            if device_specs is not None:
                pool_name = "default"
                pool_session_id = self._pool_session_id
                logger.info(f"✓ Using device pool 'default' (session: {pool_session_id})")
            else:
                profile = DeviceProfile.get_profile("fstests_default", use_tmpfs=use_tmpfs)
                device_specs = profile.devices
                logger.info(
                    f"Using default fstests device profile (7 loop devices, tmpfs={use_tmpfs})"
                )
        else:
            # No devices
            device_specs = []
            logger.info("No devices will be attached")

        # Track created loop devices for cleanup
        created_loop_devices: List[Tuple[str, Path]] = []
        script_file = None
        device_manager = None

        try:
            # Check virtme-ng is available
            if not self.check_virtme_ng():
                logger.error("✗ virtme-ng not found")
                logger.info("=" * 60)
                return (
                    BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output="ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng",
                        exit_code=-1,
                    ),
                    None,
                )

            # Auto-detect kernel architecture if not explicitly specified
            target_arch = self._resolve_target_architecture(cross_compile)

            # Check QEMU is available for target architecture
            qemu_available, qemu_info = self.check_qemu(target_arch)
            if not qemu_available:
                logger.error(f"✗ QEMU not found: {qemu_info}")
                logger.info("=" * 60)
                install_instructions = (
                    "Install QEMU for your distribution:\n"
                    "  Fedora/RHEL: sudo dnf install qemu-system-x86\n"
                    "  Ubuntu/Debian: sudo apt-get install qemu-system-x86\n"
                    "  Arch: sudo pacman -S qemu-system-x86"
                )
                return (
                    BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output=f"ERROR: {qemu_info}\n\n{install_instructions}",
                        exit_code=-1,
                    ),
                    None,
                )
            else:
                logger.info(f"✓ QEMU available: {qemu_info}")

            # Check if kernel is built
            vmlinux = self.kernel_path / "vmlinux"
            if not vmlinux.exists():
                return (
                    BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output=f"ERROR: Kernel not built. vmlinux not found at {vmlinux}",
                        exit_code=-1,
                    ),
                    None,
                )

            # Check fstests is installed
            fstests_path = Path(fstests_path)
            if not fstests_path.exists() or not (fstests_path / "check").exists():
                return (
                    BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output=f"ERROR: fstests not found at {fstests_path}",
                        exit_code=-1,
                    ),
                    None,
                )

            # Verify that fstests is fully built by checking for critical binaries
            critical_binaries = [
                fstests_path / "ltp" / "fsstress",
                fstests_path / "src" / "aio-dio-regress",
            ]
            missing_binaries = []
            for binary in critical_binaries:
                if not binary.exists() or not os.access(binary, os.X_OK):
                    missing_binaries.append(str(binary.relative_to(fstests_path)))

            if missing_binaries:
                return (
                    BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output=(
                            f"ERROR: fstests is not fully built. Missing binaries: {', '.join(missing_binaries)}\n"
                            f"Run the install_fstests tool to rebuild fstests, or manually run:\n"
                            f"  cd {fstests_path} && ./configure && make -j$(nproc)"
                        ),
                        exit_code=-1,
                    ),
                    None,
                )

            # Setup devices using VMDeviceManager
            if device_specs:
                device_manager = VMDeviceManager()
                success, error, device_paths = await device_manager.setup_devices(device_specs)
                if not success:
                    logger.error(f"✗ Device setup failed: {error}")
                    logger.info("=" * 60)
                    return (
                        BootResult(
                            success=False,
                            duration=time.time() - start_time,
                            boot_completed=False,
                            dmesg_output=f"ERROR: Device setup failed: {error}",
                            exit_code=-1,
                        ),
                        None,
                    )
                logger.info(f"✓ Setup {len(device_paths)} device(s)")

                # Track loop devices for backwards compatibility with cleanup code
                created_loop_devices = device_manager.created_loop_devices

            # Create timestamped results directory on host
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            results_base_dir = Path.home() / ".kerneldev-mcp" / "fstests-results"
            results_dir = results_base_dir / f"run-{timestamp}"
            results_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"✓ Created results directory: {results_dir}")

            # Build test command to run inside VM
            test_args = " ".join(tests) if tests else "-g quick"

            # Create script to run inside VM
            # Note: virtme-ng runs as root, so no sudo needed
            # This script uses devices passed from host via --disk
            # Host loop devices appear as /dev/sda, /dev/sdb, etc. in the VM
            # This script:
            # 1. Uses passed-through block devices (no loop device creation needed)
            # 2. Formats them with appropriate filesystem
            # 3. Creates mount points in /tmp
            # 4. Configures fstests with local.config
            # 5. Runs the tests
            test_script = f"""#!/bin/bash
# Don't exit on error immediately - we want to capture test results
set +e

# Show environment
echo "=== fstests Setup Start ==="
echo "Kernel: $(uname -r)"
echo "User: $(whoami)"
echo "fstests path: {fstests_path}"
echo "Filesystem type: {fstype}"
echo ""

# Verify fstests directory exists
if [ ! -d "{fstests_path}" ]; then
    echo "ERROR: fstests directory not found at {fstests_path}"
    exit 1
fi

# Use devices passed from host via --disk
# virtme-ng passes them as virtio block devices: /dev/vda, /dev/vdb, /dev/vdc, etc.
# We have 7 devices: 1 test + 5 scratch pool + 1 log-writes
echo "Using passed-through block devices..."
TEST_DEV=/dev/vda
POOL1=/dev/vdb
POOL2=/dev/vdc
POOL3=/dev/vdd
POOL4=/dev/vde
POOL5=/dev/vdf
LOGWRITES_DEV=/dev/vdg

# Verify devices exist
for dev in $TEST_DEV $POOL1 $POOL2 $POOL3 $POOL4 $POOL5 $LOGWRITES_DEV; do
    if [ ! -b "$dev" ]; then
        echo "ERROR: Block device $dev not found"
        echo "Available block devices:"
        ls -l /dev/vd* 2>/dev/null || echo "No /dev/vd* devices found"
        exit 1
    fi
done

echo "TEST_DEV=$TEST_DEV"
echo "SCRATCH_DEV_POOL=$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
echo "LOGWRITES_DEV=$LOGWRITES_DEV"

echo "Setting IO scheduler to '{io_scheduler}' on all devices..."
for dev in $TEST_DEV $POOL1 $POOL2 $POOL3 $POOL4 $POOL5 $LOGWRITES_DEV; do
    # Extract device name (e.g., "vda" from "/dev/vda")
    devname=$(basename $dev)
    scheduler_file="/sys/block/$devname/queue/scheduler"

    if [ ! -f "$scheduler_file" ]; then
        echo "ERROR: Scheduler file not found: $scheduler_file"
        exit 1
    fi

    # Check if scheduler is available
    available=$(cat "$scheduler_file")
    if ! echo "$available" | grep -qw "{io_scheduler}"; then
        echo "ERROR: IO scheduler '{io_scheduler}' is not available for $dev"
        echo "Available schedulers: $available"
        echo ""
        echo "Make sure the scheduler is enabled in your kernel config:"
        echo "  CONFIG_MQ_IOSCHED_DEADLINE=y (for mq-deadline)"
        echo "  CONFIG_MQ_IOSCHED_KYBER=y (for kyber)"
        echo "  CONFIG_BFQ_GROUP_IOSCHED=y (for bfq)"
        exit 1
    fi

    # Set the scheduler
    echo "{io_scheduler}" > "$scheduler_file"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to set scheduler '{io_scheduler}' on $dev"
        exit 1
    fi

    # Verify it was set
    current=$(cat "$scheduler_file" | grep -o '\[.*\]' | tr -d '[]')
    if [ "$current" != "{io_scheduler}" ]; then
        echo "ERROR: Failed to verify scheduler on $dev (expected '{io_scheduler}', got '$current')"
        exit 1
    fi

    echo "  ✓ $dev: {io_scheduler}"
done
echo ""

# Format filesystems
echo "Formatting filesystems as {fstype}..."
if [ "{fstype}" = "btrfs" ]; then
    mkfs.btrfs -f $TEST_DEV > /dev/null 2>&1
    # Don't pre-format pool devices - tests will format them as needed
else
    mkfs.ext4 -F $TEST_DEV > /dev/null 2>&1
    # Don't pre-format pool devices - tests will format them as needed
fi

# Create mount points in /tmp
echo "Creating mount points..."
mkdir -p /tmp/test /tmp/scratch

# Create fstests local.config
# Important: When using SCRATCH_DEV_POOL, do NOT set SCRATCH_DEV
# The first device in the pool serves as the scratch device
echo "Creating fstests configuration..."
cat > {fstests_path}/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
export LOGWRITES_DEV=$LOGWRITES_DEV
export FSTYP={fstype}
export RESULT_BASE=/tmp/results
EOF

echo "Configuration written to local.config"
echo ""

# Change to fstests directory
cd {fstests_path} || {{
    echo "ERROR: Failed to change to fstests directory"
    exit 1
}}

# Verify check script exists
if [ ! -f "./check" ]; then
    echo "ERROR: check script not found in $(pwd)"
    ls -la
    exit 1
fi

# Run tests
echo "=== fstests Execution Start ==="
echo "Running: ./check {test_args}"
echo "=== fstests Output ==="
./check {test_args}

# Capture exit code
exit_code=$?
echo ""
echo "=== fstests Execution Complete ==="
echo "Exit code: $exit_code"

# Cleanup
echo "Cleaning up..."
umount /tmp/test 2>/dev/null || true
umount /tmp/scratch 2>/dev/null || true
# Note: Loop devices are managed on the host, not here

exit $exit_code
"""

            # Write script to temp file
            script_file = Path("/tmp/run-fstests.sh")
            script_file.write_text(test_script)
            script_file.chmod(0o755)

            # Build vng command
            cmd = ["vng", "--verbose"]

            # Force 9p if requested (required for old kernels without virtio-fs)
            if force_9p:
                cmd.append("--force-9p")

            # Add memory and CPU options
            cmd.extend(["--memory", memory])
            cmd.extend(["--cpus", str(cpus)])

            # Add architecture if specified or auto-detected
            if target_arch:
                cmd.extend(["--arch", target_arch])

            # Pass devices to VM via --disk
            # They will appear as /dev/vda, /dev/vdb, etc. in the VM
            if device_manager:
                disk_args = device_manager.get_vng_disk_args()
                cmd.extend(disk_args)

            # Add any extra arguments
            if extra_args:
                cmd.extend(extra_args)

            # Make fstests directory available in VM with read-write overlay
            # Using --overlay-rwdir creates a writable overlay in the VM without modifying the host
            cmd.extend(["--overlay-rwdir", str(fstests_path)])

            # Mount results directory as read-write to persist test results
            # This allows results to survive VM crashes and be accessible on host
            cmd.extend([f"--rwdir=/tmp/results={results_dir}"])

            # Execute the test script
            cmd.extend(["--", "bash", str(script_file)])

            # Run with PTY (with real-time progress logging)
            logger.info(f"✓ Loop devices created: {len(created_loop_devices)}")
            logger.info(f"Booting kernel and running fstests... (timeout: {timeout}s)")
            logger.info("  Progress updates will be logged every 10 seconds")
            # Flush before long operation
            for handler in logger.handlers:
                handler.flush()
            description = f"fstests {fstype} on {self.kernel_path.name}"
            exit_code, output, progress_messages, log_file = await _run_with_pty_async(
                cmd, self.kernel_path, timeout, emit_output=True, description=description
            )

            duration = time.time() - start_time

            # Fix permissions on results directory (files may be owned by root from VM)
            # This ensures host user can read/modify results
            try:
                uid = os.getuid()
                gid = os.getgid()
                subprocess.run(
                    ["sudo", "chown", "-R", f"{uid}:{gid}", str(results_dir)],
                    check=False,  # Don't fail if sudo not available
                    capture_output=True,
                )
            except Exception as e:
                logger.warning(f"Could not fix permissions on results: {e}")

            # Parse the fstests output to extract results
            # Prefer reading from check.log file if it exists (cleaner than console output)
            fstests_manager = FstestsManager(fstests_path)
            check_log = results_dir / "check.log"
            fstests_result = fstests_manager.parse_check_output(output, check_log=check_log)

            # Also analyze dmesg for kernel issues
            errors, warnings, panics, oops = DmesgParser.analyze_dmesg(output)

            # Store result for later comparison
            self._last_fstests_result = fstests_result

            # Determine if boot actually completed
            # Boot is considered "completed" if vng ran successfully enough to actually boot the kernel
            # Exit codes: 0 = success, 1 = tests failed (but kernel booted), 2+ = vng failed to start, -1 = timeout
            boot_completed = exit_code == 0 or exit_code == 1

            # Log file was already written during execution by _run_with_pty_async
            boot_success = (
                boot_completed and len(panics) == 0
            )  # Boot succeeded if completed without panics

            # Log completion
            if boot_success:
                logger.info(f"✓ Kernel boot and fstests completed successfully in {duration:.1f}s")
                if fstests_result:
                    logger.info(
                        f"  Tests: {fstests_result.passed} passed, {fstests_result.failed} failed, {fstests_result.notrun} not run"
                    )
            else:
                logger.error(f"✗ Kernel boot or fstests failed after {duration:.1f}s")
                logger.error(f"  Panics: {len(panics)}, Oops: {len(oops)}, Errors: {len(errors)}")
            logger.info(f"Boot log saved: {log_file}")
            logger.info(f"Test results directory: {results_dir}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            # Check if this was a timeout
            timeout_occurred = exit_code == -1 and "timed out" in output.lower()

            boot_result = BootResult(
                success=boot_success,
                duration=duration,
                boot_completed=boot_completed,
                errors=errors,
                warnings=warnings,
                panics=panics,
                oops=oops,
                dmesg_output=output,
                exit_code=exit_code,
                timeout_occurred=timeout_occurred,
                log_file_path=log_file,
                progress_log=progress_messages,
            )

            return (boot_result, fstests_result)

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot test failed with exception: {e}")

            # Try to find the most recent log file (might have been created before exception)
            log_file = None
            try:
                if BOOT_LOG_DIR.exists():
                    recent_logs = sorted(
                        BOOT_LOG_DIR.glob("boot-*-running.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if recent_logs and (time.time() - recent_logs[0].stat().st_mtime) < 60:
                        log_file = recent_logs[0]
                        try:
                            error_log = log_file.parent / log_file.name.replace(
                                "-running.log", "-error.log"
                            )
                            log_file.rename(error_log)
                            log_file = error_log
                        except Exception:
                            pass
            except Exception:
                pass

            # Create error log if we couldn't find one
            if not log_file:
                error_output = f"ERROR: {str(e)}"
                log_file = _save_boot_log(error_output, success=False)

            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            return (
                BootResult(
                    success=False,
                    duration=duration,
                    boot_completed=False,
                    dmesg_output=f"ERROR: {str(e)}",
                    exit_code=-1,
                    log_file_path=log_file,
                ),
                None,
            )

        finally:
            # Cleanup temp script
            if script_file and script_file.exists():
                try:
                    script_file.unlink()
                except OSError:
                    pass

            # Cleanup device pool volumes if we used them
            if pool_name and pool_session_id:
                try:
                    from .device_pool import release_pool_volumes

                    logger.info(f"Releasing device pool volumes (session: {pool_session_id})")
                    release_pool_volumes(
                        pool_name=pool_name,
                        session_id=pool_session_id,
                        keep_volumes=False,
                        config_dir=Path.home() / ".kerneldev-mcp",
                    )
                    logger.info("✓ Device pool volumes released")
                except Exception as e:
                    logger.warning(f"Failed to release device pool volumes: {e}")

            # Cleanup devices
            if device_manager:
                device_manager.cleanup()

    async def boot_with_custom_command(
        self,
        fstests_path: Path,
        command: Optional[str] = None,
        script_file: Optional[Path] = None,
        fstype: str = "ext4",
        timeout: int = 300,
        memory: str = "4G",
        cpus: int = 4,
        custom_devices: Optional[List[DeviceSpec]] = None,
        use_default_devices: bool = True,
        cross_compile: Optional["CrossCompileConfig"] = None,
        force_9p: bool = False,
        io_scheduler: str = "mq-deadline",
        use_tmpfs: bool = False,
        extra_args: Optional[List[str]] = None,
    ) -> BootResult:
        """Boot kernel and run custom command/script with fstests device environment.

        This tool sets up the same device environment as fstests_vm_boot_and_run but
        allows you to run arbitrary commands or scripts instead of fstests.

        Args:
            fstests_path: Path to fstests installation (for environment setup)
            command: Shell command to run, or None for interactive shell
            script_file: Path to local script file to upload and execute
            fstype: Filesystem type to format devices with (e.g., "ext4", "btrfs", "xfs")
            timeout: Total timeout in seconds
            memory: Memory size for VM
            cpus: Number of CPUs
            custom_devices: Custom device specifications. If provided, use_default_devices is ignored.
            use_default_devices: If True and custom_devices is None, use 7 default fstests devices.
                                 If False and custom_devices is None, no devices are attached.
            cross_compile: Cross-compilation configuration
            force_9p: Force use of 9p filesystem instead of virtio-fs
            io_scheduler: IO scheduler to use for block devices (default: "mq-deadline")
                         Valid values: "mq-deadline", "none", "bfq", "kyber"
            use_tmpfs: Only affects default devices (when custom_devices is None).
                      Use tmpfs for loop device backing files (faster but uses more RAM)
            extra_args: Additional arguments to pass to vng

        Security Note:
            The command and script_file parameters are executed without sanitization.
            This is intentional for flexibility in kernel development and testing.
            Only use with trusted inputs. Commands run in an isolated VM environment.

        Returns:
            BootResult with command execution output
        """
        logger.info("=" * 60)
        logger.info(f"Starting kernel boot with custom command: {self.kernel_path}")
        logger.info(f"Config: fstype={fstype}, memory={memory}, cpus={cpus}, timeout={timeout}s")
        if command:
            logger.info(f"Command: {command}")
        elif script_file:
            logger.info(f"Script: {script_file}")
        else:
            logger.info("Mode: Interactive shell")
        logger.info(f"IO scheduler: {io_scheduler}")
        if use_tmpfs:
            logger.info("Using tmpfs for loop device backing files (faster but uses more RAM)")
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if force_9p:
            logger.info("Using 9p filesystem (virtio-fs disabled)")

        start_time = time.time()

        # Determine which devices to use
        device_specs = None
        if custom_devices is not None:
            # Use custom devices
            device_specs = custom_devices
            logger.info(f"Using {len(custom_devices)} custom device(s)")
        elif use_default_devices:
            # Use default 7 devices from profile
            profile = DeviceProfile.get_profile("fstests_default", use_tmpfs=use_tmpfs)
            device_specs = profile.devices
            logger.info(f"Using default fstests device profile (7 devices, tmpfs={use_tmpfs})")
        else:
            # No devices
            device_specs = []
            logger.info("No devices will be attached")

        # Track created loop devices for cleanup (for backwards compatibility)
        created_loop_devices: List[Tuple[str, Path]] = []

        # Check virtme-ng is available
        if not self.check_virtme_ng():
            logger.error("✗ virtme-ng not found")
            logger.info("=" * 60)
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output="ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng",
                exit_code=-1,
            )

        # Auto-detect kernel architecture if not explicitly specified
        target_arch = self._resolve_target_architecture(cross_compile)

        # Check QEMU is available for target architecture
        qemu_available, qemu_info = self.check_qemu(target_arch)
        if not qemu_available:
            logger.error(f"✗ QEMU not found: {qemu_info}")
            logger.info("=" * 60)
            install_instructions = (
                "Install QEMU for your distribution:\n"
                "  Fedora/RHEL: sudo dnf install qemu-system-x86\n"
                "  Ubuntu/Debian: sudo apt-get install qemu-system-x86\n"
                "  Arch: sudo pacman -S qemu-system-x86"
            )
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: {qemu_info}\n\n{install_instructions}",
                exit_code=-1,
            )
        else:
            logger.info(f"✓ QEMU available: {qemu_info}")

        # Check if kernel is built
        vmlinux = self.kernel_path / "vmlinux"
        if not vmlinux.exists():
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: Kernel not built. vmlinux not found at {vmlinux}",
                exit_code=-1,
            )

        # Check fstests path exists (for environment setup)
        fstests_path = Path(fstests_path)
        if not fstests_path.exists():
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: fstests path does not exist: {fstests_path}",
                exit_code=-1,
            )

        # Check script file exists if provided
        if script_file:
            script_file = Path(script_file)
            if not script_file.exists():
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Script file does not exist: {script_file}",
                    exit_code=-1,
                )

        # Setup devices using VMDeviceManager
        device_manager = None
        if device_specs:
            device_manager = VMDeviceManager()
            success, error, device_paths = await device_manager.setup_devices(device_specs)
            if not success:
                logger.error(f"✗ Device setup failed: {error}")
                logger.info("=" * 60)
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Device setup failed: {error}",
                    exit_code=-1,
                )
            logger.info(f"✓ Setup {len(device_paths)} device(s)")

            # Track loop devices for backwards compatibility with cleanup code
            created_loop_devices = device_manager.created_loop_devices

        # Create timestamped results directory on host
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        results_base_dir = Path.home() / ".kerneldev-mcp" / "fstests-results"
        results_dir = results_base_dir / f"custom-{timestamp}"
        results_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"✓ Created results directory: {results_dir}")

        # Build the setup and execution script
        # This sets up the same environment as fstests but runs custom command
        setup_script = f"""#!/bin/bash
# Don't exit on error immediately - we want to capture results
set +e

# Show environment
echo "=== Custom Command Setup Start ==="
echo "Kernel: $(uname -r)"
echo "User: $(whoami)"
echo "fstests path: {fstests_path}"
echo "Filesystem type: {fstype}"
echo ""

# Use devices passed from host via --disk
# virtme-ng passes them as virtio block devices: /dev/vda, /dev/vdb, /dev/vdc, etc.
# We have 7 devices: 1 test + 5 scratch pool + 1 log-writes
echo "Setting up passed-through block devices..."
TEST_DEV=/dev/vda
POOL1=/dev/vdb
POOL2=/dev/vdc
POOL3=/dev/vdd
POOL4=/dev/vde
POOL5=/dev/vdf
LOGWRITES_DEV=/dev/vdg

# Verify devices exist
for dev in $TEST_DEV $POOL1 $POOL2 $POOL3 $POOL4 $POOL5 $LOGWRITES_DEV; do
    if [ ! -b "$dev" ]; then
        echo "ERROR: Block device $dev not found"
        echo "Available block devices:"
        ls -l /dev/vd* 2>/dev/null || echo "No /dev/vd* devices found"
        exit 1
    fi
done

echo "TEST_DEV=$TEST_DEV"
echo "SCRATCH_DEV_POOL=$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
echo "LOGWRITES_DEV=$LOGWRITES_DEV"

echo "Setting IO scheduler to '{io_scheduler}' on all devices..."
for dev in $TEST_DEV $POOL1 $POOL2 $POOL3 $POOL4 $POOL5 $LOGWRITES_DEV; do
    devname=$(basename $dev)
    scheduler_file="/sys/block/$devname/queue/scheduler"

    if [ ! -f "$scheduler_file" ]; then
        echo "ERROR: Scheduler file not found: $scheduler_file"
        exit 1
    fi

    # Check if scheduler is available
    available=$(cat "$scheduler_file")
    if ! echo "$available" | grep -qw "{io_scheduler}"; then
        echo "ERROR: IO scheduler '{io_scheduler}' is not available for $dev"
        echo "Available schedulers: $available"
        exit 1
    fi

    # Set the scheduler
    echo "{io_scheduler}" > "$scheduler_file"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to set scheduler '{io_scheduler}' on $dev"
        exit 1
    fi

    # Verify it was set
    current=$(cat "$scheduler_file" | grep -o '\[.*\]' | tr -d '[]')
    if [ "$current" != "{io_scheduler}" ]; then
        echo "ERROR: Failed to verify scheduler on $dev (expected '{io_scheduler}', got '$current')"
        exit 1
    fi

    echo "  ✓ $dev: {io_scheduler}"
done
echo ""

# Format filesystems
echo "Formatting filesystems as {fstype}..."
if [ "{fstype}" = "btrfs" ]; then
    mkfs.btrfs -f $TEST_DEV > /dev/null 2>&1
else
    mkfs.ext4 -F $TEST_DEV > /dev/null 2>&1
fi

# Create mount points in /tmp
echo "Creating mount points..."
mkdir -p /tmp/test /tmp/scratch

# Export environment variables for use by custom command
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
export LOGWRITES_DEV=$LOGWRITES_DEV
export FSTYP={fstype}
export RESULT_BASE=/tmp/results

# Create fstests local.config (for compatibility if using fstests utilities)
if [ -d "{fstests_path}" ]; then
    cat > {fstests_path}/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
export LOGWRITES_DEV=$LOGWRITES_DEV
export FSTYP={fstype}
export RESULT_BASE=/tmp/results
EOF
    echo "✓ Created fstests local.config"
fi

echo "=== Environment Ready ==="
echo ""
"""

        # Add the user's command/script or interactive shell
        if script_file:
            # Copy script contents into the execution script
            script_contents = script_file.read_text()
            setup_script += f"""
# Execute uploaded script
echo "=== Executing Custom Script: {script_file.name} ==="
{script_contents}
exit_code=$?
echo "=== Script Execution Complete ==="
echo "Exit code: $exit_code"
exit $exit_code
"""
        elif command:
            # Execute the provided command
            setup_script += f"""
echo "=== Executing Custom Command ==="
echo "Command: {command}"
echo ""
{command}
exit_code=$?
echo ""
echo "=== Command Execution Complete ==="
echo "Exit code: $exit_code"
exit $exit_code
"""
        else:
            # Interactive shell
            setup_script += """
echo "=== Launching Interactive Shell ==="
echo "Environment variables are set:"
echo "  TEST_DEV=$TEST_DEV"
echo "  SCRATCH_DEV_POOL=$SCRATCH_DEV_POOL"
echo "  LOGWRITES_DEV=$LOGWRITES_DEV"
echo "  FSTYP=$FSTYP"
echo "  RESULT_BASE=$RESULT_BASE"
echo ""
echo "Devices are ready. Run 'exit' when done."
echo ""
/bin/bash
exit_code=$?
exit $exit_code
"""

        # Write script to temp file
        vm_script_file = Path("/tmp/run-custom-command.sh")
        vm_script_file.write_text(setup_script)
        vm_script_file.chmod(0o755)

        # Build vng command
        cmd = ["vng", "--verbose"]

        # Force 9p if requested
        if force_9p:
            cmd.append("--force-9p")

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add architecture if specified or auto-detected
        if target_arch:
            cmd.extend(["--arch", target_arch])

        # Pass devices to VM via --disk
        # They will appear as /dev/vda, /dev/vdb, etc. in the VM
        if device_manager:
            disk_args = device_manager.get_vng_disk_args()
            cmd.extend(disk_args)

        # Add any extra arguments
        if extra_args:
            cmd.extend(extra_args)

        # Make fstests directory available in VM with read-write overlay
        cmd.extend(["--overlay-rwdir", str(fstests_path)])

        # Mount results directory as read-write to persist results
        cmd.extend([f"--rwdir=/tmp/results={results_dir}"])

        # Execute the script
        cmd.extend(["--", "bash", str(vm_script_file)])

        # Run with PTY (with real-time progress logging)
        logger.info(f"✓ Loop devices created: {len(created_loop_devices)}")
        logger.info(f"Booting kernel and running custom command... (timeout: {timeout}s)")
        logger.info("  Progress updates will be logged every 10 seconds")
        # Flush before long operation
        for handler in logger.handlers:
            handler.flush()

        try:
            mode_desc = (
                "interactive shell"
                if not (command or script_file)
                else ("script" if script_file else "command")
            )
            description = f"custom {mode_desc} on {self.kernel_path.name}"
            exit_code, output, progress_messages, log_file = await _run_with_pty_async(
                cmd, self.kernel_path, timeout, emit_output=True, description=description
            )

            duration = time.time() - start_time

            # Fix permissions on results directory
            try:
                uid = os.getuid()
                gid = os.getgid()
                subprocess.run(
                    ["sudo", "chown", "-R", f"{uid}:{gid}", str(results_dir)],
                    check=False,
                    capture_output=True,
                )
            except Exception as e:
                logger.warning(f"Could not fix permissions on results: {e}")

            # Analyze dmesg for kernel issues
            errors, warnings, panics, oops = DmesgParser.analyze_dmesg(output)

            # Determine if boot actually completed
            boot_completed = exit_code >= 0  # Any non-timeout exit means boot completed

            # Boot succeeded if completed without panics
            boot_success = boot_completed and len(panics) == 0

            # Log completion
            if boot_success:
                logger.info(
                    f"✓ Kernel boot and custom command completed successfully in {duration:.1f}s"
                )
            else:
                logger.error(f"✗ Kernel boot or command failed after {duration:.1f}s")
                logger.error(f"  Panics: {len(panics)}, Oops: {len(oops)}, Errors: {len(errors)}")
            logger.info(f"Boot log saved: {log_file}")
            logger.info(f"Results directory: {results_dir}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            # Check if this was a timeout
            timeout_occurred = exit_code == -1 and "timed out" in output.lower()

            boot_result = BootResult(
                success=boot_success,
                duration=duration,
                boot_completed=boot_completed,
                errors=errors,
                warnings=warnings,
                panics=panics,
                oops=oops,
                dmesg_output=output,
                exit_code=exit_code,
                timeout_occurred=timeout_occurred,
                log_file_path=log_file,
                progress_log=progress_messages,
            )

            return boot_result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot test failed with exception: {e}")

            # Try to find the most recent log file
            log_file = None
            try:
                if BOOT_LOG_DIR.exists():
                    recent_logs = sorted(
                        BOOT_LOG_DIR.glob("boot-*-running.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if recent_logs and (time.time() - recent_logs[0].stat().st_mtime) < 60:
                        log_file = recent_logs[0]
                        try:
                            error_log = log_file.parent / log_file.name.replace(
                                "-running.log", "-error.log"
                            )
                            log_file.rename(error_log)
                            log_file = error_log
                        except Exception:
                            pass
            except Exception:
                pass

            # Create error log if we couldn't find one
            if not log_file:
                error_output = f"ERROR: {str(e)}"
                log_file = _save_boot_log(error_output, success=False)

            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=f"ERROR: {str(e)}",
                exit_code=-1,
                log_file_path=log_file,
            )

        finally:
            # Cleanup temp script
            if vm_script_file.exists():
                try:
                    vm_script_file.unlink()
                except OSError:
                    pass

            # Cleanup devices
            if device_manager:
                device_manager.cleanup()


def format_boot_result(result: BootResult, max_errors: int = 10) -> str:
    """Format boot result for display.

    Args:
        result: BootResult to format
        max_errors: Maximum number of errors to show

    Returns:
        Formatted string
    """
    lines = []
    lines.append(result.summary())
    lines.append("")

    # Always show log file path if available
    if result.log_file_path:
        lines.append(f"Full boot log: {result.log_file_path}")
        lines.append("")

    if result.kernel_version:
        lines.append(f"Kernel version: {result.kernel_version}")
        lines.append("")

    # Show progress log if available (for long-running operations)
    if result.progress_log:
        lines.append("Progress Log:")
        lines.append("=" * 80)
        for msg in result.progress_log:
            lines.append(msg)
        lines.append("=" * 80)
        lines.append("")

    # If boot failed, show last 200 lines of console output
    if not result.boot_completed and result.dmesg_output:
        output_lines = result.dmesg_output.splitlines()
        total_lines = len(output_lines)

        lines.append(f"Console Output (last 200 lines of {total_lines} total):")
        lines.append("=" * 80)

        # Get last 200 lines
        last_lines = output_lines[-200:] if len(output_lines) > 200 else output_lines

        # Add line numbers (starting from actual line number in output)
        start_line_num = max(1, total_lines - len(last_lines) + 1)
        for i, line in enumerate(last_lines, start=start_line_num):
            lines.append(f"{i:5d} | {line}")

        lines.append("=" * 80)
        lines.append("")

    if result.panics:
        lines.append(f"PANICS ({len(result.panics)}):")
        for i, panic in enumerate(result.panics[:max_errors], 1):
            lines.append(f"  {i}. {panic}")
        if len(result.panics) > max_errors:
            lines.append(f"  ... and {len(result.panics) - max_errors} more panics")
        lines.append("")

    if result.oops:
        lines.append(f"OOPS ({len(result.oops)}):")
        for i, oops in enumerate(result.oops[:max_errors], 1):
            lines.append(f"  {i}. {oops}")
        if len(result.oops) > max_errors:
            lines.append(f"  ... and {len(result.oops) - max_errors} more oops")
        lines.append("")

    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for i, error in enumerate(result.errors[:max_errors], 1):
            lines.append(f"  {i}. {error}")
        if len(result.errors) > max_errors:
            lines.append(f"  ... and {len(result.errors) - max_errors} more errors")
        lines.append("")

    if result.warnings and not result.has_critical_issues:
        lines.append(f"Warnings ({len(result.warnings)}):")
        for i, warning in enumerate(result.warnings[:max_errors], 1):
            lines.append(f"  {i}. {warning}")
        if len(result.warnings) > max_errors:
            lines.append(f"  ... and {len(result.warnings) - max_errors} more warnings")
        lines.append("")

    return "\n".join(lines)
