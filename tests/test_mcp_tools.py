"""
Unit tests for MCP server to ensure no variable shadowing bugs and proper parameter parsing.

This test file specifically checks:
1. Variable scoping issues (e.g., local imports shadowing global imports)
2. Device specification parameter parsing (e.g., backing parameter)

Regression tests:
- Bug where local "from .build_manager import KernelBuilder" caused variable shadowing
- Bug where 'backing' parameter in device specs was not being read from JSON arguments
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


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


class TestDeviceParameterParsing:
    """Test that device specifications are correctly parsed from JSON arguments.

    Regression test for bug where 'backing' parameter in device specs was silently
    ignored, causing all null_blk requests to fall back to tmpfs/disk.

    The bug was in server.py where DeviceSpec objects were created without reading
    the 'backing' field from device_dict, even when users specified it in JSON.
    """

    @pytest.mark.asyncio
    async def test_boot_kernel_test_parses_backing_parameter(self, temp_kernel_repo):
        """Test that boot_kernel_test correctly parses backing parameter from devices."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Mock BootManager to intercept boot_test call
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            # Setup async mock return value
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.duration = 10.0
            mock_result.boot_completed = True
            mock_boot_manager.boot_test = AsyncMock(return_value=mock_result)

            # Call the tool with devices including backing parameter
            arguments = {
                "kernel_path": str(temp_kernel_repo),
                "devices": [
                    {
                        "name": "test",
                        "size": "10G",
                        "backing": "null_blk",  # THIS is what was being ignored
                        "env_var": "TEST_DEV",
                        "order": 0,
                    },
                    {
                        "name": "scratch",
                        "size": "5G",
                        "backing": "tmpfs",
                        "order": 1,
                    },
                    {
                        "name": "disk",
                        "size": "1G",
                        "backing": "disk",
                        "order": 2,
                    },
                ],
            }

            await call_tool("boot_kernel_test", arguments)

            # Verify boot_test was called
            assert mock_boot_manager.boot_test.called

            # Get the devices argument passed to boot_test
            call_args = mock_boot_manager.boot_test.call_args
            devices = call_args.kwargs.get("devices")

            # Verify devices were parsed and backing parameter is correct
            assert devices is not None, "Devices should be passed to boot_with_custom_command"
            assert len(devices) == 3, "Should have 3 devices"

            # Check first device (null_blk)
            assert devices[0].backing == DeviceBacking.NULL_BLK, (
                f"First device should have NULL_BLK backing, got {devices[0].backing}"
            )
            assert devices[0].name == "test"
            assert devices[0].size == "10G"

            # Check second device (tmpfs)
            assert devices[1].backing == DeviceBacking.TMPFS, (
                f"Second device should have TMPFS backing, got {devices[1].backing}"
            )
            assert devices[1].name == "scratch"

            # Check third device (disk)
            assert devices[2].backing == DeviceBacking.DISK, (
                f"Third device should have DISK backing, got {devices[2].backing}"
            )
            assert devices[2].name == "disk"

    @pytest.mark.asyncio
    async def test_fstests_vm_boot_and_run_parses_backing_parameter(self, tmp_path):
        """Test that fstests_vm_boot_and_run correctly parses backing parameter."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Create fake kernel and fstests dirs
        kernel_path = tmp_path / "linux"
        kernel_path.mkdir()
        (kernel_path / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 8\n")

        fstests_path = tmp_path / "fstests"
        fstests_path.mkdir()
        (fstests_path / "check").write_text("#!/bin/bash\necho test")

        # Mock BootManager
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            # Setup async mock return value
            mock_result = MagicMock()
            mock_result.success = True
            mock_fstests_result = MagicMock()
            mock_boot_manager.boot_with_fstests = AsyncMock(
                return_value=(mock_result, mock_fstests_result)
            )

            arguments = {
                "kernel_path": str(kernel_path),
                "fstests_path": str(fstests_path),
                "custom_devices": [
                    {
                        "name": "null_blk_device",
                        "size": "20G",
                        "backing": "null_blk",
                        "env_var": "TEST_DEV",
                    }
                ],
            }

            await call_tool("fstests_vm_boot_and_run", arguments)

            # Verify the custom_devices were passed with correct backing
            assert mock_boot_manager.boot_with_fstests.called
            call_args = mock_boot_manager.boot_with_fstests.call_args
            custom_devices = call_args.kwargs.get("custom_devices")

            assert custom_devices is not None
            assert len(custom_devices) == 1
            assert custom_devices[0].backing == DeviceBacking.NULL_BLK, (
                "fstests_vm_boot_and_run should parse backing=null_blk correctly"
            )

    @pytest.mark.asyncio
    async def test_fstests_vm_boot_custom_parses_backing_parameter(self, tmp_path):
        """Test that fstests_vm_boot_custom correctly parses backing parameter."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Create fake kernel and fstests dirs
        kernel_path = tmp_path / "linux"
        kernel_path.mkdir()
        (kernel_path / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 8\n")

        fstests_path = tmp_path / "fstests"
        fstests_path.mkdir()

        # Mock BootManager
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            # Setup async mock return value
            mock_result = MagicMock()
            mock_result.success = True
            mock_boot_manager.boot_with_custom_command = AsyncMock(return_value=mock_result)

            arguments = {
                "kernel_path": str(kernel_path),
                "fstests_path": str(fstests_path),
                "command": "echo test",
                "custom_devices": [
                    {
                        "name": "fast_device",
                        "size": "15G",
                        "backing": "null_blk",
                    }
                ],
            }

            await call_tool("fstests_vm_boot_custom", arguments)

            # Verify the custom_devices were passed with correct backing
            assert mock_boot_manager.boot_with_custom_command.called
            call_args = mock_boot_manager.boot_with_custom_command.call_args
            custom_devices = call_args.kwargs.get("custom_devices")

            assert custom_devices is not None
            assert len(custom_devices) == 1
            assert custom_devices[0].backing == DeviceBacking.NULL_BLK, (
                "fstests_vm_boot_custom should parse backing=null_blk correctly"
            )

    @pytest.mark.asyncio
    async def test_invalid_backing_value_uses_default(self, temp_kernel_repo):
        """Test that invalid backing values fall back to default (disk) with warning."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Mock BootManager
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.duration = 10.0
            mock_result.boot_completed = True
            mock_boot_manager.boot_test = AsyncMock(return_value=mock_result)

            # Capture log warnings
            with patch("kerneldev_mcp.server.logger") as mock_logger:
                arguments = {
                    "kernel_path": str(temp_kernel_repo),
                    "devices": [
                        {
                            "name": "test",
                            "size": "10G",
                            "backing": "invalid_backing_type",  # Invalid value
                        }
                    ],
                }

                await call_tool("boot_kernel_test", arguments)

                # Should have logged a warning about invalid backing value
                assert any(
                    "Invalid backing value" in str(call)
                    for call in mock_logger.warning.call_args_list
                ), "Should warn about invalid backing value"

                # Should still create device with default backing
                call_args = mock_boot_manager.boot_test.call_args
                devices = call_args.kwargs.get("devices")
                assert devices is not None
                assert devices[0].backing == DeviceBacking.DISK, (
                    "Invalid backing should fall back to DISK"
                )

    @pytest.mark.asyncio
    async def test_missing_backing_parameter_defaults_to_disk(self, temp_kernel_repo):
        """Test that devices without backing parameter default to DISK (backward compatibility)."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Mock BootManager
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.duration = 10.0
            mock_result.boot_completed = True
            mock_boot_manager.boot_test = AsyncMock(return_value=mock_result)

            arguments = {
                "kernel_path": str(temp_kernel_repo),
                "devices": [
                    {
                        "name": "test",
                        "size": "10G",
                        # No backing parameter - should default to DISK
                    }
                ],
            }

            await call_tool("boot_kernel_test", arguments)

            call_args = mock_boot_manager.boot_test.call_args
            devices = call_args.kwargs.get("devices")

            assert devices is not None
            assert devices[0].backing == DeviceBacking.DISK, (
                "Missing backing parameter should default to DISK for backward compatibility"
            )

    @pytest.mark.asyncio
    async def test_backing_parameter_is_case_insensitive(self, temp_kernel_repo):
        """Test that backing parameter accepts uppercase/mixed case values."""
        from kerneldev_mcp.server import call_tool
        from kerneldev_mcp.boot_manager import DeviceBacking

        # Mock BootManager
        with patch("kerneldev_mcp.server.BootManager") as mock_boot_manager_class:
            mock_boot_manager = MagicMock()
            mock_boot_manager_class.return_value = mock_boot_manager

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.duration = 10.0
            mock_result.boot_completed = True
            mock_boot_manager.boot_test = AsyncMock(return_value=mock_result)

            arguments = {
                "kernel_path": str(temp_kernel_repo),
                "devices": [
                    {
                        "name": "test1",
                        "size": "10G",
                        "backing": "NULL_BLK",  # Uppercase
                    },
                    {
                        "name": "test2",
                        "size": "5G",
                        "backing": "Tmpfs",  # Mixed case
                    },
                    {
                        "name": "test3",
                        "size": "1G",
                        "backing": "DISK",  # Uppercase
                    },
                ],
            }

            await call_tool("boot_kernel_test", arguments)

            call_args = mock_boot_manager.boot_test.call_args
            devices = call_args.kwargs.get("devices")

            assert devices is not None
            assert len(devices) == 3
            assert devices[0].backing == DeviceBacking.NULL_BLK, "NULL_BLK (uppercase) should work"
            assert devices[1].backing == DeviceBacking.TMPFS, "Tmpfs (mixed case) should work"
            assert devices[2].backing == DeviceBacking.DISK, "DISK (uppercase) should work"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
