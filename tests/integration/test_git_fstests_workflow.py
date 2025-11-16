"""
Integration tests for git-based fstests workflow.

Tests the full workflow of:
1. Running fstests and saving to git notes
2. Loading results from git notes
3. Comparing against baselines
"""

import subprocess
import pytest

from kerneldev_mcp.git_manager import GitManager
from kerneldev_mcp.fstests_manager import FstestsRunResult, TestResult
from kerneldev_mcp.baseline_manager import BaselineManager


@pytest.fixture
def temp_kernel_repo(tmp_path):
    """Create a temporary git repository mimicking a kernel tree."""
    repo_path = tmp_path / "linux"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True
    )

    # Create minimal kernel structure
    (repo_path / "Makefile").write_text("""
VERSION = 6
PATCHLEVEL = 8
SUBLEVEL = 0
EXTRAVERSION =
NAME = Kernel Test
""")

    (repo_path / "README").write_text("Test kernel tree\n")

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial kernel tree"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def baseline_storage(tmp_path):
    """Create temporary baseline storage."""
    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    return baseline_dir


@pytest.fixture
def sample_baseline_results():
    """Create sample baseline results."""
    test_results = [
        TestResult(test_name="generic/001", status="passed", duration=5.2),
        TestResult(test_name="generic/002", status="passed", duration=3.1),
        TestResult(test_name="generic/003", status="passed", duration=8.5),
        TestResult(test_name="generic/004", status="passed", duration=2.3),
    ]

    return FstestsRunResult(
        success=True,
        total_tests=4,
        passed=4,
        failed=0,
        notrun=0,
        test_results=test_results,
        duration=19.1,
    )


@pytest.fixture
def sample_current_results():
    """Create sample current results (with one regression)."""
    test_results = [
        TestResult(test_name="generic/001", status="passed", duration=5.3),
        TestResult(test_name="generic/002", status="passed", duration=3.2),
        TestResult(
            test_name="generic/003", status="failed", duration=10.0, failure_reason="Timeout"
        ),
        TestResult(test_name="generic/004", status="passed", duration=2.4),
    ]

    return FstestsRunResult(
        success=False,
        total_tests=4,
        passed=3,
        failed=1,
        notrun=0,
        test_results=test_results,
        duration=20.9,
    )


class TestGitFstestsWorkflow:
    """Integration tests for git-based fstests workflow."""

    def test_save_and_load_workflow(self, temp_kernel_repo, sample_current_results):
        """Test basic save and load workflow."""
        git_mgr = GitManager(temp_kernel_repo)

        # Save results to branch
        success = git_mgr.save_fstests_results(
            results=sample_current_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        assert success is True

        # Load results back
        loaded_results = git_mgr.load_fstests_run_result()

        assert loaded_results is not None
        assert loaded_results.total_tests == sample_current_results.total_tests
        assert loaded_results.passed == sample_current_results.passed
        assert loaded_results.failed == sample_current_results.failed

    def test_multiple_branches_workflow(self, temp_kernel_repo, sample_current_results):
        """Test saving results to multiple branches."""
        git_mgr = GitManager(temp_kernel_repo)

        # Remember the original branch name
        original_branch = git_mgr.get_current_branch()

        # Save to main/master branch
        git_mgr.save_fstests_results(
            results=sample_current_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Create a feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feature-test"],
            cwd=temp_kernel_repo,
            check=True,
            capture_output=True,
        )

        # Make a new commit on feature branch so it has a different commit than main
        (temp_kernel_repo / "feature.txt").write_text("feature work\n")
        subprocess.run(
            ["git", "add", "feature.txt"], cwd=temp_kernel_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add feature"],
            cwd=temp_kernel_repo,
            check=True,
            capture_output=True,
        )

        # Modify results for feature branch
        feature_results = FstestsRunResult(
            success=True,
            total_tests=4,
            passed=4,
            failed=0,
            notrun=0,
            test_results=[
                TestResult(test_name="generic/001", status="passed", duration=5.0),
                TestResult(test_name="generic/002", status="passed", duration=3.0),
                TestResult(test_name="generic/003", status="passed", duration=8.0),
                TestResult(test_name="generic/004", status="passed", duration=2.0),
            ],
            duration=18.0,
        )

        # Save to feature branch
        git_mgr.save_fstests_results(
            results=feature_results,
            target="branch",
            kernel_version="6.8.0-feature",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Load from feature branch (current)
        feature_loaded = git_mgr.load_fstests_run_result()
        assert feature_loaded.failed == 0
        assert feature_loaded.passed == 4

        # Load from main branch explicitly using the saved branch name
        main_loaded = git_mgr.load_fstests_run_result(branch_name=original_branch)
        assert main_loaded.failed == 1
        assert main_loaded.passed == 3

    def test_comparison_workflow(
        self, temp_kernel_repo, baseline_storage, sample_baseline_results, sample_current_results
    ):
        """Test full workflow: baseline creation, git save, and comparison."""
        git_mgr = GitManager(temp_kernel_repo)
        baseline_mgr = BaselineManager(baseline_storage)

        # 1. Save baseline
        baseline = baseline_mgr.save_baseline(
            baseline_name="stable-6.7",
            results=sample_baseline_results,
            kernel_version="6.7.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        assert baseline is not None

        # 2. Save current results to git
        success = git_mgr.save_fstests_results(
            results=sample_current_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        assert success is True

        # 3. Load current results from git
        current_loaded = git_mgr.load_fstests_run_result()
        assert current_loaded is not None

        # 4. Compare against baseline
        comparison = baseline_mgr.compare_results(current_loaded, baseline)

        # Should detect the regression (generic/003 failed)
        assert comparison.regression_detected is True
        assert len(comparison.new_failures) == 1
        assert comparison.new_failures[0].test_name == "generic/003"
        assert len(comparison.new_passes) == 0
        assert len(comparison.still_passing) == 3

    def test_listing_results_workflow(self, temp_kernel_repo, sample_current_results):
        """Test listing results across multiple commits."""
        git_mgr = GitManager(temp_kernel_repo)

        # Save results on initial commit
        git_mgr.save_fstests_results(
            results=sample_current_results,
            target="commit",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Create a new commit
        (temp_kernel_repo / "drivers" / "test.c").mkdir(parents=True, exist_ok=True)
        (temp_kernel_repo / "drivers" / "test.c" / "driver.c").write_text("// test driver\n")
        subprocess.run(
            ["git", "add", "drivers"], cwd=temp_kernel_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add test driver"],
            cwd=temp_kernel_repo,
            check=True,
            capture_output=True,
        )

        # Save results on new commit
        improved_results = FstestsRunResult(
            success=True,
            total_tests=4,
            passed=4,
            failed=0,
            notrun=0,
            test_results=[
                TestResult(test_name="generic/001", status="passed", duration=5.0),
                TestResult(test_name="generic/002", status="passed", duration=3.0),
                TestResult(test_name="generic/003", status="passed", duration=8.0),
                TestResult(test_name="generic/004", status="passed", duration=2.0),
            ],
            duration=18.0,
        )

        git_mgr.save_fstests_results(
            results=improved_results,
            target="commit",
            kernel_version="6.8.1",
            fstype="ext4",
            test_selection="-g quick",
        )

        # List all results
        all_results = git_mgr.list_commits_with_results()

        assert len(all_results) == 2
        # Results should be available
        assert all(r.kernel_version in ("6.8.0", "6.8.1") for r in all_results)

    def test_delete_and_replace_workflow(self, temp_kernel_repo, sample_current_results):
        """Test deleting old results and replacing with new ones."""
        git_mgr = GitManager(temp_kernel_repo)

        # Save initial results
        git_mgr.save_fstests_results(
            results=sample_current_results,
            target="branch",
            kernel_version="6.8.0-rc1",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Verify saved
        loaded = git_mgr.load_fstests_results()
        assert loaded["metadata"]["kernel_version"] == "6.8.0-rc1"

        # Delete
        success = git_mgr.delete_fstests_results()
        assert success is True

        # Verify deleted
        assert git_mgr.load_fstests_results() is None

        # Save new results (simulating final release)
        final_results = FstestsRunResult(
            success=True,
            total_tests=4,
            passed=4,
            failed=0,
            notrun=0,
            test_results=[
                TestResult(test_name="generic/001", status="passed", duration=5.0),
                TestResult(test_name="generic/002", status="passed", duration=3.0),
                TestResult(test_name="generic/003", status="passed", duration=8.0),
                TestResult(test_name="generic/004", status="passed", duration=2.0),
            ],
            duration=18.0,
        )

        git_mgr.save_fstests_results(
            results=final_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Verify new results
        loaded = git_mgr.load_fstests_results()
        assert loaded["metadata"]["kernel_version"] == "6.8.0"
        assert loaded["results"]["failed"] == 0

    def test_cross_filesystem_comparison(
        self, temp_kernel_repo, baseline_storage, sample_current_results
    ):
        """Test storing and comparing results for different filesystems."""
        git_mgr = GitManager(temp_kernel_repo)

        # Remember the original branch name
        original_branch = git_mgr.get_current_branch()

        # Save ext4 results
        git_mgr.save_fstests_results(
            results=sample_current_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick",
        )

        # Create a branch for btrfs testing
        subprocess.run(
            ["git", "checkout", "-b", "btrfs-testing"],
            cwd=temp_kernel_repo,
            check=True,
            capture_output=True,
        )

        # Make a new commit on btrfs branch so it has a different commit
        (temp_kernel_repo / "btrfs.txt").write_text("btrfs testing\n")
        subprocess.run(
            ["git", "add", "btrfs.txt"], cwd=temp_kernel_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add btrfs config"],
            cwd=temp_kernel_repo,
            check=True,
            capture_output=True,
        )

        # Save btrfs results
        btrfs_results = FstestsRunResult(
            success=True,
            total_tests=4,
            passed=4,
            failed=0,
            notrun=0,
            test_results=[
                TestResult(test_name="generic/001", status="passed", duration=6.0),
                TestResult(test_name="generic/002", status="passed", duration=4.0),
                TestResult(test_name="generic/003", status="passed", duration=9.0),
                TestResult(test_name="generic/004", status="passed", duration=3.0),
            ],
            duration=22.0,
        )

        git_mgr.save_fstests_results(
            results=btrfs_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="btrfs",
            test_selection="-g quick",
        )

        # Load btrfs results (current branch)
        btrfs_loaded = git_mgr.load_fstests_results()
        assert btrfs_loaded["metadata"]["fstype"] == "btrfs"

        # Load ext4 results (different branch) using saved branch name
        ext4_loaded = git_mgr.load_fstests_results(branch_name=original_branch)
        assert ext4_loaded["metadata"]["fstype"] == "ext4"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
