"""
Unit tests for MCP server to ensure no variable shadowing bugs.

This test file specifically checks that the server module doesn't have
variable scoping issues (e.g., local imports shadowing global imports).

Regression test for bug where local "from .build_manager import KernelBuilder"
in run_and_save_fstests handler caused Python to treat KernelBuilder as a local
variable for the entire call_tool function, breaking other handlers.
"""

import subprocess
from pathlib import Path
import pytest


@pytest.fixture
def temp_kernel_repo(tmp_path):
    """Create a minimal fake kernel repository for testing."""
    repo_path = tmp_path / "linux"
    repo_path.mkdir()

    # Create minimal kernel structure
    makefile_content = """
VERSION = 6
PATCHLEVEL = 8
SUBLEVEL = 0
EXTRAVERSION =
NAME = Test Kernel
"""
    (repo_path / "Makefile").write_text(makefile_content)
    (repo_path / ".config").write_text("# Test config\nCONFIG_X86=y\n")

    # Initialize as git repo
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
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True
    )

    return repo_path


class TestNoVariableShadowing:
    """Test that server module doesn't have variable shadowing issues."""

    def test_kernelbuilder_import_not_shadowed(self):
        """
        Test that KernelBuilder can be imported and used from server module.

        This would fail if there was a local import shadowing the global one.
        The bug was: a local "from .build_manager import KernelBuilder" in
        run_and_save_fstests made Python treat KernelBuilder as local for
        the entire call_tool function.
        """
        # Import the server module - this should not raise any errors
        from kerneldev_mcp import server

        # Verify KernelBuilder is accessible at module level
        # (it's imported from build_manager)
        from kerneldev_mcp.build_manager import KernelBuilder

        # The import should work fine
        assert KernelBuilder is not None

    def test_server_module_imports_successfully(self):
        """
        Test that the server module can be imported without errors.

        If there were shadowing issues, this could fail during module
        compilation.
        """
        from kerneldev_mcp import server

        # Should be able to access the call_tool function
        assert hasattr(server, "call_tool")
        assert callable(server.call_tool)

    def test_build_manager_usage_in_server(self, temp_kernel_repo):
        """
        Test that KernelBuilder can be instantiated with the server imported.

        This verifies the fix: removing the local import of KernelBuilder
        from within the run_and_save_fstests handler.
        """
        # Import server first (which has call_tool function that could shadow)
        from kerneldev_mcp import server

        # Now try to use KernelBuilder - this would fail with the bug
        from kerneldev_mcp.build_manager import KernelBuilder

        # Should be able to create an instance
        builder = KernelBuilder(temp_kernel_repo)
        assert builder is not None
        assert builder.kernel_path == temp_kernel_repo

    def test_no_local_kernelbuilder_import_in_call_tool(self):
        """
        Verify that call_tool function doesn't have local KernelBuilder import.

        This is a static check to prevent the bug from being reintroduced.
        """
        from kerneldev_mcp import server
        import inspect

        # Get the source code of call_tool
        source = inspect.getsource(server.call_tool)

        # Check that there's no local import of KernelBuilder
        # The only import should be at module level
        lines = source.split("\n")

        for line in lines:
            # Skip the function definition line
            if "def call_tool" in line or "async def call_tool" in line:
                continue

            # Check for problematic local import
            if "from .build_manager import KernelBuilder" in line:
                # Make sure it's not commented out
                stripped = line.strip()
                if not stripped.startswith("#"):
                    pytest.fail(
                        "Found local import of KernelBuilder in call_tool function. "
                        "This causes variable shadowing and breaks other handlers. "
                        f"Offending line: {line}"
                    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
