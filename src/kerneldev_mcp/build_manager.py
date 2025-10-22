"""
Kernel build management - building kernels and handling build errors.
"""
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BuildError:
    """Represents a single build error or warning."""

    file: str
    line: Optional[int]
    column: Optional[int]
    error_type: str  # 'error', 'warning', 'fatal'
    message: str
    context: Optional[str] = None  # Source code context if available

    def __str__(self) -> str:
        location = f"{self.file}"
        if self.line:
            location += f":{self.line}"
        if self.column:
            location += f":{self.column}"
        return f"{location}: {self.error_type}: {self.message}"


@dataclass
class BuildResult:
    """Result of a kernel build."""

    success: bool
    duration: float  # seconds
    errors: List[BuildError] = field(default_factory=list)
    warnings: List[BuildError] = field(default_factory=list)
    output: str = ""
    exit_code: int = 0

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def summary(self) -> str:
        """Get a human-readable summary."""
        if self.success:
            return f"✓ Build succeeded in {self.duration:.1f}s ({self.warning_count} warnings)"
        else:
            return f"✗ Build failed in {self.duration:.1f}s ({self.error_count} errors, {self.warning_count} warnings)"


class BuildOutputParser:
    """Parse build output to extract errors and warnings."""

    # Common error patterns
    ERROR_PATTERNS = [
        # GCC/Clang error format: file:line:column: error: message
        re.compile(r"^(.+?):(\d+):(\d+):\s*(error|fatal error):\s*(.+)$"),
        # GCC/Clang warning format
        re.compile(r"^(.+?):(\d+):(\d+):\s*(warning):\s*(.+)$"),
        # Linker errors
        re.compile(r"^(.+?):(\d+):\s*(undefined reference to .+)$"),
        # Make errors
        re.compile(r"^make.*:\s*\*\*\*\s*\[(.+?)\]\s*Error\s+(\d+)"),
    ]

    @staticmethod
    def parse_output(output: str) -> Tuple[List[BuildError], List[BuildError]]:
        """Parse build output and extract errors and warnings.

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []

        for line in output.splitlines():
            parsed = BuildOutputParser._parse_line(line)
            if parsed:
                if parsed.error_type in ('error', 'fatal', 'fatal error'):
                    errors.append(parsed)
                elif parsed.error_type == 'warning':
                    warnings.append(parsed)

        return errors, warnings

    @staticmethod
    def _parse_line(line: str) -> Optional[BuildError]:
        """Parse a single line for errors/warnings."""
        line = line.strip()

        for pattern in BuildOutputParser.ERROR_PATTERNS:
            match = pattern.match(line)
            if match:
                groups = match.groups()

                # Standard error format
                if len(groups) >= 5:
                    file_path = groups[0]
                    line_num = int(groups[1]) if groups[1] else None
                    col_num = int(groups[2]) if groups[2] else None
                    error_type = groups[3]
                    message = groups[4]

                    return BuildError(
                        file=file_path,
                        line=line_num,
                        column=col_num,
                        error_type=error_type,
                        message=message
                    )

                # Make errors
                elif len(groups) >= 2 and 'make' in line:
                    return BuildError(
                        file=groups[0] if groups[0] else "Makefile",
                        line=None,
                        column=None,
                        error_type='error',
                        message=f"Make error (exit {groups[1]})"
                    )

        return None


class KernelBuilder:
    """Manages kernel building operations."""

    def __init__(self, kernel_path: Path):
        """Initialize kernel builder.

        Args:
            kernel_path: Path to kernel source tree
        """
        self.kernel_path = Path(kernel_path)
        if not self.kernel_path.exists():
            raise ValueError(f"Kernel path does not exist: {kernel_path}")

        self._build_thread: Optional[threading.Thread] = None
        self._build_process: Optional[subprocess.Popen] = None
        self._build_running = False

    def build(
        self,
        jobs: Optional[int] = None,
        verbose: bool = False,
        keep_going: bool = False,
        target: str = "all",
        build_dir: Optional[Path] = None,
        make_args: Optional[List[str]] = None,
        timeout: Optional[int] = None
    ) -> BuildResult:
        """Build the kernel.

        Args:
            jobs: Number of parallel jobs (default: number of CPUs)
            verbose: Show detailed build output
            keep_going: Continue building despite errors
            target: Make target (default: 'all')
            build_dir: Output directory for out-of-tree build
            make_args: Additional make arguments
            timeout: Build timeout in seconds

        Returns:
            BuildResult with build status and errors
        """
        start_time = time.time()

        # Build make command
        cmd = ["make"]

        # Out-of-tree build
        if build_dir:
            cmd.extend([f"O={build_dir}"])

        # Parallel jobs
        if jobs is None:
            jobs = os.cpu_count() or 1
        cmd.extend([f"-j{jobs}"])

        # Verbose output
        if verbose:
            cmd.append("V=1")

        # Keep going
        if keep_going:
            cmd.append("-k")

        # Additional args
        if make_args:
            cmd.extend(make_args)

        # Target
        cmd.append(target)

        # Run build
        try:
            result = subprocess.run(
                cmd,
                cwd=self.kernel_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            duration = time.time() - start_time

            # Parse output for errors
            combined_output = result.stdout + result.stderr
            errors, warnings = BuildOutputParser.parse_output(combined_output)

            return BuildResult(
                success=(result.returncode == 0),
                duration=duration,
                errors=errors,
                warnings=warnings,
                output=combined_output,
                exit_code=result.returncode
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            output = ""
            if e.stdout:
                output += e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout
            if e.stderr:
                output += e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr

            errors, warnings = BuildOutputParser.parse_output(output)
            errors.append(BuildError(
                file="<build>",
                line=None,
                column=None,
                error_type="fatal",
                message=f"Build timeout after {timeout}s"
            ))

            return BuildResult(
                success=False,
                duration=duration,
                errors=errors,
                warnings=warnings,
                output=output,
                exit_code=-1
            )

        except Exception as e:
            duration = time.time() - start_time
            return BuildResult(
                success=False,
                duration=duration,
                errors=[BuildError(
                    file="<build>",
                    line=None,
                    column=None,
                    error_type="fatal",
                    message=str(e)
                )],
                warnings=[],
                output="",
                exit_code=-1
            )

    def clean(self, target: str = "clean", build_dir: Optional[Path] = None) -> bool:
        """Clean build artifacts.

        Args:
            target: Clean target ('clean', 'mrproper', 'distclean')
            build_dir: Build directory for out-of-tree builds

        Returns:
            True if successful
        """
        cmd = ["make", target]
        if build_dir:
            cmd.append(f"O={build_dir}")

        try:
            subprocess.run(
                cmd,
                cwd=self.kernel_path,
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def get_kernel_version(self) -> Optional[str]:
        """Get kernel version from Makefile.

        Returns:
            Kernel version string or None
        """
        try:
            result = subprocess.run(
                ["make", "kernelversion"],
                cwd=self.kernel_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def check_config(self) -> bool:
        """Check if kernel is configured.

        Returns:
            True if .config exists
        """
        return (self.kernel_path / ".config").exists()

    def prepare_build(self) -> bool:
        """Prepare kernel for building (run scripts_prepare).

        Returns:
            True if successful
        """
        try:
            subprocess.run(
                ["make", "scripts_prepare"],
                cwd=self.kernel_path,
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError:
            return False


def format_build_errors(result: BuildResult, max_errors: int = 10) -> str:
    """Format build errors for display.

    Args:
        result: BuildResult to format
        max_errors: Maximum number of errors to show

    Returns:
        Formatted error string
    """
    lines = []
    lines.append(result.summary())
    lines.append("")

    if result.errors:
        lines.append(f"Errors ({len(result.errors)}):")
        for i, error in enumerate(result.errors[:max_errors], 1):
            lines.append(f"  {i}. {error}")
        if len(result.errors) > max_errors:
            lines.append(f"  ... and {len(result.errors) - max_errors} more errors")
        lines.append("")

    if result.warnings:
        lines.append(f"Warnings ({len(result.warnings)}):")
        for i, warning in enumerate(result.warnings[:max_errors], 1):
            lines.append(f"  {i}. {warning}")
        if len(result.warnings) > max_errors:
            lines.append(f"  ... and {len(result.warnings) - max_errors} more warnings")
        lines.append("")

    return "\n".join(lines)
