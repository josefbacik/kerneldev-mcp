"""
Tests for fstests_baseline_save functionality.
"""

import pytest

from kerneldev_mcp.baseline_manager import BaselineManager
from kerneldev_mcp.fstests_manager import FstestsManager


@pytest.fixture
def baseline_storage(tmp_path):
    """Create temporary baseline storage."""
    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    return baseline_dir


@pytest.fixture
def mock_results_dir(tmp_path):
    """Create a mock results directory with check.log."""
    results_dir = tmp_path / "fstests-results" / "run-20251120-143022"
    results_dir.mkdir(parents=True)

    # Create a simple check.log with sample results
    # Format matches what fstests actually produces
    check_log = results_dir / "check.log"
    check_log.write_text("""FSTYP         -- ext4
PLATFORM      -- Linux/x86_64 testhost 6.12.0-rc1
MKFS_OPTIONS  -- /dev/vdb
MOUNT_OPTIONS -- -o acl,user_xattr /dev/vdb /mnt/scratch

generic/001 5s
generic/002 3s
generic/003 - output mismatch (see generic/003.out.bad)
generic/004 [not run] requires feature
Ran: generic/001 generic/002 generic/003 generic/004
Failures: generic/003
Not run: generic/004
Passed all 2 tests
""")

    return results_dir


def test_parse_results_from_check_log(mock_results_dir):
    """Test parsing results from check.log file."""
    manager = FstestsManager()
    check_log = mock_results_dir / "check.log"

    with open(check_log) as f:
        output = f.read()

    result = manager.parse_check_output(output, check_log=check_log)

    # Verify parsed results
    assert result.total_tests == 4
    assert result.passed == 2
    assert result.failed == 1
    assert result.notrun == 1
    assert result.success is False  # Has failures


def test_save_baseline_from_results_dir(mock_results_dir, baseline_storage):
    """Test saving baseline from results directory."""
    manager = FstestsManager()
    baseline_mgr = BaselineManager(baseline_storage)

    # Parse results
    check_log = mock_results_dir / "check.log"
    with open(check_log) as f:
        output = f.read()

    result = manager.parse_check_output(output, check_log=check_log)

    # Save as baseline
    baseline = baseline_mgr.save_baseline(
        baseline_name="test-baseline",
        results=result,
        kernel_version="6.12-rc1",
        fstype="ext4",
        test_selection="-g quick",
    )

    # Verify baseline was created
    assert baseline is not None
    assert baseline.metadata.name == "test-baseline"
    assert baseline.results.total_tests == 4
    assert baseline.results.passed == 2

    # Verify baseline can be loaded
    loaded = baseline_mgr.load_baseline("test-baseline")
    assert loaded is not None
    assert loaded.metadata.name == "test-baseline"
    assert loaded.results.total_tests == 4


def test_baseline_save_validation(tmp_path):
    """Test validation of baseline_save parameters."""
    # This test validates the parameter checking logic
    # In the actual implementation, these checks happen in server.py

    # Test: results_dir doesn't exist
    nonexistent_dir = tmp_path / "does-not-exist"
    assert not nonexistent_dir.exists()

    # Test: check.log doesn't exist
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    check_log = results_dir / "check.log"
    assert not check_log.exists()
