"""
Kernel boot testing and validation using virtme-ng.
"""
import logging
import os
import pty
import re
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

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


class DmesgParser:
    """Parse and analyze dmesg output."""

    # Kernel log levels
    LOG_LEVELS = {
        0: "emerg",   # System is unusable
        1: "alert",   # Action must be taken immediately
        2: "crit",    # Critical conditions
        3: "err",     # Error conditions
        4: "warn",    # Warning conditions
        5: "notice",  # Normal but significant
        6: "info",    # Informational
        7: "debug",   # Debug-level messages
    }

    # Patterns for detecting critical issues
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

    # Patterns to exclude from error detection (known false positives)
    ERROR_EXCLUSIONS = [
        re.compile(r"ignoring", re.IGNORECASE),  # "failed...ignoring" is not an error
        re.compile(r"virtme-ng-init:.*(?:Failed|Permission denied)", re.IGNORECASE),  # userspace init issues
        re.compile(r"PCI: Fatal: No config space access function found", re.IGNORECASE),  # expected in virtme
        re.compile(r"Permission denied", re.IGNORECASE),  # userspace permission issues
        re.compile(r"Failed to read.*tmpfiles\.d", re.IGNORECASE),  # systemd-tmpfile userspace issues
        re.compile(r"Failed to create directory.*Permission denied", re.IGNORECASE),  # userspace directory creation
        re.compile(r"Failed to opendir\(\)", re.IGNORECASE),  # userspace directory access
    ]

    WARNING_PATTERNS = [
        re.compile(r"\bwarning\b", re.IGNORECASE),
        re.compile(r"\bWARN", re.IGNORECASE),
    ]

    # Userspace message prefixes to ignore (not kernel messages)
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
                # Check if this looks like an error, but exclude false positives
                is_error = False
                for pattern in DmesgParser.ERROR_PATTERNS:
                    if pattern.search(message):
                        # Check if this matches any exclusion patterns
                        is_excluded = any(excl.search(message) for excl in DmesgParser.ERROR_EXCLUSIONS)
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

        return DmesgMessage(
            timestamp=timestamp,
            level=level,
            subsystem=subsystem,
            message=message
        )

    @staticmethod
    def analyze_dmesg(dmesg_text: str) -> Tuple[List[DmesgMessage], List[DmesgMessage],
                                                  List[DmesgMessage], List[DmesgMessage]]:
        """Analyze dmesg output and categorize messages.

        Returns:
            Tuple of (errors, warnings, panics, oops)
        """
        errors = []
        warnings = []
        panics = []
        oops = []

        for line in dmesg_text.splitlines():
            # Skip userspace messages (not kernel messages)
            if any(prefix in line for prefix in DmesgParser.USERSPACE_PREFIXES):
                continue

            # Skip lines that don't start with timestamp (likely continuation lines from userspace)
            stripped = line.strip()
            if stripped and not stripped.startswith('[') and not stripped.startswith('<'):
                # This is likely a continuation line from userspace output
                continue

            msg = DmesgParser.parse_dmesg_line(line)
            if not msg:
                continue

            # Check for panics
            for pattern in DmesgParser.PANIC_PATTERNS:
                if pattern.search(msg.message):
                    panics.append(msg)
                    break

            # Check for oops
            for pattern in DmesgParser.OOPS_PATTERNS:
                if pattern.search(msg.message):
                    oops.append(msg)
                    break

            # Categorize by level
            if msg.level in ("emerg", "alert", "crit", "err"):
                errors.append(msg)
            elif msg.level == "warn":
                warnings.append(msg)

        return errors, warnings, panics, oops


# Boot log management
BOOT_LOG_DIR = Path("/tmp/kerneldev-boot-logs")

# Host loop device management for fstests
HOST_LOOP_WORK_DIR = Path("/var/tmp/kerneldev-loop-devices")


def _create_host_loop_device(size: str, name: str) -> Tuple[Optional[str], Optional[Path]]:
    """Create a loop device on the host for passing to VM.

    Args:
        size: Size of device (e.g., "10G")
        name: Name for the backing file

    Returns:
        Tuple of (loop_device_path, backing_file_path) or (None, None) on failure
    """
    # Ensure work directory exists
    HOST_LOOP_WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Create backing file
    backing_file = HOST_LOOP_WORK_DIR / f"{name}.img"

    try:
        # Create sparse file
        subprocess.run(
            ["truncate", "-s", size, str(backing_file)],
            check=True,
            capture_output=True,
            text=True
        )

        # Setup loop device
        result = subprocess.run(
            ["sudo", "losetup", "-f", "--show", str(backing_file)],
            check=True,
            capture_output=True,
            text=True
        )

        loop_dev = result.stdout.strip()

        # Change permissions so current user can access the loop device
        # This is needed because virtme-ng (QEMU) runs as the current user
        try:
            subprocess.run(
                ["sudo", "chmod", "666", loop_dev],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError:
            # If chmod fails, cleanup and return error
            subprocess.run(["sudo", "losetup", "-d", loop_dev], capture_output=True)
            if backing_file.exists():
                backing_file.unlink()
            return None, None

        return loop_dev, backing_file

    except subprocess.CalledProcessError as e:
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
        subprocess.run(
            ["sudo", "losetup", "-d", loop_device],
            capture_output=True,
            timeout=10
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Try to force detach if normal detach fails
        try:
            subprocess.run(
                ["sudo", "losetup", "-D"],  # Detach all unused loop devices
                capture_output=True,
                timeout=10
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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    status = "success" if success else "failure"
    log_file = BOOT_LOG_DIR / f"boot-{timestamp}-{status}.log"

    # Save log
    try:
        log_file.write_text(output, encoding='utf-8')
    except Exception as e:
        # If saving fails, create a minimal error log
        try:
            log_file.write_text(f"Error saving boot log: {e}\n\n{output[:1000]}", encoding='utf-8')
        except Exception:
            pass

    return log_file


def _run_with_pty(cmd: List[str], cwd: Path, timeout: int, emit_output: bool = False) -> Tuple[int, str, List[str]]:
    """Run a command with a pseudo-terminal.

    This is needed for virtme-ng which requires a valid PTS.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        timeout: Timeout in seconds
        emit_output: If True, emit output in real-time to logger (for long operations)

    Returns:
        Tuple of (exit_code, output, progress_messages)
    """
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
            start_new_session=True  # Create new process group
        )

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
                raise subprocess.TimeoutExpired(cmd, timeout, b''.join(output))

            # Emit progress log every 10 seconds if emit_output is enabled
            if emit_output and (time.time() - last_progress_log) > 10:
                progress_msg = f"[{elapsed:.0f}s] Still running ({timeout - elapsed:.0f}s remaining)"
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
                        if any(marker in line for marker in ["===", "ERROR", "FAIL", "btrfs/", "generic/", "xfs/", "ext4/", "FSTYP", "Passed", "Failed"]):
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
                        # Track lines for progress logging
                        if emit_output:
                            try:
                                text = data.decode('utf-8', errors='replace')
                                # Accumulate into line buffer and extract complete lines
                                line_buffer += text

                                # Split on newlines but keep incomplete last line in buffer
                                if '\n' in line_buffer:
                                    parts = line_buffer.split('\n')
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
            except OSError:
                break

        # Wait for process to finish
        exit_code = process.wait()

        # Decode output
        output_str = b''.join(output).decode('utf-8', errors='replace')

        return exit_code, output_str, progress_messages

    finally:
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
            result = subprocess.run(
                ["vng", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
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
                [qemu_binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5
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
                ["file", str(vmlinux_path)],
                capture_output=True,
                text=True,
                timeout=5
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

    def boot_test(
        self,
        timeout: int = 60,
        memory: str = "2G",
        cpus: int = 2,
        cross_compile: Optional["CrossCompileConfig"] = None,
        extra_args: Optional[List[str]] = None,
        use_host_kernel: bool = False
    ) -> BootResult:
        """Boot kernel and validate it works.

        Args:
            timeout: Boot timeout in seconds
            memory: Memory size for VM (e.g., "2G")
            cpus: Number of CPUs for VM
            cross_compile: Cross-compilation configuration
            extra_args: Additional arguments to pass to vng
            use_host_kernel: Use host kernel instead of building from source

        Returns:
            BootResult with boot status and analysis
        """
        logger.info("=" * 60)
        logger.info(f"Starting kernel boot test: {self.kernel_path}")
        logger.info(f"Config: memory={memory}, cpus={cpus}, timeout={timeout}s")
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if use_host_kernel:
            logger.info("Using host kernel (not building from source)")

        start_time = time.time()

        # Cleanup old boot logs
        _cleanup_old_logs()

        # Check virtme-ng is available
        if not self.check_virtme_ng():
            logger.error("✗ virtme-ng not found")
            logger.info("=" * 60)
            return BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output="ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng",
                exit_code=-1
            )

        # Auto-detect kernel architecture if not explicitly specified and not using host kernel
        target_arch = None
        if cross_compile:
            target_arch = cross_compile.arch
        elif not use_host_kernel:
            # Try to detect architecture from vmlinux
            detected_arch = self.detect_kernel_architecture()
            if detected_arch:
                import platform
                host_arch = platform.machine()
                # Normalize host arch names
                if host_arch == "amd64":
                    host_arch = "x86_64"
                elif host_arch == "aarch64":
                    host_arch = "arm64"

                if detected_arch != host_arch:
                    target_arch = detected_arch
                    logger.info(f"✓ Auto-detected kernel architecture: {detected_arch}")
                    logger.info(f"  (different from host: {host_arch})")
                else:
                    logger.info(f"✓ Kernel architecture matches host: {detected_arch}")
            else:
                logger.warning("Could not auto-detect kernel architecture, assuming host architecture")

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
                exit_code=-1
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
                    exit_code=-1
                )

        # Build vng command
        cmd = ["vng", "--verbose"]  # --verbose is critical to capture serial console output
        logger.info(f"Boot command: {' '.join(cmd[:5])}...")  # Don't log full command (may be long)

        # Use --run for host kernel
        if use_host_kernel:
            cmd.append("--run")

        # Force full machine type (not microvm) to ensure virtio-serial is available
        # microvm is too minimal - it lacks PCI, virtio-serial, and other devices needed for testing
        # Use q35 for modern x86_64, or default machine for other architectures
        machine_type = "q35" if not target_arch or target_arch in ["x86_64", "x86"] else None
        if machine_type:
            # Use -M (short form) and equals sign to pass as single argument
            cmd.append(f"--qemu-opts=-M {machine_type}")
            logger.info(f"Using QEMU machine type: {machine_type}")

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add architecture if specified or auto-detected
        if target_arch:
            cmd.extend(["--arch", target_arch])

        # Add any extra arguments
        if extra_args:
            cmd.extend(extra_args)

        # Execute command to get dmesg
        cmd.extend(["--", "dmesg"])

        logger.info("Booting kernel... (this may take a minute)")

        # Run boot test with PTY (virtme-ng requires a valid PTS)
        try:
            exit_code, dmesg_output, _ = _run_with_pty(cmd, self.kernel_path, timeout)

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

            # Save boot log to file
            boot_success = (exit_code == 0 and len(panics) == 0)
            log_file = _save_boot_log(dmesg_output, boot_success)

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
                timeout_occurred=False,
                log_file_path=log_file
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot timeout after {timeout}s (ran for {duration:.1f}s)")
            logger.info("=" * 60)

            output = ""
            if e.output:
                output = e.output.decode() if isinstance(e.output, bytes) else e.output

            full_output = output + f"\n\nERROR: Boot test timed out after {timeout}s"
            log_file = _save_boot_log(full_output, success=False)

            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=full_output,
                exit_code=-1,
                timeout_occurred=True,
                log_file_path=log_file
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot failed with exception: {e}")
            logger.info("=" * 60)

            error_output = f"ERROR: {str(e)}"
            log_file = _save_boot_log(error_output, success=False)

            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=error_output,
                exit_code=-1,
                log_file_path=log_file
            )

    def boot_with_fstests(
        self,
        fstests_path: Path,
        tests: List[str],
        fstype: str = "ext4",
        timeout: int = 300,
        memory: str = "4G",
        cpus: int = 4,
        cross_compile: Optional["CrossCompileConfig"] = None,
        force_9p: bool = False,
        io_scheduler: str = "mq-deadline",
        use_custom_rootfs: bool = False,
        custom_rootfs_path: Optional[Path] = None
    ) -> Tuple[BootResult, Optional[object]]:
        """Boot kernel and run fstests inside VM.

        Args:
            fstests_path: Path to fstests installation
            tests: Tests to run (e.g., ["-g", "quick"])
            fstype: Filesystem type to test (e.g., "ext4", "btrfs", "xfs")
            timeout: Total timeout in seconds
            memory: Memory size for VM
            cpus: Number of CPUs
            cross_compile: Cross-compilation configuration
            force_9p: Force use of 9p filesystem instead of virtio-fs
            io_scheduler: IO scheduler to use for block devices (default: "mq-deadline")
                         Valid values: "mq-deadline", "none", "bfq", "kyber"
            use_custom_rootfs: Use custom test rootfs instead of host filesystem
            custom_rootfs_path: Path to custom rootfs (default: ~/.kerneldev-mcp/test-rootfs)

        Returns:
            Tuple of (BootResult, FstestsRunResult or None)
        """
        # Import here to avoid circular dependency
        from .fstests_manager import FstestsManager, FstestsRunResult

        logger.info("=" * 60)
        logger.info(f"Starting kernel boot with fstests: {self.kernel_path}")
        logger.info(f"Config: fstype={fstype}, memory={memory}, cpus={cpus}, timeout={timeout}s")
        test_args = " ".join(tests) if tests else "-g quick"
        logger.info(f"Tests: {test_args}")
        logger.info(f"IO scheduler: {io_scheduler}")
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if force_9p:
            logger.info("Using 9p filesystem (virtio-fs disabled)")
        if use_custom_rootfs:
            logger.info("Using custom test rootfs (isolated from host)")

        # Validate test arguments
        if tests:
            is_valid, error_msg = FstestsManager.validate_test_args(tests)
            if not is_valid:
                logger.error(f"✗ Invalid test arguments: {error_msg}")
                logger.info("=" * 60)
                return (BootResult(
                    success=False,
                    duration=0.0,
                    boot_completed=False,
                    dmesg_output=f"ERROR: {error_msg}",
                    exit_code=-1
                ), None)

        start_time = time.time()

        # Track created loop devices for cleanup
        created_loop_devices: List[Tuple[str, Path]] = []

        # Check virtme-ng is available
        if not self.check_virtme_ng():
            logger.error("✗ virtme-ng not found")
            logger.info("=" * 60)
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output="ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng",
                exit_code=-1
            ), None)

        # Auto-detect kernel architecture if not explicitly specified
        target_arch = None
        if cross_compile:
            target_arch = cross_compile.arch
        else:
            # Try to detect architecture from vmlinux
            detected_arch = self.detect_kernel_architecture()
            if detected_arch:
                import platform
                host_arch = platform.machine()
                # Normalize host arch names
                if host_arch == "amd64":
                    host_arch = "x86_64"
                elif host_arch == "aarch64":
                    host_arch = "arm64"

                if detected_arch != host_arch:
                    target_arch = detected_arch
                    logger.info(f"✓ Auto-detected kernel architecture: {detected_arch}")
                    logger.info(f"  (different from host: {host_arch})")
                else:
                    logger.info(f"✓ Kernel architecture matches host: {detected_arch}")
            else:
                logger.warning("Could not auto-detect kernel architecture, assuming host architecture")

        # Check and setup custom rootfs if requested
        rootfs_path = None
        if use_custom_rootfs:
            from .rootfs_manager import RootfsManager

            rootfs_mgr = RootfsManager(custom_rootfs_path)

            if not rootfs_mgr.check_exists():
                logger.error(f"✗ Custom rootfs not found at {rootfs_mgr.rootfs_path}")
                logger.info("  Create rootfs with: mcp__kerneldev__create_test_rootfs")
                logger.info("=" * 60)
                return (BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=(
                        f"ERROR: Custom rootfs not found at {rootfs_mgr.rootfs_path}\n"
                        "Create it first using the create_test_rootfs tool."
                    ),
                    exit_code=-1
                ), None)

            is_configured, msg = rootfs_mgr.check_configured()
            if not is_configured:
                logger.error(f"✗ Custom rootfs not properly configured: {msg}")
                logger.info("  Recreate rootfs with: mcp__kerneldev__create_test_rootfs (force=true)")
                logger.info("=" * 60)
                return (BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=(
                        f"ERROR: Custom rootfs not properly configured: {msg}\n"
                        "Recreate it using the create_test_rootfs tool with force=true."
                    ),
                    exit_code=-1
                ), None)

            rootfs_path = rootfs_mgr.rootfs_path
            logger.info(f"✓ Using custom rootfs: {rootfs_path}")
            info = rootfs_mgr.get_info()
            if info.get("size"):
                logger.info(f"  Size: {info['size']}")
            if info.get("users"):
                logger.info(f"  Test users: {', '.join(info['users'])}")

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
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: {qemu_info}\n\n{install_instructions}",
                exit_code=-1
            ), None)
        else:
            logger.info(f"✓ QEMU available: {qemu_info}")

        # Check if kernel is built
        vmlinux = self.kernel_path / "vmlinux"
        if not vmlinux.exists():
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: Kernel not built. vmlinux not found at {vmlinux}",
                exit_code=-1
            ), None)

        # Check fstests is installed
        fstests_path = Path(fstests_path)
        if not fstests_path.exists() or not (fstests_path / "check").exists():
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: fstests not found at {fstests_path}",
                exit_code=-1
            ), None)

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
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=(
                    f"ERROR: fstests is not fully built. Missing binaries: {', '.join(missing_binaries)}\n"
                    f"Run the install_fstests tool to rebuild fstests, or manually run:\n"
                    f"  cd {fstests_path} && ./configure && make -j$(nproc)"
                ),
                exit_code=-1
            ), None)

        # Create loop devices on host for passing to VM
        # We need: 1 test device + 5 scratch pool devices + 1 log-writes device = 7 total
        logger.info("Creating loop devices on host (7 x 10G)...")
        device_size = "10G"
        device_names = ["test", "pool1", "pool2", "pool3", "pool4", "pool5", "logwrites"]

        try:
            for name in device_names:
                loop_dev, backing_file = _create_host_loop_device(device_size, name)
                if not loop_dev:
                    # Cleanup any already created devices
                    for dev, backing in created_loop_devices:
                        _cleanup_host_loop_device(dev, backing)
                    return (BootResult(
                        success=False,
                        duration=time.time() - start_time,
                        boot_completed=False,
                        dmesg_output=f"ERROR: Failed to create host loop device '{name}'\n"
                                     f"Make sure you have sudo access for losetup commands.",
                        exit_code=-1
                    ), None)
                created_loop_devices.append((loop_dev, backing_file))

        except Exception as e:
            # Cleanup on any error
            for dev, backing in created_loop_devices:
                _cleanup_host_loop_device(dev, backing)
            return (BootResult(
                success=False,
                duration=time.time() - start_time,
                boot_completed=False,
                dmesg_output=f"ERROR: Exception while creating loop devices: {e}",
                exit_code=-1
            ), None)

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

# Set IO scheduler on all devices
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

        # Force full machine type (not microvm) to ensure virtio-serial is available
        # microvm is too minimal - it lacks PCI, virtio-serial, and other devices needed for testing
        # Use q35 for modern x86_64, or default machine for other architectures
        machine_type = "q35" if not target_arch or target_arch in ["x86_64", "x86"] else None
        if machine_type:
            # Use -M (short form) and equals sign to pass as single argument
            cmd.append(f"--qemu-opts=-M {machine_type}")
            logger.info(f"Using QEMU machine type: {machine_type}")

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add architecture if specified or auto-detected
        if target_arch:
            cmd.extend(["--arch", target_arch])

        # Add custom rootfs if requested
        if rootfs_path:
            cmd.extend(["--root", str(rootfs_path)])
            logger.info(f"Using custom rootfs: {rootfs_path}")

        # Pass loop devices to VM via --disk
        # They will appear as /dev/sda, /dev/sdb, etc. in the VM
        for loop_dev, _ in created_loop_devices:
            cmd.extend(["--disk", loop_dev])

        # Make fstests directory available in VM (read-write)
        cmd.extend(["--rwdir", str(fstests_path)])

        # Execute the test script
        cmd.extend(["--", "bash", str(script_file)])

        # Run with PTY (with real-time progress logging)
        logger.info(f"✓ Loop devices created: {len(created_loop_devices)}")
        logger.info(f"Booting kernel and running fstests... (timeout: {timeout}s)")
        logger.info("  Progress updates will be logged every 10 seconds")
        # Flush before long operation
        for handler in logger.handlers:
            handler.flush()

        try:
            exit_code, output, progress_messages = _run_with_pty(cmd, self.kernel_path, timeout, emit_output=True)

            duration = time.time() - start_time

            # Parse the fstests output to extract results
            # Prefer reading from check.log file if it exists (cleaner than console output)
            fstests_manager = FstestsManager(fstests_path)
            check_log = fstests_path / "results" / "check.log"
            fstests_result = fstests_manager.parse_check_output(output, check_log=check_log)

            # Also analyze dmesg for kernel issues
            errors, warnings, panics, oops = DmesgParser.analyze_dmesg(output)

            # Store result for later comparison
            self._last_fstests_result = fstests_result

            # Determine if boot actually completed
            # Boot is considered "completed" if vng ran successfully enough to actually boot the kernel
            # Exit codes: 0 = success, 1 = tests failed (but kernel booted), 2+ = vng failed to start
            boot_completed = (exit_code == 0 or exit_code == 1)

            # Save boot log
            boot_success = boot_completed and len(panics) == 0  # Boot succeeded if completed without panics
            log_file = _save_boot_log(output, boot_success)

            # Log completion
            if boot_success:
                logger.info(f"✓ Kernel boot and fstests completed successfully in {duration:.1f}s")
                if fstests_result:
                    logger.info(f"  Tests: {fstests_result.passed} passed, {fstests_result.failed} failed, {fstests_result.notrun} not run")
            else:
                logger.error(f"✗ Kernel boot or fstests failed after {duration:.1f}s")
                logger.error(f"  Panics: {len(panics)}, Oops: {len(oops)}, Errors: {len(errors)}")
            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

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
                timeout_occurred=False,
                log_file_path=log_file,
                progress_log=progress_messages
            )

            return (boot_result, fstests_result)

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot test timed out after {timeout}s (ran for {duration:.1f}s)")
            output = ""
            if e.output:
                output = e.output.decode() if isinstance(e.output, bytes) else e.output

            full_output = output + f"\n\nERROR: Test timed out after {timeout}s"
            log_file = _save_boot_log(full_output, success=False)
            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            return (BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=full_output,
                exit_code=-1,
                timeout_occurred=True,
                log_file_path=log_file
            ), None)

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"✗ Boot test failed with exception: {e}")
            error_output = f"ERROR: {str(e)}"
            log_file = _save_boot_log(error_output, success=False)
            logger.info(f"Boot log saved: {log_file}")
            logger.info("=" * 60)
            # Flush logs
            for handler in logger.handlers:
                handler.flush()

            return (BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=error_output,
                exit_code=-1,
                log_file_path=log_file
            ), None)

        finally:
            # Cleanup temp script
            if script_file.exists():
                try:
                    script_file.unlink()
                except OSError:
                    pass

            # Cleanup host loop devices
            for loop_dev, backing_file in created_loop_devices:
                _cleanup_host_loop_device(loop_dev, backing_file)


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
