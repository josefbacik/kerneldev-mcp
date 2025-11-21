"""
Regression tests for boot_kernel_with_fstests filesystem type handling.

Tests ensure that:
1. fstype parameter is properly passed and used
2. Auto-detection logic doesn't exist (regression test for bug)
3. Success detection checks actual test results, not just VM boot
"""

import inspect
import re
from pathlib import Path
import pytest


class TestBootFstestsFilesystemType:
    """Test filesystem type handling in boot_with_fstests."""

    def test_boot_with_fstests_has_fstype_parameter(self):
        """
        Verify that boot_with_fstests method has fstype parameter.

        Regression test for bug where fstype was auto-detected from test args
        instead of being an explicit parameter.
        """
        from kerneldev_mcp.boot_manager import BootManager

        # Get method signature
        sig = inspect.signature(BootManager.boot_with_fstests)

        # Check fstype parameter exists
        assert "fstype" in sig.parameters, "boot_with_fstests must have 'fstype' parameter"

        # Check it has a default value
        param = sig.parameters["fstype"]
        assert param.default != inspect.Parameter.empty, (
            "fstype parameter should have a default value"
        )

        # Default should be a reasonable filesystem
        assert param.default in ("ext4", "btrfs", "xfs"), (
            f"fstype default '{param.default}' should be a common filesystem"
        )

    def test_no_filesystem_auto_detection_in_boot_with_fstests(self):
        """
        Ensure no filesystem type auto-detection logic exists.

        Regression test for the bug where code checked if "btrfs" appeared
        in test arguments to determine filesystem type. This was unreliable
        (e.g., "-g auto" on btrfs would default to ext4).
        """
        from kerneldev_mcp import boot_manager

        # Get source code of boot_with_fstests method
        source = inspect.getsource(boot_manager.BootManager.boot_with_fstests)

        # Check for problematic auto-detection patterns
        problematic_patterns = [
            r'if.*"btrfs".*in.*test',  # if "btrfs" in tests
            r'fstype\s*=\s*"ext4"\s*\n.*if.*btrfs',  # fstype = "ext4" followed by btrfs check
            r"any\(.*btrfs.*for.*in.*test",  # any("btrfs" ... for ... in tests)
        ]

        for pattern in problematic_patterns:
            matches = re.findall(pattern, source, re.IGNORECASE)
            assert not matches, (
                f"Found filesystem auto-detection code (pattern: {pattern}). "
                f"Filesystem type should be an explicit parameter, not auto-detected. "
                f"Matches: {matches}"
            )

    def test_boot_with_fstests_docstring_mentions_fstype(self):
        """
        Verify that method documentation mentions fstype parameter.
        """
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_fstests.__doc__
        assert docstring is not None, "boot_with_fstests should have documentation"

        # Check that fstype is documented
        assert "fstype" in docstring.lower(), "Documentation should mention 'fstype' parameter"

    def test_mcp_tool_has_fstype_in_schema(self):
        """
        Verify that fstests_vm_boot_and_run MCP tool has fstype in schema.
        """
        from kerneldev_mcp import server

        # Get the source code to check that fstype is in the tool definition
        source = inspect.getsource(server)

        # Find the fstests_vm_boot_and_run tool definition
        # Look for Tool(...name="fstests_vm_boot_and_run"...)
        assert 'name="fstests_vm_boot_and_run"' in source, (
            "fstests_vm_boot_and_run tool should be defined"
        )

        # Extract the tool definition section
        lines = source.split("\n")
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_and_run"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find tool definition"

        # Get next ~100 lines (the tool schema - increased to cover longer descriptions)
        tool_def = "\n".join(lines[tool_start : tool_start + 100])

        # Check that fstype is in the schema
        assert '"fstype"' in tool_def or "'fstype'" in tool_def, (
            "Tool schema should include 'fstype' property"
        )

        # Check it has a description
        # The fstype property should be followed by a description
        assert "filesystem" in tool_def.lower(), (
            "Tool schema should describe fstype as filesystem-related"
        )


class TestBootFstestsBasicFunctionality:
    """Basic smoke tests to catch runtime errors like import scoping issues."""

    def test_boot_with_fstests_method_exists_and_callable(self):
        """
        Verify boot_with_fstests method can be accessed without errors.

        Regression test for import scoping bug where accessing the method
        would fail with "cannot access local variable 'os' where it is not
        associated with a value" due to redundant imports in try blocks.
        """
        from kerneldev_mcp.boot_manager import BootManager
        import tempfile

        # Use a real temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            kernel_path = Path(tmpdir)

            # This should not raise any import-related errors
            boot_mgr = BootManager(kernel_path)

            # Verify the method exists and is callable
            assert hasattr(boot_mgr, "boot_with_fstests")
            assert callable(boot_mgr.boot_with_fstests)

            # Get the method to ensure no NameError or scoping issues
            method = boot_mgr.boot_with_fstests
            assert method is not None


class TestFstestsDeviceSetupScript:
    """Test that device setup script generation correctly substitutes variables."""

    def test_fstype_substitution_in_device_setup_script(self):
        """
        Verify that fstype is properly substituted in generated device setup script.

        Regression test for bug where mkfs_script used a regular string instead of
        an f-string, causing {fstype} to appear literally in the script instead of
        being replaced with the actual filesystem type value.
        """
        from kerneldev_mcp.boot_manager import BootManager
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            kernel_path = Path(tmpdir)
            boot_mgr = BootManager(kernel_path)

            # Test all built-in filesystem types
            for fstype in ["ext4", "xfs", "btrfs", "f2fs"]:
                script = boot_mgr._generate_fstests_device_setup_script(
                    fstype=fstype, io_scheduler="mq-deadline", fstests_path="/tmp/fstests"
                )

                # The script should contain the actual filesystem type value
                assert fstype in script, (
                    f"Generated script should contain actual filesystem type '{fstype}'"
                )

                # The script should NOT contain the literal string "{fstype}"
                assert "{fstype}" not in script, (
                    "Generated script should not contain literal '{fstype}' placeholder. "
                    "This indicates the string is not being properly interpolated as an f-string."
                )

                # Verify the script has the expected case statement with the fstype
                assert f'case "{fstype}" in' in script, (
                    f"Script should have 'case \"{fstype}\" in' statement"
                )

                # Verify error messages include the actual fstype
                assert f"Failed to format $TEST_DEV as {fstype}" in script, (
                    f"Error messages should reference actual fstype '{fstype}'"
                )

                # Verify success message includes the actual fstype
                assert f"Formatted $TEST_DEV as {fstype}" in script, (
                    f"Success message should reference actual fstype '{fstype}'"
                )

    def test_custom_mkfs_command_fstype_substitution(self):
        """
        Verify fstype substitution works with custom_mkfs_command.

        The custom mkfs path uses double braces {{fstype}} because it's already
        in an f-string, so this test ensures that pattern still works correctly.
        """
        from kerneldev_mcp.boot_manager import BootManager
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            kernel_path = Path(tmpdir)
            boot_mgr = BootManager(kernel_path)

            # Test with custom mkfs command
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="bcachefs",
                io_scheduler="mq-deadline",
                fstests_path="/tmp/fstests",
                custom_mkfs_command="mkfs.bcachefs",
            )

            # Should contain "bcachefs" (the actual fstype value)
            assert "bcachefs" in script, "Script should contain actual fstype value"

            # Should NOT contain literal {fstype} or {{fstype}}
            assert "{fstype}" not in script, "Script should not contain single-brace placeholder"
            assert "{{fstype}}" not in script, "Script should not contain double-brace placeholder"


class TestBootFstestsSuccessDetection:
    """Test success detection in fstests_vm_boot_and_run."""

    def test_handler_checks_fstests_success(self):
        """
        Verify that the MCP tool handler checks fstests_result.success.

        Regression test for bug where handler only checked if results existed,
        not if tests actually passed.
        """
        from kerneldev_mcp import server
        import inspect

        # Get source code of call_tool function
        source = inspect.getsource(server.call_tool)

        # Find the fstests_vm_boot_and_run handler section
        # Look for the section after "elif name == 'fstests_vm_boot_and_run'"
        lines = source.split("\n")
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_and_run" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find fstests_vm_boot_and_run handler"

        # Get next ~100 lines (the handler code)
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Check that we check fstests_result.success
        # This ensures we don't just report success because VM booted
        success_check_patterns = [
            r"fstests_result\.success",
            r"not.*fstests_result\.success",
            r"if.*success",  # General success check
        ]

        found_success_check = False
        for pattern in success_check_patterns:
            if re.search(pattern, handler_code):
                found_success_check = True
                break

        assert found_success_check, (
            "Handler should check fstests_result.success, not just if results exist. "
            "This ensures we detect when tests fail even if VM boots successfully."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
