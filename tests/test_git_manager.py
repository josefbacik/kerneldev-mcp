"""
Unit tests for git_manager module.
"""
import json
import subprocess
import tempfile
from pathlib import Path
import pytest

from kerneldev_mcp.git_manager import GitManager, FSTESTS_NOTES_REF
from kerneldev_mcp.fstests_manager import FstestsRunResult, TestResult


@pytest.fixture
def temp_git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path, check=True, capture_output=True
    )

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path, check=True, capture_output=True
    )

    return repo_path


@pytest.fixture
def sample_test_results():
    """Create sample fstests results."""
    test_results = [
        TestResult(test_name="generic/001", status="passed", duration=5.2),
        TestResult(test_name="generic/002", status="passed", duration=3.1),
        TestResult(test_name="generic/003", status="failed", duration=10.5, failure_reason="Timeout"),
        TestResult(test_name="generic/004", status="notrun", duration=0.0),
    ]

    return FstestsRunResult(
        success=False,
        total_tests=4,
        passed=2,
        failed=1,
        notrun=1,
        test_results=test_results,
        duration=18.8
    )


class TestGitManager:
    """Test GitManager class."""

    def test_init_valid_repo(self, temp_git_repo):
        """Test initialization with valid git repository."""
        git_mgr = GitManager(temp_git_repo)
        assert git_mgr.repo_path == temp_git_repo

    def test_init_invalid_repo(self, tmp_path):
        """Test initialization with non-git directory."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()

        with pytest.raises(ValueError, match="Not a git repository"):
            GitManager(non_repo)

    def test_get_current_commit(self, temp_git_repo):
        """Test getting current commit SHA."""
        git_mgr = GitManager(temp_git_repo)
        commit_sha = git_mgr.get_current_commit()

        assert commit_sha is not None
        assert len(commit_sha) == 40  # Full SHA
        assert all(c in '0123456789abcdef' for c in commit_sha)

    def test_get_current_branch(self, temp_git_repo):
        """Test getting current branch name."""
        git_mgr = GitManager(temp_git_repo)

        # Should be on default branch (master or main)
        branch = git_mgr.get_current_branch()
        assert branch in ("master", "main")

    def test_get_current_branch_detached(self, temp_git_repo):
        """Test getting branch name when in detached HEAD state."""
        git_mgr = GitManager(temp_git_repo)

        # Get current commit
        commit_sha = git_mgr.get_current_commit()

        # Checkout detached HEAD
        subprocess.run(
            ["git", "checkout", commit_sha],
            cwd=temp_git_repo,
            check=True,
            capture_output=True
        )

        # Should return None for detached HEAD
        branch = git_mgr.get_current_branch()
        assert branch is None

    def test_get_branch_commit(self, temp_git_repo):
        """Test getting commit SHA for a branch."""
        git_mgr = GitManager(temp_git_repo)

        # Get current branch
        branch = git_mgr.get_current_branch()

        # Get its commit
        commit_sha = git_mgr.get_branch_commit(branch)

        # Should match current commit
        assert commit_sha == git_mgr.get_current_commit()

    def test_save_and_load_results_branch(self, temp_git_repo, sample_test_results):
        """Test saving and loading results attached to branch."""
        git_mgr = GitManager(temp_git_repo)

        # Save to branch
        success = git_mgr.save_fstests_results(
            results=sample_test_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        assert success is True

        # Load back
        data = git_mgr.load_fstests_results()

        assert data is not None
        assert data["metadata"]["kernel_version"] == "6.8.0"
        assert data["metadata"]["fstype"] == "ext4"
        assert data["metadata"]["test_selection"] == "-g quick"
        assert data["results"]["total_tests"] == 4
        assert data["results"]["passed"] == 2
        assert data["results"]["failed"] == 1

    def test_save_and_load_results_commit(self, temp_git_repo, sample_test_results):
        """Test saving and loading results attached to commit."""
        git_mgr = GitManager(temp_git_repo)
        commit_sha = git_mgr.get_current_commit()

        # Save to commit
        success = git_mgr.save_fstests_results(
            results=sample_test_results,
            target="commit",
            commit_sha=commit_sha,
            kernel_version="6.8.0",
            fstype="btrfs",
            test_selection="generic/001"
        )

        assert success is True

        # Load back
        data = git_mgr.load_fstests_results(commit_sha=commit_sha)

        assert data is not None
        assert data["metadata"]["commit_sha"] == commit_sha
        assert data["metadata"]["fstype"] == "btrfs"

    def test_load_fstests_run_result(self, temp_git_repo, sample_test_results):
        """Test loading results as FstestsRunResult object."""
        git_mgr = GitManager(temp_git_repo)

        # Save
        git_mgr.save_fstests_results(
            results=sample_test_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        # Load as FstestsRunResult
        result = git_mgr.load_fstests_run_result()

        assert result is not None
        assert isinstance(result, FstestsRunResult)
        assert result.total_tests == 4
        assert result.passed == 2
        assert result.failed == 1
        assert len(result.test_results) == 4

        # Check test details
        failed_test = next(t for t in result.test_results if t.status == "failed")
        assert failed_test.test_name == "generic/003"
        assert failed_test.failure_reason == "Timeout"

    def test_load_nonexistent_results(self, temp_git_repo):
        """Test loading results when none exist."""
        git_mgr = GitManager(temp_git_repo)

        data = git_mgr.load_fstests_results()
        assert data is None

        result = git_mgr.load_fstests_run_result()
        assert result is None

    def test_overwrite_results(self, temp_git_repo, sample_test_results):
        """Test overwriting existing results."""
        git_mgr = GitManager(temp_git_repo)

        # Save first results
        git_mgr.save_fstests_results(
            results=sample_test_results,
            target="branch",
            kernel_version="6.7.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        # Create new results
        new_results = FstestsRunResult(
            success=True,
            total_tests=2,
            passed=2,
            failed=0,
            notrun=0,
            test_results=[
                TestResult(test_name="generic/001", status="passed", duration=5.2),
                TestResult(test_name="generic/002", status="passed", duration=3.1),
            ],
            duration=8.3
        )

        # Overwrite with new results
        success = git_mgr.save_fstests_results(
            results=new_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        assert success is True

        # Load back - should have new results
        result = git_mgr.load_fstests_run_result()
        assert result.total_tests == 2
        assert result.passed == 2
        assert result.failed == 0

        # Check metadata is updated
        data = git_mgr.load_fstests_results()
        assert data["metadata"]["kernel_version"] == "6.8.0"

    def test_list_commits_with_results(self, temp_git_repo, sample_test_results):
        """Test listing commits with results."""
        git_mgr = GitManager(temp_git_repo)

        # Initially no results
        results = git_mgr.list_commits_with_results()
        assert len(results) == 0

        # Add results to current commit
        git_mgr.save_fstests_results(
            results=sample_test_results,
            target="commit",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        # Create another commit
        (temp_git_repo / "file.txt").write_text("test")
        subprocess.run(["git", "add", "file.txt"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Second commit"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True
        )

        # Add results to new commit
        git_mgr.save_fstests_results(
            results=sample_test_results,
            target="commit",
            kernel_version="6.9.0",
            fstype="btrfs",
            test_selection="-g auto"
        )

        # List results
        results = git_mgr.list_commits_with_results()
        assert len(results) == 2

        # Check metadata
        assert results[0].kernel_version in ("6.8.0", "6.9.0")
        assert results[0].fstype in ("ext4", "btrfs")

    def test_delete_results(self, temp_git_repo, sample_test_results):
        """Test deleting results."""
        git_mgr = GitManager(temp_git_repo)

        # Save results
        git_mgr.save_fstests_results(
            results=sample_test_results,
            target="branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        # Verify they exist
        assert git_mgr.load_fstests_results() is not None

        # Delete
        success = git_mgr.delete_fstests_results()
        assert success is True

        # Verify they're gone
        assert git_mgr.load_fstests_results() is None

    def test_delete_nonexistent_results(self, temp_git_repo):
        """Test deleting results that don't exist."""
        git_mgr = GitManager(temp_git_repo)

        # Try to delete when no results exist
        success = git_mgr.delete_fstests_results()
        # Git notes remove returns error when note doesn't exist
        assert success is False

    def test_save_to_specific_branch(self, temp_git_repo, sample_test_results):
        """Test saving results to a specific branch."""
        git_mgr = GitManager(temp_git_repo)

        # Create a new branch
        subprocess.run(
            ["git", "checkout", "-b", "test-branch"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True
        )

        # Save to this branch explicitly
        success = git_mgr.save_fstests_results(
            results=sample_test_results,
            target="branch",
            branch_name="test-branch",
            kernel_version="6.8.0",
            fstype="ext4",
            test_selection="-g quick"
        )

        assert success is True

        # Load from this branch
        data = git_mgr.load_fstests_results(branch_name="test-branch")
        assert data is not None
        assert data["metadata"]["branch_name"] == "test-branch"

    def test_max_count_limit(self, temp_git_repo, sample_test_results):
        """Test max_count parameter in list_commits_with_results."""
        git_mgr = GitManager(temp_git_repo)

        # Create 5 commits with results
        for i in range(5):
            (temp_git_repo / f"file{i}.txt").write_text(f"test {i}")
            subprocess.run(
                ["git", "add", f"file{i}.txt"],
                cwd=temp_git_repo,
                check=True,
                capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"Commit {i}"],
                cwd=temp_git_repo,
                check=True,
                capture_output=True
            )

            git_mgr.save_fstests_results(
                results=sample_test_results,
                target="commit",
                kernel_version=f"6.{i}.0",
                fstype="ext4",
                test_selection="-g quick"
            )

        # List with max_count
        results = git_mgr.list_commits_with_results(max_count=3)
        assert len(results) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
