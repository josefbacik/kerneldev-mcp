"""
Git operations manager for storing and retrieving fstests results as git notes.

Git notes allow storing metadata attached to commits without modifying the commit itself.
We use a custom notes ref 'refs/notes/fstests' to store test results.
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .fstests_manager import FstestsRunResult, TestResult

logger = logging.getLogger(__name__)

# Custom git notes ref for fstests results
FSTESTS_NOTES_REF = "refs/notes/fstests"


@dataclass
class GitNoteMetadata:
    """Metadata for a git note containing fstests results."""

    commit_sha: str
    branch_name: Optional[str]
    kernel_version: Optional[str]
    fstype: str
    test_selection: str
    created_at: str  # ISO format timestamp


class GitManager:
    """Manages git operations for storing fstests results."""

    def __init__(self, repo_path: Path):
        """Initialize git manager.

        Args:
            repo_path: Path to git repository

        Raises:
            ValueError: If path is not a git repository
        """
        self.repo_path = Path(repo_path).resolve()

        # Verify it's a git repo
        if not self._is_git_repo():
            raise ValueError(f"Not a git repository: {repo_path}")

    def _is_git_repo(self) -> bool:
        """Check if path is a git repository.

        Returns:
            True if git repository
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def get_current_commit(self) -> Optional[str]:
        """Get current commit SHA.

        Returns:
            Commit SHA or None on error
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            return result.stdout.strip()
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Failed to get current commit: {e}")
            return None

    def get_current_branch(self) -> Optional[str]:
        """Get current branch name.

        Returns:
            Branch name or None if detached HEAD
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            branch = result.stdout.strip()
            # 'HEAD' means detached
            return None if branch == "HEAD" else branch
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Failed to get current branch: {e}")
            return None

    def get_branch_commit(self, branch_name: str) -> Optional[str]:
        """Get the commit SHA for a branch.

        Args:
            branch_name: Name of branch

        Returns:
            Commit SHA or None on error
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", branch_name],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            return result.stdout.strip()
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Failed to get branch commit: {e}")
            return None

    def save_fstests_results(
        self,
        results: FstestsRunResult,
        target: str = "branch",
        branch_name: Optional[str] = None,
        commit_sha: Optional[str] = None,
        kernel_version: Optional[str] = None,
        fstype: str = "ext4",
        test_selection: str = "-g quick"
    ) -> bool:
        """Save fstests results as a git note.

        Args:
            results: Test results to save
            target: Where to attach note - 'branch' or 'commit'
            branch_name: Branch name (for branch target, defaults to current)
            commit_sha: Commit SHA (for commit target, defaults to HEAD)
            kernel_version: Kernel version string
            fstype: Filesystem type tested
            test_selection: Test selection used

        Returns:
            True if successful
        """
        from datetime import datetime

        # Determine target commit
        if target == "branch":
            if branch_name is None:
                branch_name = self.get_current_branch()
                if branch_name is None:
                    logger.error("Not on a branch and no branch_name provided")
                    return False

            target_commit = self.get_branch_commit(branch_name)
            if target_commit is None:
                logger.error(f"Could not resolve branch: {branch_name}")
                return False
        else:  # commit
            if commit_sha is None:
                target_commit = self.get_current_commit()
            else:
                target_commit = commit_sha

            if target_commit is None:
                logger.error("Could not determine target commit")
                return False
            branch_name = None  # Not attached to a branch

        # Build note data
        note_data = {
            "metadata": {
                "commit_sha": target_commit,
                "branch_name": branch_name,
                "kernel_version": kernel_version,
                "fstype": fstype,
                "test_selection": test_selection,
                "created_at": datetime.now().isoformat()
            },
            "results": {
                "success": results.success,
                "total_tests": results.total_tests,
                "passed": results.passed,
                "failed": results.failed,
                "notrun": results.notrun,
                "duration": results.duration,
                "test_results": [
                    {
                        "test_name": t.test_name,
                        "status": t.status,
                        "duration": t.duration,
                        "failure_reason": t.failure_reason
                    }
                    for t in results.test_results
                ]
            }
        }

        # Convert to JSON
        note_content = json.dumps(note_data, indent=2)

        # Save as git note
        try:
            # Use git notes add with --force to overwrite existing notes
            process = subprocess.run(
                ["git", "notes", "--ref", FSTESTS_NOTES_REF, "add", "-f", "-m", note_content, target_commit],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            if process.returncode != 0:
                logger.error(f"Failed to add git note: {process.stderr}")
                return False

            logger.info(f"Saved fstests results to git note on {target_commit[:8]}")
            if branch_name:
                logger.info(f"  Attached to branch: {branch_name}")

            return True

        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Error saving git note: {e}")
            return False

    def load_fstests_results(
        self,
        branch_name: Optional[str] = None,
        commit_sha: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Load fstests results from a git note.

        Args:
            branch_name: Branch name to load from (defaults to current)
            commit_sha: Commit SHA to load from (overrides branch_name)

        Returns:
            Dictionary with metadata and results, or None if not found
        """
        # Determine target commit
        if commit_sha:
            target_commit = commit_sha
        elif branch_name:
            target_commit = self.get_branch_commit(branch_name)
            if target_commit is None:
                logger.error(f"Could not resolve branch: {branch_name}")
                return None
        else:
            # Default to current commit
            target_commit = self.get_current_commit()
            if target_commit is None:
                return None

        # Load git note
        try:
            result = subprocess.run(
                ["git", "notes", "--ref", FSTESTS_NOTES_REF, "show", target_commit],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                # No note found
                logger.debug(f"No fstests note found for {target_commit[:8]}")
                return None

            # Parse JSON
            note_data = json.loads(result.stdout)
            return note_data

        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
            logger.error(f"Error loading git note: {e}")
            return None

    def load_fstests_run_result(
        self,
        branch_name: Optional[str] = None,
        commit_sha: Optional[str] = None
    ) -> Optional[FstestsRunResult]:
        """Load fstests results as FstestsRunResult object.

        Args:
            branch_name: Branch name to load from
            commit_sha: Commit SHA to load from

        Returns:
            FstestsRunResult or None if not found
        """
        data = self.load_fstests_results(branch_name=branch_name, commit_sha=commit_sha)

        if data is None:
            return None

        # Reconstruct FstestsRunResult
        try:
            results_data = data["results"]
            test_results = [
                TestResult(
                    test_name=t["test_name"],
                    status=t["status"],
                    duration=t["duration"],
                    failure_reason=t.get("failure_reason")
                )
                for t in results_data["test_results"]
            ]

            return FstestsRunResult(
                success=results_data["success"],
                total_tests=results_data["total_tests"],
                passed=results_data["passed"],
                failed=results_data["failed"],
                notrun=results_data["notrun"],
                test_results=test_results,
                duration=results_data["duration"]
            )
        except (KeyError, TypeError) as e:
            logger.error(f"Invalid note data format: {e}")
            return None

    def list_commits_with_results(self, max_count: int = 20) -> List[GitNoteMetadata]:
        """List commits that have fstests results stored.

        Args:
            max_count: Maximum number of commits to return

        Returns:
            List of GitNoteMetadata
        """
        try:
            # List all notes in our ref
            result = subprocess.run(
                ["git", "notes", "--ref", FSTESTS_NOTES_REF, "list"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return []

            metadata_list = []

            # Each line is: <note-sha> <object-sha>
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split()
                if len(parts) != 2:
                    continue

                _, commit_sha = parts

                # Load the note data
                note_data = self.load_fstests_results(commit_sha=commit_sha)
                if note_data and "metadata" in note_data:
                    meta = note_data["metadata"]
                    metadata_list.append(GitNoteMetadata(
                        commit_sha=meta["commit_sha"],
                        branch_name=meta.get("branch_name"),
                        kernel_version=meta.get("kernel_version"),
                        fstype=meta["fstype"],
                        test_selection=meta["test_selection"],
                        created_at=meta["created_at"]
                    ))

                if len(metadata_list) >= max_count:
                    break

            return metadata_list

        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Error listing notes: {e}")
            return []

    def delete_fstests_results(
        self,
        branch_name: Optional[str] = None,
        commit_sha: Optional[str] = None
    ) -> bool:
        """Delete fstests results for a commit.

        Args:
            branch_name: Branch name
            commit_sha: Commit SHA

        Returns:
            True if successful
        """
        # Determine target commit
        if commit_sha:
            target_commit = commit_sha
        elif branch_name:
            target_commit = self.get_branch_commit(branch_name)
            if target_commit is None:
                return False
        else:
            target_commit = self.get_current_commit()
            if target_commit is None:
                return False

        try:
            result = subprocess.run(
                ["git", "notes", "--ref", FSTESTS_NOTES_REF, "remove", target_commit],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            return result.returncode == 0

        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Error deleting note: {e}")
            return False
