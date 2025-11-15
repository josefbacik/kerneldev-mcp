"""
Baseline management for fstests - tracking, comparison, and regression detection.
"""
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime

from .fstests_manager import TestResult, FstestsRunResult


@dataclass
class BaselineMetadata:
    """Metadata for a baseline."""

    name: str
    created_at: str  # ISO format timestamp
    kernel_version: Optional[str] = None
    config_hash: Optional[str] = None
    fstype: str = "ext4"
    description: Optional[str] = None
    test_selection: Optional[str] = None  # e.g., "-g quick"


@dataclass
class Baseline:
    """A stored baseline with test results."""

    metadata: BaselineMetadata
    results: FstestsRunResult
    baseline_dir: Path

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation
        """
        return {
            "metadata": asdict(self.metadata),
            "results": {
                "success": self.results.success,
                "total_tests": self.results.total_tests,
                "passed": self.results.passed,
                "failed": self.results.failed,
                "notrun": self.results.notrun,
                "duration": self.results.duration,
                "test_results": [
                    {
                        "test_name": t.test_name,
                        "status": t.status,
                        "duration": t.duration,
                        "failure_reason": t.failure_reason
                    }
                    for t in self.results.test_results
                ]
            }
        }

    @staticmethod
    def from_dict(data: Dict, baseline_dir: Path) -> "Baseline":
        """Create Baseline from dictionary.

        Args:
            data: Dictionary representation
            baseline_dir: Path to baseline directory

        Returns:
            Baseline instance
        """
        metadata = BaselineMetadata(**data["metadata"])

        # Reconstruct TestResult objects
        test_results = [
            TestResult(
                test_name=t["test_name"],
                status=t["status"],
                duration=t["duration"],
                failure_reason=t.get("failure_reason")
            )
            for t in data["results"]["test_results"]
        ]

        results = FstestsRunResult(
            success=data["results"]["success"],
            total_tests=data["results"]["total_tests"],
            passed=data["results"]["passed"],
            failed=data["results"]["failed"],
            notrun=data["results"]["notrun"],
            test_results=test_results,
            duration=data["results"]["duration"]
        )

        return Baseline(metadata=metadata, results=results, baseline_dir=baseline_dir)


@dataclass
class ComparisonResult:
    """Result of comparing two test runs."""

    new_failures: List[TestResult] = field(default_factory=list)
    new_passes: List[TestResult] = field(default_factory=list)
    still_failing: List[TestResult] = field(default_factory=list)
    still_passing: List[TestResult] = field(default_factory=list)
    new_notrun: List[TestResult] = field(default_factory=list)

    @property
    def regression_detected(self) -> bool:
        """Whether any regressions were detected."""
        return len(self.new_failures) > 0

    @property
    def regression_count(self) -> int:
        """Number of regressions (new failures)."""
        return len(self.new_failures)

    @property
    def improvement_count(self) -> int:
        """Number of improvements (new passes)."""
        return len(self.new_passes)

    def summary(self) -> str:
        """Get human-readable summary.

        Returns:
            Summary string
        """
        if self.regression_count == 0 and self.improvement_count == 0:
            return "✓ No regressions detected - results match baseline"

        lines = []

        if self.regression_count > 0:
            lines.append(f"✗ REGRESSION DETECTED: {self.regression_count} new failure(s)")
        else:
            lines.append("✓ No new failures")

        if self.improvement_count > 0:
            lines.append(f"✓ {self.improvement_count} test(s) now passing")

        if self.still_failing:
            lines.append(f"⚠ {len(self.still_failing)} test(s) still failing (pre-existing)")

        return " | ".join(lines)


class BaselineManager:
    """Manages baselines for fstests results."""

    def __init__(self, storage_dir: Optional[Path] = None):
        """Initialize baseline manager.

        Args:
            storage_dir: Directory for storing baselines (default: ~/.kerneldev-mcp/fstests-baselines)
        """
        if storage_dir is None:
            self.storage_dir = Path.home() / ".kerneldev-mcp" / "fstests-baselines"
        else:
            self.storage_dir = Path(storage_dir)

        # Create storage directory
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_baseline_dir(self, baseline_name: str) -> Path:
        """Get path to baseline directory.

        Args:
            baseline_name: Name of baseline

        Returns:
            Path to baseline directory
        """
        # Sanitize name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in baseline_name)
        return self.storage_dir / safe_name

    def save_baseline(
        self,
        baseline_name: str,
        results: FstestsRunResult,
        kernel_version: Optional[str] = None,
        config_hash: Optional[str] = None,
        fstype: str = "ext4",
        description: Optional[str] = None,
        test_selection: Optional[str] = None
    ) -> Baseline:
        """Save a baseline.

        Args:
            baseline_name: Name for the baseline
            results: Test results to save
            kernel_version: Kernel version string
            config_hash: Hash of kernel config
            fstype: Filesystem type
            description: Optional description
            test_selection: Test selection used (e.g., "-g quick")

        Returns:
            Saved Baseline object
        """
        # Create baseline directory
        baseline_dir = self._get_baseline_dir(baseline_name)
        baseline_dir.mkdir(parents=True, exist_ok=True)

        # Create metadata
        metadata = BaselineMetadata(
            name=baseline_name,
            created_at=datetime.now().isoformat(),
            kernel_version=kernel_version,
            config_hash=config_hash,
            fstype=fstype,
            description=description,
            test_selection=test_selection
        )

        baseline = Baseline(metadata=metadata, results=results, baseline_dir=baseline_dir)

        # Save to JSON
        json_file = baseline_dir / "baseline.json"
        with json_file.open("w") as f:
            json.dump(baseline.to_dict(), f, indent=2)

        # Copy check.log if available
        if results.check_log and results.check_log.exists():
            shutil.copy2(results.check_log, baseline_dir / "check.log")

        return baseline

    def load_baseline(self, baseline_name: str) -> Optional[Baseline]:
        """Load a baseline.

        Args:
            baseline_name: Name of baseline to load

        Returns:
            Baseline object or None if not found
        """
        baseline_dir = self._get_baseline_dir(baseline_name)
        json_file = baseline_dir / "baseline.json"

        if not json_file.exists():
            return None

        try:
            with json_file.open() as f:
                data = json.load(f)

            return Baseline.from_dict(data, baseline_dir)

        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def list_baselines(self) -> List[BaselineMetadata]:
        """List all available baselines.

        Returns:
            List of baseline metadata
        """
        baselines = []

        for baseline_dir in self.storage_dir.iterdir():
            if not baseline_dir.is_dir():
                continue

            json_file = baseline_dir / "baseline.json"
            if not json_file.exists():
                continue

            try:
                with json_file.open() as f:
                    data = json.load(f)

                metadata = BaselineMetadata(**data["metadata"])
                baselines.append(metadata)

            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        # Sort by creation time, newest first
        baselines.sort(key=lambda m: m.created_at, reverse=True)

        return baselines

    def delete_baseline(self, baseline_name: str) -> bool:
        """Delete a baseline.

        Args:
            baseline_name: Name of baseline to delete

        Returns:
            True if successful
        """
        baseline_dir = self._get_baseline_dir(baseline_name)

        if not baseline_dir.exists():
            return False

        try:
            shutil.rmtree(baseline_dir)
            return True
        except OSError:
            return False

    def compare_results(
        self,
        current_results: FstestsRunResult,
        baseline: Baseline
    ) -> ComparisonResult:
        """Compare current results against a baseline.

        Args:
            current_results: Current test results
            baseline: Baseline to compare against

        Returns:
            ComparisonResult with detailed comparison
        """
        # Build test name -> result maps
        current_map: Dict[str, TestResult] = {
            t.test_name: t for t in current_results.test_results
        }
        baseline_map: Dict[str, TestResult] = {
            t.test_name: t for t in baseline.results.test_results
        }

        # Get test names
        current_tests = set(current_map.keys())
        baseline_tests = set(baseline_map.keys())

        # Analyze results
        new_failures = []
        new_passes = []
        still_failing = []
        still_passing = []
        new_notrun = []

        # Check all tests in current results
        for test_name in current_tests:
            current = current_map[test_name]

            if test_name in baseline_map:
                baseline_test = baseline_map[test_name]

                # Compare status
                if current.status == "failed" and baseline_test.status == "passed":
                    # REGRESSION: Was passing, now failing
                    new_failures.append(current)
                elif current.status == "passed" and baseline_test.status == "failed":
                    # IMPROVEMENT: Was failing, now passing
                    new_passes.append(current)
                elif current.status == "failed" and baseline_test.status == "failed":
                    # Still failing (pre-existing failure)
                    still_failing.append(current)
                elif current.status == "passed" and baseline_test.status == "passed":
                    # Still passing
                    still_passing.append(current)
                elif current.status == "notrun" and baseline_test.status != "notrun":
                    # Now not run (was run before)
                    new_notrun.append(current)

            else:
                # Test not in baseline
                if current.status == "failed":
                    # New test that's failing
                    new_failures.append(current)
                elif current.status == "notrun":
                    new_notrun.append(current)

        return ComparisonResult(
            new_failures=new_failures,
            new_passes=new_passes,
            still_failing=still_failing,
            still_passing=still_passing,
            new_notrun=new_notrun
        )

    def generate_exclude_list(self, baseline: Baseline, output_file: Path) -> int:
        """Generate exclude list from baseline failures.

        This creates an exclude file containing all tests that failed in the baseline,
        which can be used with fstests -E option to focus on new failures only.

        Args:
            baseline: Baseline with known failures
            output_file: Path to write exclude list

        Returns:
            Number of tests added to exclude list
        """
        failed_tests = [
            t.test_name for t in baseline.results.test_results
            if t.status == "failed"
        ]

        try:
            with output_file.open("w") as f:
                f.write("# Exclude list generated from baseline\n")
                f.write(f"# Baseline: {baseline.metadata.name}\n")
                f.write(f"# Created: {baseline.metadata.created_at}\n")
                f.write(f"# Failed tests: {len(failed_tests)}\n")
                f.write("\n")
                for test in failed_tests:
                    f.write(f"{test}\n")

            return len(failed_tests)

        except OSError:
            return 0


def format_comparison_result(
    comparison: ComparisonResult,
    baseline_name: str,
    max_shown: int = 20
) -> str:
    """Format comparison result for display.

    Args:
        comparison: ComparisonResult to format
        baseline_name: Name of baseline compared against
        max_shown: Maximum number of items to show per category

    Returns:
        Formatted string
    """
    lines = []
    lines.append(f"Comparison against baseline: {baseline_name}")
    lines.append("=" * 80)
    lines.append(comparison.summary())
    lines.append("")

    # Show new failures (regressions)
    if comparison.new_failures:
        lines.append(f"NEW FAILURES - REGRESSIONS ({len(comparison.new_failures)}):")
        for i, test in enumerate(comparison.new_failures[:max_shown], 1):
            lines.append(f"  {i}. {test.test_name}")
            if test.failure_reason:
                lines.append(f"     {test.failure_reason}")
        if len(comparison.new_failures) > max_shown:
            lines.append(f"  ... and {len(comparison.new_failures) - max_shown} more")
        lines.append("")

    # Show new passes (improvements)
    if comparison.new_passes:
        lines.append(f"NEW PASSES - IMPROVEMENTS ({len(comparison.new_passes)}):")
        for i, test in enumerate(comparison.new_passes[:max_shown], 1):
            lines.append(f"  {i}. {test.test_name}")
        if len(comparison.new_passes) > max_shown:
            lines.append(f"  ... and {len(comparison.new_passes) - max_shown} more")
        lines.append("")

    # Show still failing (pre-existing failures)
    if comparison.still_failing:
        lines.append(f"STILL FAILING - PRE-EXISTING ({len(comparison.still_failing)}):")
        for i, test in enumerate(comparison.still_failing[:5], 1):
            lines.append(f"  {i}. {test.test_name}")
        if len(comparison.still_failing) > 5:
            lines.append(f"  ... and {len(comparison.still_failing) - 5} more")
        lines.append("")

    # Show recommendation
    if comparison.regression_detected:
        lines.append("⚠ ACTION REQUIRED:")
        lines.append("  Regressions detected! Investigate new failures before submitting patches.")
    else:
        lines.append("✓ SAFE TO PROCEED:")
        lines.append("  No regressions detected. Your changes don't introduce new test failures.")

    return "\n".join(lines)
