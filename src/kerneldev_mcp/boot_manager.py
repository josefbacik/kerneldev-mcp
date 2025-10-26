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

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add cross-compilation architecture if specified
        if cross_compile:
            cmd.extend(["--arch", cross_compile.arch])

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
        force_9p: bool = False
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
        if cross_compile:
            logger.info(f"Cross-compile arch: {cross_compile.arch}")
        if force_9p:
            logger.info("Using 9p filesystem (virtio-fs disabled)")

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

        # Add memory and CPU options
        cmd.extend(["--memory", memory])
        cmd.extend(["--cpus", str(cpus)])

        # Add cross-compilation architecture if specified
        if cross_compile:
            cmd.extend(["--arch", cross_compile.arch])

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

            # Save boot log
            boot_success = (exit_code == 0 or exit_code == 1) and len(panics) == 0  # exit 1 is OK if tests failed
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
                boot_completed=True,
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
