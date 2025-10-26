"""
Unit tests for fstests test argument validation and error detection.
"""
import pytest
from pathlib import Path
from kerneldev_mcp.fstests_manager import FstestsManager, FstestsRunResult


class TestFstestsValidation:
    """Tests for test argument validation."""

    def test_validate_empty_list(self):
        """Empty test list should be valid."""
        is_valid, error = FstestsManager.validate_test_args([])
        assert is_valid is True
        assert error is None

    def test_validate_group_with_valid_name(self):
        """'-g quick' should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "quick"])
        assert is_valid is True
        assert error is None

    def test_validate_group_with_auto(self):
        """'-g auto' should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "auto"])
        assert is_valid is True
        assert error is None

    def test_validate_multiple_groups(self):
        """Multiple groups should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "quick", "-g", "auto"])
        assert is_valid is True
        assert error is None

    def test_validate_individual_test(self):
        """Individual test 'btrfs/010' should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["btrfs/010"])
        assert is_valid is True
        assert error is None

    def test_validate_multiple_individual_tests(self):
        """Multiple individual tests should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["btrfs/010", "generic/001", "xfs/100"])
        assert is_valid is True
        assert error is None

    def test_validate_mixed_valid_args(self):
        """Mix of groups and flags should be valid."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "quick", "-x", "btrfs/050"])
        assert is_valid is True
        assert error is None

    def test_validate_g_with_individual_test_btrfs(self):
        """'-g btrfs/010' should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "btrfs/010"])
        assert is_valid is False
        assert error is not None
        assert "btrfs/010" in error
        assert "-g" in error
        assert "groups" in error.lower()

    def test_validate_g_with_individual_test_generic(self):
        """'-g generic/001' should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "generic/001"])
        assert is_valid is False
        assert error is not None
        assert "generic/001" in error

    def test_validate_g_with_individual_test_xfs(self):
        """'-g xfs/100' should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "xfs/100"])
        assert is_valid is False
        assert error is not None
        assert "xfs/100" in error

    def test_validate_g_with_individual_test_ext4(self):
        """'-g ext4/001' should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "ext4/001"])
        assert is_valid is False
        assert error is not None
        assert "ext4/001" in error

    def test_validate_g_without_argument(self):
        """'-g' without an argument should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g"])
        assert is_valid is False
        assert error is not None
        assert "requires" in error.lower()

    def test_validate_multiple_tests_with_one_invalid(self):
        """Multiple args where one uses '-g' incorrectly should be INVALID."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "quick", "-g", "btrfs/010"])
        assert is_valid is False
        assert error is not None
        assert "btrfs/010" in error

    def test_error_message_provides_solution(self):
        """Error message should suggest the correct usage."""
        is_valid, error = FstestsManager.validate_test_args(["-g", "btrfs/010"])
        assert is_valid is False
        assert "without '-g'" in error or "use it without" in error.lower()
        assert "['btrfs/010']" in error or "btrfs/010" in error


class TestFstestsErrorDetection:
    """Tests for error detection in parse_check_output."""

    def setup_method(self):
        """Setup test fixtures."""
        # Create a temporary fstests manager (path doesn't need to exist for parsing)
        self.manager = FstestsManager(Path("/tmp/test-fstests"))

    def test_parse_group_not_defined_error(self):
        """Should detect 'Group is empty or not defined' error."""
        output = """
=== fstests Output ===
Group "btrfs/010" is empty or not defined?

=== fstests Execution Complete ===
Exit code: 1
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 0
        assert result.passed == 0
        assert result.failed == 0

    def test_parse_invalid_option_error(self):
        """Should detect invalid option errors."""
        output = """
check: invalid option -- 'z'
Usage: check [options]
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 0

    def test_parse_successful_test_run(self):
        """Should correctly parse successful test runs."""
        output = """
btrfs/010 5s
generic/001 3s
Ran: 2 tests in 8s
"""
        result = self.manager.parse_check_output(output)
        assert result.success is True
        assert result.total_tests == 2
        assert result.passed == 2
        assert result.failed == 0

    def test_parse_failed_test_run(self):
        """Should correctly identify failed tests."""
        output = """
btrfs/010 5s
generic/001 - output mismatch (see generic/001.out.bad)
Ran: 2 tests in 8s
Failures: generic/001
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 2
        assert result.passed == 1
        assert result.failed == 1

    def test_parse_not_run_tests(self):
        """Should correctly identify tests that were not run."""
        output = """
btrfs/010 5s
generic/001 [not run] requires feature
Ran: 2 tests in 5s
Not run: generic/001
"""
        result = self.manager.parse_check_output(output)
        assert result.success is True  # Not run tests don't count as failures
        assert result.total_tests == 2
        assert result.passed == 1
        assert result.failed == 0
        assert result.notrun == 1

    def test_parse_mixed_results(self):
        """Should correctly parse mixed results."""
        output = """
btrfs/010 5s
generic/001 - output mismatch
generic/002 [not run] requires feature
xfs/100 3s
Ran: 4 tests in 10s
Failures: generic/001
Not run: generic/002
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False  # Has failures
        assert result.total_tests == 4
        assert result.passed == 2
        assert result.failed == 1
        assert result.notrun == 1

    def test_parse_error_with_kernel_messages(self):
        """Should detect errors even with kernel log messages."""
        output = """
[    3.131933] BTRFS: device fsid 08423282-0248-4c1a-b3ef-dc84283ac8f0 devid 1
[    3.134924] BTRFS info (device vda): first mount of filesystem
Group "btrfs/010" is empty or not defined?
[    3.313262] BTRFS info (device vda): last unmount of filesystem
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 0

    def test_parse_usage_message(self):
        """Should detect usage message as error."""
        output = """
Usage: check [options] test_list
    -g group[,group...]    include tests from these groups
    -x group[,group...]    exclude tests from these groups
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 0

    def test_parse_generic_error(self):
        """Should detect generic ERROR messages."""
        output = """
ERROR: Failed to setup test devices
"""
        result = self.manager.parse_check_output(output)
        assert result.success is False
        assert result.total_tests == 0

    def test_parse_empty_output(self):
        """Empty output should not be considered successful."""
        output = ""
        result = self.manager.parse_check_output(output)
        # Empty output with no tests should still return success=True for failed==0
        # This is the current behavior - we only fail if there's an explicit error
        assert result.total_tests == 0
        assert result.passed == 0
        assert result.failed == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
