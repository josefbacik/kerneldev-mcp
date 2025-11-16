"""
Unit tests for baseline_manager module.
"""

import pytest
from pathlib import Path

from kerneldev_mcp.baseline_manager import (
    BaselineManager,
    Baseline,
    BaselineMetadata,
    ComparisonResult,
    format_comparison_result,
)
from kerneldev_mcp.fstests_manager import TestResult, FstestsRunResult


@pytest.fixture
def baseline_manager(tmp_path):
    """Create BaselineManager with temporary storage."""
    return BaselineManager(storage_dir=tmp_path / "baselines")


@pytest.fixture
def sample_results():
    """Sample test results."""
    return FstestsRunResult(
        success=True,
        total_tests=10,
        passed=8,
        failed=2,
        notrun=0,
        test_results=[
            TestResult("generic/001", "passed", 5.0),
            TestResult("generic/002", "passed", 3.0),
            TestResult("generic/003", "failed", 0.0, "output mismatch"),
            TestResult("generic/004", "passed", 7.0),
            TestResult("generic/005", "failed", 0.0, "timeout"),
            TestResult("generic/006", "passed", 4.0),
            TestResult("generic/007", "passed", 6.0),
            TestResult("generic/008", "passed", 8.0),
            TestResult("generic/009", "passed", 2.0),
            TestResult("generic/010", "passed", 9.0),
        ],
        duration=50.0,
    )


@pytest.fixture
def sample_baseline(baseline_manager, sample_results):
    """Create a sample baseline."""
    return baseline_manager.save_baseline(
        baseline_name="test-baseline",
        results=sample_results,
        kernel_version="6.12-rc1",
        fstype="ext4",
    )


class TestBaselineMetadata:
    """Test BaselineMetadata dataclass."""

    def test_metadata_creation(self):
        """Test creating baseline metadata."""
        metadata = BaselineMetadata(
            name="test", created_at="2024-01-01T12:00:00", kernel_version="6.12-rc1", fstype="ext4"
        )

        assert metadata.name == "test"
        assert metadata.kernel_version == "6.12-rc1"
        assert metadata.fstype == "ext4"

    def test_metadata_defaults(self):
        """Test metadata default values."""
        metadata = BaselineMetadata(name="test", created_at="2024-01-01T12:00:00")

        assert metadata.kernel_version is None
        assert metadata.config_hash is None
        assert metadata.fstype == "ext4"
        assert metadata.description is None


class TestBaseline:
    """Test Baseline class."""

    def test_baseline_to_dict(self, tmp_path, sample_results):
        """Test converting baseline to dictionary."""
        metadata = BaselineMetadata(
            name="test", created_at="2024-01-01T12:00:00", kernel_version="6.12-rc1", fstype="ext4"
        )

        baseline = Baseline(metadata=metadata, results=sample_results, baseline_dir=tmp_path)

        data = baseline.to_dict()

        assert data["metadata"]["name"] == "test"
        assert data["metadata"]["kernel_version"] == "6.12-rc1"
        assert data["results"]["total_tests"] == 10
        assert data["results"]["passed"] == 8
        assert len(data["results"]["test_results"]) == 10

    def test_baseline_from_dict(self, tmp_path):
        """Test creating baseline from dictionary."""
        data = {
            "metadata": {
                "name": "test",
                "created_at": "2024-01-01T12:00:00",
                "kernel_version": "6.12-rc1",
                "fstype": "ext4",
            },
            "results": {
                "success": True,
                "total_tests": 2,
                "passed": 1,
                "failed": 1,
                "notrun": 0,
                "duration": 10.0,
                "test_results": [
                    {
                        "test_name": "generic/001",
                        "status": "passed",
                        "duration": 5.0,
                        "failure_reason": None,
                    },
                    {
                        "test_name": "generic/002",
                        "status": "failed",
                        "duration": 0.0,
                        "failure_reason": "error",
                    },
                ],
            },
        }

        baseline = Baseline.from_dict(data, tmp_path)

        assert baseline.metadata.name == "test"
        assert baseline.metadata.kernel_version == "6.12-rc1"
        assert baseline.results.total_tests == 2
        assert len(baseline.results.test_results) == 2


class TestComparisonResult:
    """Test ComparisonResult class."""

    def test_comparison_no_changes(self):
        """Test comparison with no changes."""
        comparison = ComparisonResult()

        assert comparison.regression_count == 0
        assert comparison.improvement_count == 0
        assert not comparison.regression_detected

    def test_comparison_with_regressions(self):
        """Test comparison with regressions."""
        comparison = ComparisonResult(
            new_failures=[
                TestResult("generic/001", "failed", 0.0, "error"),
                TestResult("generic/002", "failed", 0.0, "timeout"),
            ]
        )

        assert comparison.regression_count == 2
        assert comparison.regression_detected

    def test_comparison_with_improvements(self):
        """Test comparison with improvements."""
        comparison = ComparisonResult(
            new_passes=[
                TestResult("generic/003", "passed", 5.0),
            ]
        )

        assert comparison.improvement_count == 1
        assert not comparison.regression_detected

    def test_summary_no_changes(self):
        """Test summary with no changes."""
        comparison = ComparisonResult()
        summary = comparison.summary()

        assert "No regressions detected" in summary

    def test_summary_with_regressions(self):
        """Test summary with regressions."""
        comparison = ComparisonResult(
            new_failures=[
                TestResult("generic/001", "failed", 0.0, "error"),
            ]
        )

        summary = comparison.summary()

        assert "REGRESSION DETECTED" in summary
        assert "1 new failure" in summary

    def test_summary_with_improvements(self):
        """Test summary with improvements."""
        comparison = ComparisonResult(
            new_passes=[
                TestResult("generic/001", "passed", 5.0),
            ]
        )

        summary = comparison.summary()

        assert "1 test(s) now passing" in summary


class TestBaselineManager:
    """Test BaselineManager class."""

    def test_init_creates_storage_dir(self, tmp_path):
        """Test that storage directory is created."""
        storage_dir = tmp_path / "baselines"
        manager = BaselineManager(storage_dir=storage_dir)

        assert manager.storage_dir == storage_dir
        assert storage_dir.exists()

    def test_init_default_storage_dir(self):
        """Test default storage directory."""
        manager = BaselineManager()
        expected = Path.home() / ".kerneldev-mcp" / "fstests-baselines"
        assert manager.storage_dir == expected

    def test_save_baseline(self, baseline_manager, sample_results):
        """Test saving a baseline."""
        baseline = baseline_manager.save_baseline(
            baseline_name="test-baseline",
            results=sample_results,
            kernel_version="6.12-rc1",
            config_hash="abc123",
            fstype="ext4",
            description="Test baseline",
            test_selection="-g quick",
        )

        assert baseline.metadata.name == "test-baseline"
        assert baseline.metadata.kernel_version == "6.12-rc1"
        assert baseline.metadata.config_hash == "abc123"
        assert baseline.metadata.fstype == "ext4"
        assert baseline.metadata.description == "Test baseline"
        assert baseline.metadata.test_selection == "-g quick"

        # Check that files were created
        baseline_dir = baseline_manager.storage_dir / "test-baseline"
        assert baseline_dir.exists()
        assert (baseline_dir / "baseline.json").exists()

    def test_save_baseline_sanitizes_name(self, baseline_manager, sample_results):
        """Test that baseline name is sanitized for filesystem."""
        baseline_manager.save_baseline(
            baseline_name="test/baseline:with*special", results=sample_results
        )

        # Name should be sanitized
        baseline_dir = baseline_manager.storage_dir / "test_baseline_with_special"
        assert baseline_dir.exists()

    def test_load_baseline_success(self, baseline_manager, sample_baseline):
        """Test loading a baseline."""
        loaded = baseline_manager.load_baseline("test-baseline")

        assert loaded is not None
        assert loaded.metadata.name == "test-baseline"
        assert loaded.results.total_tests == 10

    def test_load_baseline_not_found(self, baseline_manager):
        """Test loading non-existent baseline."""
        loaded = baseline_manager.load_baseline("nonexistent")
        assert loaded is None

    def test_load_baseline_corrupted_json(self, baseline_manager, tmp_path):
        """Test loading baseline with corrupted JSON."""
        # Create corrupted baseline
        baseline_dir = baseline_manager.storage_dir / "corrupted"
        baseline_dir.mkdir(parents=True)
        json_file = baseline_dir / "baseline.json"
        json_file.write_text("{ invalid json }")

        loaded = baseline_manager.load_baseline("corrupted")
        assert loaded is None

    def test_list_baselines_empty(self, baseline_manager):
        """Test listing baselines when none exist."""
        baselines = baseline_manager.list_baselines()
        assert baselines == []

    def test_list_baselines_multiple(self, baseline_manager, sample_results):
        """Test listing multiple baselines."""
        # Create multiple baselines
        baseline_manager.save_baseline("baseline1", sample_results)
        baseline_manager.save_baseline("baseline2", sample_results)
        baseline_manager.save_baseline("baseline3", sample_results)

        baselines = baseline_manager.list_baselines()

        assert len(baselines) == 3
        names = [b.name for b in baselines]
        assert "baseline1" in names
        assert "baseline2" in names
        assert "baseline3" in names

    def test_list_baselines_sorted_by_date(self, baseline_manager, sample_results):
        """Test that baselines are sorted by creation time."""
        import time

        # Create baselines with slight delay
        baseline_manager.save_baseline("first", sample_results)
        time.sleep(0.01)
        baseline_manager.save_baseline("second", sample_results)
        time.sleep(0.01)
        baseline_manager.save_baseline("third", sample_results)

        baselines = baseline_manager.list_baselines()

        # Should be sorted newest first
        assert baselines[0].name == "third"
        assert baselines[1].name == "second"
        assert baselines[2].name == "first"

    def test_delete_baseline_success(self, baseline_manager, sample_baseline):
        """Test deleting a baseline."""
        success = baseline_manager.delete_baseline("test-baseline")

        assert success
        assert not (baseline_manager.storage_dir / "test-baseline").exists()

    def test_delete_baseline_not_found(self, baseline_manager):
        """Test deleting non-existent baseline."""
        success = baseline_manager.delete_baseline("nonexistent")
        assert not success

    def test_compare_results_no_changes(self, baseline_manager, sample_results):
        """Test comparing identical results."""
        baseline = baseline_manager.save_baseline("baseline", sample_results)

        comparison = baseline_manager.compare_results(sample_results, baseline)

        assert len(comparison.new_failures) == 0
        assert len(comparison.new_passes) == 0
        assert not comparison.regression_detected

    def test_compare_results_new_failure(self, baseline_manager, sample_results):
        """Test comparing with new failure."""
        baseline = baseline_manager.save_baseline("baseline", sample_results)

        # Create new results with additional failure
        new_results = FstestsRunResult(
            success=False,
            total_tests=10,
            passed=7,
            failed=3,
            notrun=0,
            test_results=[
                TestResult("generic/001", "failed", 0.0, "new error"),  # Was passing
                TestResult("generic/002", "passed", 3.0),
                TestResult("generic/003", "failed", 0.0, "output mismatch"),
                TestResult("generic/004", "passed", 7.0),
                TestResult("generic/005", "failed", 0.0, "timeout"),
                TestResult("generic/006", "passed", 4.0),
                TestResult("generic/007", "passed", 6.0),
                TestResult("generic/008", "passed", 8.0),
                TestResult("generic/009", "passed", 2.0),
                TestResult("generic/010", "passed", 9.0),
            ],
            duration=50.0,
        )

        comparison = baseline_manager.compare_results(new_results, baseline)

        assert len(comparison.new_failures) == 1
        assert comparison.new_failures[0].test_name == "generic/001"
        assert comparison.regression_detected

    def test_compare_results_new_pass(self, baseline_manager, sample_results):
        """Test comparing with new pass."""
        baseline = baseline_manager.save_baseline("baseline", sample_results)

        # Create new results with failure fixed
        new_results = FstestsRunResult(
            success=True,
            total_tests=10,
            passed=9,
            failed=1,
            notrun=0,
            test_results=[
                TestResult("generic/001", "passed", 5.0),
                TestResult("generic/002", "passed", 3.0),
                TestResult("generic/003", "passed", 5.0),  # Was failing
                TestResult("generic/004", "passed", 7.0),
                TestResult("generic/005", "failed", 0.0, "timeout"),
                TestResult("generic/006", "passed", 4.0),
                TestResult("generic/007", "passed", 6.0),
                TestResult("generic/008", "passed", 8.0),
                TestResult("generic/009", "passed", 2.0),
                TestResult("generic/010", "passed", 9.0),
            ],
            duration=50.0,
        )

        comparison = baseline_manager.compare_results(new_results, baseline)

        assert len(comparison.new_passes) == 1
        assert comparison.new_passes[0].test_name == "generic/003"
        assert not comparison.regression_detected

    def test_compare_results_still_failing(self, baseline_manager, sample_results):
        """Test comparing with tests still failing."""
        baseline = baseline_manager.save_baseline("baseline", sample_results)

        comparison = baseline_manager.compare_results(sample_results, baseline)

        # Tests that were failing and still failing
        assert len(comparison.still_failing) == 2
        failing_names = [t.test_name for t in comparison.still_failing]
        assert "generic/003" in failing_names
        assert "generic/005" in failing_names

    def test_compare_results_new_test(self, baseline_manager, sample_results):
        """Test comparing with new test."""
        baseline = baseline_manager.save_baseline("baseline", sample_results)

        # Add new test that fails
        new_results = FstestsRunResult(
            success=False,
            total_tests=11,
            passed=8,
            failed=3,
            notrun=0,
            test_results=sample_results.test_results
            + [TestResult("generic/011", "failed", 0.0, "new test failure")],
            duration=50.0,
        )

        comparison = baseline_manager.compare_results(new_results, baseline)

        # New test failure should be detected
        assert len(comparison.new_failures) == 1
        assert comparison.new_failures[0].test_name == "generic/011"

    def test_generate_exclude_list(self, baseline_manager, sample_baseline, tmp_path):
        """Test generating exclude list from baseline."""
        output_file = tmp_path / "exclude.txt"

        count = baseline_manager.generate_exclude_list(sample_baseline, output_file)

        assert count == 2  # Two failures in sample_results
        assert output_file.exists()

        content = output_file.read_text()
        assert "generic/003" in content
        assert "generic/005" in content
        assert "# Exclude list generated from baseline" in content

    def test_generate_exclude_list_io_error(self, baseline_manager, sample_baseline):
        """Test exclude list generation with I/O error."""
        # Use invalid path
        output_file = Path("/invalid/path/exclude.txt")

        count = baseline_manager.generate_exclude_list(sample_baseline, output_file)

        assert count == 0


class TestFormatComparisonResult:
    """Test format_comparison_result function."""

    def test_format_no_changes(self):
        """Test formatting with no changes."""
        comparison = ComparisonResult()
        formatted = format_comparison_result(comparison, "baseline")

        assert "baseline" in formatted
        assert "No regressions detected" in formatted

    def test_format_with_regressions(self):
        """Test formatting with regressions."""
        comparison = ComparisonResult(
            new_failures=[
                TestResult("generic/001", "failed", 0.0, "error 1"),
                TestResult("generic/002", "failed", 0.0, "error 2"),
            ]
        )

        formatted = format_comparison_result(comparison, "baseline", max_shown=10)

        assert "NEW FAILURES - REGRESSIONS (2)" in formatted
        assert "generic/001" in formatted
        assert "generic/002" in formatted
        assert "ACTION REQUIRED" in formatted

    def test_format_with_improvements(self):
        """Test formatting with improvements."""
        comparison = ComparisonResult(
            new_passes=[
                TestResult("generic/001", "passed", 5.0),
                TestResult("generic/002", "passed", 3.0),
            ]
        )

        formatted = format_comparison_result(comparison, "baseline", max_shown=10)

        assert "NEW PASSES - IMPROVEMENTS (2)" in formatted
        assert "generic/001" in formatted
        assert "SAFE TO PROCEED" in formatted

    def test_format_limits_shown(self):
        """Test that formatting limits items shown."""
        new_failures = [
            TestResult(f"generic/{i:03d}", "failed", 0.0, f"error {i}")
            for i in range(1, 26)  # 25 failures
        ]

        comparison = ComparisonResult(new_failures=new_failures)

        formatted = format_comparison_result(comparison, "baseline", max_shown=5)

        assert "and 20 more" in formatted

    def test_format_with_pre_existing(self):
        """Test formatting with pre-existing failures."""
        comparison = ComparisonResult(
            still_failing=[
                TestResult("generic/003", "failed", 0.0, "old error"),
                TestResult("generic/004", "failed", 0.0, "old error 2"),
            ]
        )

        formatted = format_comparison_result(comparison, "baseline", max_shown=10)

        assert "STILL FAILING - PRE-EXISTING (2)" in formatted
        assert "SAFE TO PROCEED" in formatted  # No new failures
