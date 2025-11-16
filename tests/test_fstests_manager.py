"""
Unit tests for fstests_manager module.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import subprocess

from kerneldev_mcp.fstests_manager import (
    FstestsManager,
    FstestsConfig,
    TestResult,
    FstestsRunResult,
    format_fstests_result,
)


@pytest.fixture
def fstests_manager(tmp_path):
    """Create FstestsManager with temporary path."""
    return FstestsManager(fstests_path=tmp_path / "fstests")


@pytest.fixture
def sample_check_output():
    """Sample output from fstests ./check command."""
    return """generic/001 5s
generic/002  [not run] requires feature XYZ
generic/003 - output mismatch (see generic/003.out.bad)
generic/004 10s
Ran: 4 tests in 15s
"""


@pytest.fixture
def fstests_config():
    """Sample FstestsConfig."""
    return FstestsConfig(
        fstests_path=Path("/tmp/fstests"),
        test_dev="/dev/loop0",
        test_dir=Path("/mnt/test"),
        scratch_dev="/dev/loop1",
        scratch_dir=Path("/mnt/scratch"),
        fstype="ext4",
    )


class TestFstestsConfig:
    """Test FstestsConfig class."""

    def test_config_creation(self):
        """Test creating FstestsConfig."""
        config = FstestsConfig(
            fstests_path=Path("/tmp/fstests"),
            test_dev="/dev/loop0",
            test_dir=Path("/mnt/test"),
            scratch_dev="/dev/loop1",
            scratch_dir=Path("/mnt/scratch"),
            fstype="ext4",
        )

        assert config.test_dev == "/dev/loop0"
        assert config.fstype == "ext4"

    def test_to_config_text_basic(self, fstests_config):
        """Test generating config text."""
        text = fstests_config.to_config_text()

        assert "export TEST_DEV=/dev/loop0" in text
        assert "export TEST_DIR=/mnt/test" in text
        assert "export SCRATCH_DEV=/dev/loop1" in text
        assert "export SCRATCH_MNT=/mnt/scratch" in text
        assert "export FSTYP=ext4" in text

    def test_to_config_text_with_options(self):
        """Test config text with mount/mkfs options."""
        config = FstestsConfig(
            fstests_path=Path("/tmp/fstests"),
            test_dev="/dev/loop0",
            test_dir=Path("/mnt/test"),
            scratch_dev="/dev/loop1",
            scratch_dir=Path("/mnt/scratch"),
            fstype="btrfs",
            mount_options="compress=zstd",
            mkfs_options="-L TEST",
        )

        text = config.to_config_text()

        assert 'export MOUNT_OPTIONS="compress=zstd"' in text
        assert 'export MKFS_OPTIONS="-L TEST"' in text

    def test_to_config_text_with_additional_vars(self):
        """Test config text with additional variables."""
        config = FstestsConfig(
            fstests_path=Path("/tmp/fstests"),
            test_dev="/dev/loop0",
            test_dir=Path("/mnt/test"),
            scratch_dev="/dev/loop1",
            scratch_dir=Path("/mnt/scratch"),
            fstype="ext4",
            additional_vars={"CUSTOM_VAR": "value"},
        )

        text = config.to_config_text()

        assert 'export CUSTOM_VAR="value"' in text


class TestTestResult:
    """Test TestResult dataclass."""

    def test_passed_result(self):
        """Test passed test result."""
        result = TestResult(test_name="generic/001", status="passed", duration=5.0)

        assert result.test_name == "generic/001"
        assert result.status == "passed"
        assert result.duration == 5.0

    def test_failed_result(self):
        """Test failed test result."""
        result = TestResult(
            test_name="generic/003", status="failed", duration=0.0, failure_reason="output mismatch"
        )

        assert result.status == "failed"
        assert result.failure_reason == "output mismatch"

    def test_notrun_result(self):
        """Test notrun test result."""
        result = TestResult(
            test_name="generic/002",
            status="notrun",
            duration=0.0,
            failure_reason="requires feature XYZ",
        )

        assert result.status == "notrun"


class TestFstestsRunResult:
    """Test FstestsRunResult dataclass."""

    def test_run_result_creation(self):
        """Test creating FstestsRunResult."""
        result = FstestsRunResult(
            success=True, total_tests=10, passed=8, failed=1, notrun=1, duration=100.0
        )

        assert result.success
        assert result.total_tests == 10
        assert result.passed == 8
        assert result.failed == 1
        assert result.notrun == 1

    def test_pass_rate(self):
        """Test pass rate calculation."""
        result = FstestsRunResult(success=True, total_tests=10, passed=8, failed=2, notrun=0)

        assert result.pass_rate == 80.0

    def test_pass_rate_zero_tests(self):
        """Test pass rate with zero tests."""
        result = FstestsRunResult(success=True, total_tests=0, passed=0, failed=0, notrun=0)

        assert result.pass_rate == 0.0

    def test_summary_all_passed(self):
        """Test summary with all tests passed."""
        result = FstestsRunResult(
            success=True, total_tests=10, passed=10, failed=0, notrun=0, duration=50.0
        )

        summary = result.summary()

        assert "✓" in summary
        assert "10/10 passed" in summary
        assert "100.0% pass rate" in summary

    def test_summary_with_failures(self):
        """Test summary with failures."""
        result = FstestsRunResult(
            success=False, total_tests=10, passed=7, failed=2, notrun=1, duration=50.0
        )

        summary = result.summary()

        assert "✗" in summary
        assert "2 failed" in summary
        assert "1 not run" in summary


class TestFstestsManager:
    """Test FstestsManager class."""

    def test_init_default_path(self):
        """Test default fstests path."""
        manager = FstestsManager()
        expected = Path.home() / ".kerneldev-mcp" / "fstests"
        assert manager.fstests_path == expected

    def test_init_custom_path(self, tmp_path):
        """Test custom fstests path."""
        custom_path = tmp_path / "custom"
        manager = FstestsManager(fstests_path=custom_path)
        assert manager.fstests_path == custom_path

    def test_check_installed_not_exists(self, fstests_manager):
        """Test check_installed when directory doesn't exist."""
        assert not fstests_manager.check_installed()

    def test_check_installed_no_check_script(self, fstests_manager):
        """Test check_installed when check script missing."""
        fstests_manager.fstests_path.mkdir(parents=True)
        assert not fstests_manager.check_installed()

    def test_check_installed_not_built(self, fstests_manager):
        """Test check_installed when not built."""
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        (fstests_manager.fstests_path / "src").mkdir()

        assert not fstests_manager.check_installed()

    def test_check_installed_success(self, fstests_manager):
        """Test check_installed when properly installed."""
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        assert fstests_manager.check_installed()

    def test_get_version_not_installed(self, fstests_manager):
        """Test get_version when not installed."""
        assert fstests_manager.get_version() is None

    def test_get_version_success(self, fstests_manager):
        """Test getting version."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0, stdout="v2024.01.01-123-gabcdef\n", stderr=""
            )

            version = fstests_manager.get_version()

            assert version == "v2024.01.01-123-gabcdef"

    def test_check_build_dependencies_success(self, fstests_manager):
        """Test checking build dependencies when all present."""
        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # All tools and headers are available
            mock_run.return_value = Mock(returncode=0)

            deps_ok, missing = fstests_manager.check_build_dependencies()

            assert deps_ok
            assert missing == []

    def test_check_build_dependencies_missing_tools(self, fstests_manager):
        """Test checking build dependencies when tools are missing."""
        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # First call (make --version) fails, rest succeed
            mock_run.side_effect = [
                FileNotFoundError(),  # make missing
                Mock(returncode=0),  # gcc present
                Mock(returncode=0),  # git present
                Mock(returncode=0),  # xfs/xfs.h present
                Mock(returncode=0),  # sys/acl.h present
                Mock(returncode=0),  # attr/xattr.h present
            ]

            deps_ok, missing = fstests_manager.check_build_dependencies()

            assert not deps_ok
            assert "make" in missing

    def test_check_build_dependencies_missing_headers(self, fstests_manager):
        """Test checking build dependencies when headers are missing."""
        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # Tools present, but one header missing
            def run_side_effect(cmd, *args, **kwargs):
                if (
                    cmd == ["make", "--version"]
                    or cmd == ["gcc", "--version"]
                    or cmd == ["git", "--version"]
                ):
                    return Mock(returncode=0)
                # gcc compilation test for headers
                if cmd[0] == "gcc" and "-x" in cmd:
                    # Check which header is being tested based on input
                    input_data = kwargs.get("input", "")
                    if "sys/acl.h" in input_data:
                        return Mock(returncode=1)  # acl.h missing
                    return Mock(returncode=0)  # others present
                return Mock(returncode=0)

            mock_run.side_effect = run_side_effect

            deps_ok, missing = fstests_manager.check_build_dependencies()

            assert not deps_ok
            assert any("sys/acl.h" in item for item in missing)

    def test_install_success(self, fstests_manager):
        """Test successful installation."""
        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # Mock successful git clone and make
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            # Mock check_installed to return True after build
            with patch.object(fstests_manager, "check_installed", return_value=True):
                with patch.object(
                    fstests_manager, "check_build_dependencies", return_value=(True, [])
                ):
                    # Mock build() to return success
                    with patch.object(
                        fstests_manager, "build", return_value=(True, "Build successful")
                    ):
                        success, message = fstests_manager.install()

                        assert success
                        assert "Successfully installed" in message

    def test_install_missing_dependencies(self, fstests_manager):
        """Test installation with missing dependencies."""
        with patch.object(
            fstests_manager, "check_build_dependencies", return_value=(False, ["gcc", "make"])
        ):
            success, message = fstests_manager.install()

            assert not success
            assert "Missing required build tools" in message
            assert "gcc" in message

    def test_install_clone_failure(self, fstests_manager):
        """Test installation when git clone fails."""
        with patch.object(fstests_manager, "check_build_dependencies", return_value=(True, [])):
            with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1, stderr="clone failed")

                success, message = fstests_manager.install()

                assert not success
                assert "Git clone failed" in message

    def test_install_timeout(self, fstests_manager):
        """Test installation timeout."""
        with patch.object(fstests_manager, "check_build_dependencies", return_value=(True, [])):
            with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired("git", 300)

                success, message = fstests_manager.install()

                assert not success
                assert "timed out" in message

    def test_build_success(self, fstests_manager):
        """Test successful build."""
        fstests_manager.fstests_path.mkdir(parents=True)

        # Create directories for critical binaries
        (fstests_manager.fstests_path / "ltp").mkdir(parents=True)
        (fstests_manager.fstests_path / "src").mkdir(parents=True)

        # Create the critical binaries that build() checks for
        fsstress = fstests_manager.fstests_path / "ltp" / "fsstress"
        aio_dio = fstests_manager.fstests_path / "src" / "aio-dio-regress"
        fsstress.touch(mode=0o755)
        aio_dio.touch(mode=0o755)

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            success, message = fstests_manager.build()

            assert success
            assert "Build successful" in message

    def test_build_with_configure(self, fstests_manager):
        """Test build when configure script exists."""
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "configure").touch()

        # Create include directory and builddefs file
        include_dir = fstests_manager.fstests_path / "include"
        include_dir.mkdir(parents=True)
        builddefs = include_dir / "builddefs"
        builddefs.touch()

        # Create directories and binaries
        (fstests_manager.fstests_path / "ltp").mkdir(parents=True)
        (fstests_manager.fstests_path / "src").mkdir(parents=True)
        fsstress = fstests_manager.fstests_path / "ltp" / "fsstress"
        aio_dio = fstests_manager.fstests_path / "src" / "aio-dio-regress"
        fsstress.touch(mode=0o755)
        aio_dio.touch(mode=0o755)

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            success, message = fstests_manager.build()

            assert success
            # Should have called configure and make
            assert mock_run.call_count >= 2

    def test_build_not_exists(self, fstests_manager):
        """Test build when directory doesn't exist."""
        success, message = fstests_manager.build()

        assert not success
        assert "does not exist" in message

    def test_build_failure(self, fstests_manager):
        """Test build failure."""
        fstests_manager.fstests_path.mkdir(parents=True)

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="build error")

            success, message = fstests_manager.build()

            assert not success
            assert "Build failed" in message
            assert "build error" in message

    def test_build_configure_failure(self, fstests_manager):
        """Test build when configure fails."""
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "configure").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # Configure returns error
            mock_run.return_value = Mock(
                returncode=1, stdout="FATAL ERROR: cannot find xfs/xfs.h", stderr=""
            )

            success, message = fstests_manager.build()

            assert not success
            assert "Configure failed" in message
            assert "development packages" in message

    def test_build_configure_no_builddefs(self, fstests_manager):
        """Test build when configure doesn't create builddefs."""
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "configure").touch()
        (fstests_manager.fstests_path / "include").mkdir(parents=True)

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            # Configure returns success but doesn't create builddefs
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            success, message = fstests_manager.build()

            assert not success
            assert "builddefs" in message

    def test_build_missing_binaries(self, fstests_manager):
        """Test build when critical binaries are not created."""
        fstests_manager.fstests_path.mkdir(parents=True)

        # Create directories but not the binaries
        (fstests_manager.fstests_path / "ltp").mkdir(parents=True)
        (fstests_manager.fstests_path / "src").mkdir(parents=True)

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            success, message = fstests_manager.build()

            assert not success
            assert "critical binaries" in message
            assert "fsstress" in message

    def test_write_config_success(self, fstests_manager, fstests_config):
        """Test writing config file."""
        fstests_manager.fstests_path.mkdir(parents=True)

        success = fstests_manager.write_config(fstests_config)

        assert success
        config_file = fstests_manager.fstests_path / "local.config"
        assert config_file.exists()
        content = config_file.read_text()
        assert "TEST_DEV=/dev/loop0" in content

    def test_write_config_not_installed(self, fstests_manager, fstests_config):
        """Test writing config when not installed."""
        success = fstests_manager.write_config(fstests_config)
        assert not success

    def test_parse_check_output_basic(self, fstests_manager, sample_check_output):
        """Test parsing check output."""
        result = fstests_manager.parse_check_output(sample_check_output)

        assert result.total_tests == 4
        assert result.passed == 2
        assert result.failed == 1
        assert result.notrun == 1
        assert result.duration == 15.0

    def test_parse_check_output_test_details(self, fstests_manager, sample_check_output):
        """Test parsing individual test details."""
        result = fstests_manager.parse_check_output(sample_check_output)

        # Check passed test
        passed_tests = [t for t in result.test_results if t.status == "passed"]
        assert len(passed_tests) == 2
        assert passed_tests[0].test_name == "generic/001"
        assert passed_tests[0].duration == 5.0

        # Check failed test
        failed_tests = [t for t in result.test_results if t.status == "failed"]
        assert len(failed_tests) == 1
        assert failed_tests[0].test_name == "generic/003"
        assert "output mismatch" in failed_tests[0].failure_reason

        # Check notrun test
        notrun_tests = [t for t in result.test_results if t.status == "notrun"]
        assert len(notrun_tests) == 1
        assert notrun_tests[0].test_name == "generic/002"

    def test_parse_check_output_summary_notrun(self, fstests_manager):
        """Test parsing summary output with 'Not run' line."""
        # This tests the scenario from btrfs/282 where test is skipped
        output = """Kernel version: 6.16.0+
Wed Oct 22 03:06:22 EDT 2025
Ran: btrfs/282
Not run: btrfs/282
Passed all 1 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 1
        assert result.passed == 0
        assert result.failed == 0
        assert result.notrun == 1

        notrun_tests = [t for t in result.test_results if t.status == "notrun"]
        assert len(notrun_tests) == 1
        assert notrun_tests[0].test_name == "btrfs/282"
        assert notrun_tests[0].status == "notrun"

    def test_parse_check_output_summary_all_passed(self, fstests_manager):
        """Test parsing summary output with all tests passed."""
        output = """Kernel version: 6.16.0+
Wed Oct 22 02:55:45 EDT 2025
Ran: btrfs/003
Passed all 1 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 1
        assert result.passed == 1
        assert result.failed == 0
        assert result.notrun == 0

        passed_tests = [t for t in result.test_results if t.status == "passed"]
        assert len(passed_tests) == 1
        assert passed_tests[0].test_name == "btrfs/003"

    def test_parse_check_output_summary_mixed(self, fstests_manager):
        """Test parsing summary output with mixed statuses."""
        output = """Kernel version: 6.16.0+
Ran: generic/001 generic/002 generic/003
Not run: generic/002
Failures: generic/003
Failed 1 of 3 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 3
        assert result.passed == 1
        assert result.failed == 1
        assert result.notrun == 1

        # Check each status
        passed_tests = [t for t in result.test_results if t.status == "passed"]
        assert len(passed_tests) == 1
        assert passed_tests[0].test_name == "generic/001"

        notrun_tests = [t for t in result.test_results if t.status == "notrun"]
        assert len(notrun_tests) == 1
        assert notrun_tests[0].test_name == "generic/002"

        failed_tests = [t for t in result.test_results if t.status == "failed"]
        assert len(failed_tests) == 1
        assert failed_tests[0].test_name == "generic/003"

    def test_parse_check_output_kernel_messages_interleaved(self, fstests_manager):
        """Test parsing output with kernel messages interleaved."""
        # Simulates kernel dmesg messages splitting test output
        output = """FSTYP         -- btrfs
PLATFORM      -- Linux/x86_64 virtme-ng 6.16.0+
btrfs/003       [    2.383242] run fstests btrfs/003 at 2025-10-22 02:55:38
 7s
Ran: btrfs/003
Passed all 1 tests
"""
        result = fstests_manager.parse_check_output(output)

        # Should still parse correctly despite kernel messages
        assert result.total_tests == 1
        assert result.passed == 1
        assert result.failed == 0
        assert result.notrun == 0

    def test_parse_check_output_multiple_notrun(self, fstests_manager):
        """Test parsing output with multiple not run tests."""
        output = """Kernel version: 6.16.0+
Ran: btrfs/100 btrfs/200 btrfs/300
Not run: btrfs/100 btrfs/200
Passed all 1 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 3
        assert result.passed == 1
        assert result.failed == 0
        assert result.notrun == 2

        notrun_tests = [t for t in result.test_results if t.status == "notrun"]
        assert len(notrun_tests) == 2
        notrun_names = {t.test_name for t in notrun_tests}
        assert notrun_names == {"btrfs/100", "btrfs/200"}

        passed_tests = [t for t in result.test_results if t.status == "passed"]
        assert len(passed_tests) == 1
        assert passed_tests[0].test_name == "btrfs/300"

    def test_parse_check_log_multiple_runs(self, fstests_manager, tmp_path):
        """Test parsing check.log with multiple test runs (only last entry should be used)."""
        # Create a check.log file with multiple runs
        check_log = tmp_path / "check.log"
        check_log.write_text("""Kernel version: 6.16.0+
Wed Oct 22 02:46:24 EDT 2025
Ran: btrfs/001
Passed all 1 tests

Kernel version: 6.16.0+
Wed Oct 22 02:55:45 EDT 2025
Ran: btrfs/003
Passed all 1 tests

Kernel version: 6.16.0+
Wed Oct 22 03:12:36 EDT 2025
Ran: btrfs/282
Not run: btrfs/282
Passed all 1 tests
""")

        # Parse with check_log parameter
        result = fstests_manager.parse_check_output("", check_log=check_log)

        # Should only parse the LAST entry (btrfs/282 as notrun)
        assert result.total_tests == 1
        assert result.passed == 0
        assert result.failed == 0
        assert result.notrun == 1

        notrun_tests = [t for t in result.test_results if t.status == "notrun"]
        assert len(notrun_tests) == 1
        assert notrun_tests[0].test_name == "btrfs/282"

    def test_parse_check_output_summary_single_failure(self, fstests_manager):
        """Test parsing summary output with single failure (Failures: line)."""
        # This tests the scenario where a test fails
        output = """Kernel version: 6.16.0+
Wed Oct 22 03:22:21 EDT 2025
Ran: btrfs/282
Failures: btrfs/282
Failed 1 of 1 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 1
        assert result.passed == 0
        assert result.failed == 1
        assert result.notrun == 0
        assert result.success == False

        failed_tests = [t for t in result.test_results if t.status == "failed"]
        assert len(failed_tests) == 1
        assert failed_tests[0].test_name == "btrfs/282"
        assert failed_tests[0].status == "failed"

    def test_parse_check_output_summary_multiple_failures(self, fstests_manager):
        """Test parsing summary output with multiple failures."""
        output = """Kernel version: 6.16.0+
Ran: generic/001 generic/002 generic/003
Failures: generic/001 generic/003
Failed 2 of 3 tests
"""
        result = fstests_manager.parse_check_output(output)

        assert result.total_tests == 3
        assert result.passed == 1
        assert result.failed == 2
        assert result.notrun == 0
        assert result.success == False

        failed_tests = [t for t in result.test_results if t.status == "failed"]
        assert len(failed_tests) == 2
        failed_names = {t.test_name for t in failed_tests}
        assert failed_names == {"generic/001", "generic/003"}

        passed_tests = [t for t in result.test_results if t.status == "passed"]
        assert len(passed_tests) == 1
        assert passed_tests[0].test_name == "generic/002"

    def test_run_tests_not_installed(self, fstests_manager):
        """Test running tests when not installed."""
        result = fstests_manager.run_tests()

        assert not result.success
        assert result.total_tests == 0

    def test_run_tests_success(self, fstests_manager, sample_check_output):
        """Test successful test run."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=sample_check_output, stderr="")

            result = fstests_manager.run_tests(tests=["-g", "quick"])

            assert result.total_tests == 4
            assert result.passed == 2
            assert result.failed == 1

    def test_run_tests_timeout(self, fstests_manager):
        """Test test run timeout."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("check", 300, output=b"partial output")

            result = fstests_manager.run_tests(timeout=300)

            assert not result.success

    def test_run_tests_with_exclude_file(self, fstests_manager, tmp_path):
        """Test running tests with exclude file."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        exclude_file = tmp_path / "exclude.txt"
        exclude_file.write_text("generic/001\n")

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            fstests_manager.run_tests(exclude_file=exclude_file)

            # Check that -E flag was used
            call_args = mock_run.call_args[0][0]
            assert "-E" in call_args
            assert str(exclude_file) in call_args

    def test_run_tests_randomize(self, fstests_manager):
        """Test running tests with randomize flag."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            fstests_manager.run_tests(randomize=True)

            call_args = mock_run.call_args[0][0]
            assert "-r" in call_args

    def test_run_tests_iterations(self, fstests_manager):
        """Test running tests multiple iterations."""
        # Setup installed fstests
        fstests_manager.fstests_path.mkdir(parents=True)
        (fstests_manager.fstests_path / "check").touch()
        src_dir = fstests_manager.fstests_path / "src"
        src_dir.mkdir()
        (src_dir / "fsstress").touch()

        with patch("kerneldev_mcp.fstests_manager.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            fstests_manager.run_tests(iterations=5)

            call_args = mock_run.call_args[0][0]
            assert "-i" in call_args
            assert "5" in call_args

    def test_get_test_failure_details_not_found(self, fstests_manager):
        """Test getting failure details when file doesn't exist."""
        details = fstests_manager.get_test_failure_details("generic/001")
        assert details is None

    def test_get_test_failure_details_success(self, fstests_manager):
        """Test getting failure details."""
        # Setup results directory
        results_dir = fstests_manager.fstests_path / "results" / "ext4"
        results_dir.mkdir(parents=True)
        out_bad = results_dir / "001.out.bad"
        out_bad.write_text("actual output\n")

        details = fstests_manager.get_test_failure_details("generic/001")

        assert details is not None
        assert "actual output" in details

    def test_list_groups(self, fstests_manager):
        """Test listing test groups."""
        groups = fstests_manager.list_groups()

        assert "auto" in groups
        assert "quick" in groups
        assert "dangerous" in groups
        assert isinstance(groups["auto"], str)


class TestFormatFstestsResult:
    """Test format_fstests_result function."""

    def test_format_basic(self):
        """Test basic formatting."""
        result = FstestsRunResult(
            success=True, total_tests=10, passed=10, failed=0, notrun=0, duration=50.0
        )

        formatted = format_fstests_result(result)

        assert "✓" in formatted
        assert "10/10 passed" in formatted

    def test_format_with_failures(self):
        """Test formatting with failures."""
        result = FstestsRunResult(
            success=False,
            total_tests=10,
            passed=7,
            failed=2,
            notrun=1,
            test_results=[
                TestResult("generic/001", "failed", 0.0, "error 1"),
                TestResult("generic/002", "failed", 0.0, "error 2"),
            ],
            duration=50.0,
        )

        formatted = format_fstests_result(result, max_failures=10)

        assert "Failed Tests (2)" in formatted
        assert "generic/001" in formatted
        assert "generic/002" in formatted

    def test_format_limits_failures(self):
        """Test that formatting limits number of failures shown."""
        failures = [
            TestResult(f"generic/{i:03d}", "failed", 0.0, f"error {i}") for i in range(1, 21)
        ]

        result = FstestsRunResult(
            success=False,
            total_tests=20,
            passed=0,
            failed=20,
            notrun=0,
            test_results=failures,
            duration=100.0,
        )

        formatted = format_fstests_result(result, max_failures=5)

        assert "and 15 more failures" in formatted
