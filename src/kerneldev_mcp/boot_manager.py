"""
Kernel boot testing and validation using virtme-ng.
"""
import os
import pty
import re
import select
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

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

    WARNING_PATTERNS = [
        re.compile(r"\bwarning\b", re.IGNORECASE),
        re.compile(r"\bWARN", re.IGNORECASE),
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
                for pattern in DmesgParser.ERROR_PATTERNS:
                    if pattern.search(message):
                        level = "err"
                        break

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


def _run_with_pty(cmd: List[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    """Run a command with a pseudo-terminal.

    This is needed for virtme-ng which requires a valid PTS.

    Args:
        cmd: Command and arguments to run
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        Tuple of (exit_code, output)
    """
    # Create a pseudo-terminal
    master_fd, slave_fd = pty.openpty()

    try:
        # Start the process with the slave PTY
        process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            close_fds=True
        )

        # Close the slave FD in the parent process
        os.close(slave_fd)

        # Read output with timeout
        output = []
        start_time = time.time()

        while True:
            # Check if process is still running
            if process.poll() is not None:
                break

            # Check timeout
            if time.time() - start_time > timeout:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(cmd, timeout, b''.join(output))

            # Try to read with a short timeout
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        output.append(data)
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

        return exit_code, output_str

    finally:
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
        start_time = time.time()

        # Check virtme-ng is available
        if not self.check_virtme_ng():
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
                return BootResult(
                    success=False,
                    duration=time.time() - start_time,
                    boot_completed=False,
                    dmesg_output=f"ERROR: Kernel not built. vmlinux not found at {vmlinux}\nBuild the kernel first or set use_host_kernel=True",
                    exit_code=-1
                )

        # Build vng command
        cmd = ["vng"]

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

        # Run boot test with PTY (virtme-ng requires a valid PTS)
        try:
            exit_code, dmesg_output = _run_with_pty(cmd, self.kernel_path, timeout)

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
                    break

            return BootResult(
                success=(exit_code == 0 and len(panics) == 0),
                duration=duration,
                boot_completed=(exit_code == 0),
                kernel_version=kernel_version,
                errors=errors,
                warnings=warnings,
                panics=panics,
                oops=oops,
                dmesg_output=dmesg_output,
                exit_code=exit_code,
                timeout_occurred=False
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            output = ""
            if e.output:
                output = e.output.decode() if isinstance(e.output, bytes) else e.output

            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=output + f"\n\nERROR: Boot test timed out after {timeout}s",
                exit_code=-1,
                timeout_occurred=True
            )

        except Exception as e:
            duration = time.time() - start_time
            return BootResult(
                success=False,
                duration=duration,
                boot_completed=False,
                dmesg_output=f"ERROR: {str(e)}",
                exit_code=-1
            )


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

    if result.kernel_version:
        lines.append(f"Kernel version: {result.kernel_version}")
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
