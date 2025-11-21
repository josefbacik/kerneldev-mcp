"""
Tests for custom_mkfs_command functionality.

Tests ensure that:
1. custom_mkfs_command parameter is available in boot methods
2. The parameter is properly passed through MCP tool schemas
3. The script generation correctly uses custom mkfs commands
4. Unknown filesystem types with custom_mkfs_command don't default to ext4
"""

import inspect
from pathlib import Path
import tempfile
import pytest


class TestCustomMkfsCommandParameter:
    """Test that custom_mkfs_command parameter exists in relevant methods."""

    def test_boot_with_fstests_has_custom_mkfs_command_parameter(self):
        """Verify boot_with_fstests has custom_mkfs_command parameter."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager.boot_with_fstests)

        assert "custom_mkfs_command" in sig.parameters, (
            "boot_with_fstests must have 'custom_mkfs_command' parameter"
        )

        param = sig.parameters["custom_mkfs_command"]
        assert param.default is None, "custom_mkfs_command should default to None"

    def test_boot_with_custom_command_has_custom_mkfs_command_parameter(self):
        """Verify boot_with_custom_command has custom_mkfs_command parameter."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager.boot_with_custom_command)

        assert "custom_mkfs_command" in sig.parameters, (
            "boot_with_custom_command must have 'custom_mkfs_command' parameter"
        )

        param = sig.parameters["custom_mkfs_command"]
        assert param.default is None, "custom_mkfs_command should default to None"

    def test_generate_fstests_device_setup_script_has_custom_mkfs_command(self):
        """Verify _generate_fstests_device_setup_script accepts custom_mkfs_command."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager._generate_fstests_device_setup_script)

        assert "custom_mkfs_command" in sig.parameters, (
            "_generate_fstests_device_setup_script must have 'custom_mkfs_command' parameter"
        )

        param = sig.parameters["custom_mkfs_command"]
        assert param.default is None, "custom_mkfs_command should default to None"


class TestCustomMkfsCommandDocumentation:
    """Test that custom_mkfs_command is properly documented."""

    def test_boot_with_fstests_docstring_mentions_custom_mkfs_command(self):
        """Verify boot_with_fstests documentation mentions custom_mkfs_command."""
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_fstests.__doc__
        assert docstring is not None, "boot_with_fstests should have documentation"

        assert "custom_mkfs_command" in docstring, (
            "Documentation should mention 'custom_mkfs_command' parameter"
        )

        # Check it mentions example usage
        assert "mkfs" in docstring.lower(), "Documentation should explain mkfs usage"

    def test_boot_with_custom_command_docstring_mentions_custom_mkfs_command(self):
        """Verify boot_with_custom_command documentation mentions custom_mkfs_command."""
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_custom_command.__doc__
        assert docstring is not None, "boot_with_custom_command should have documentation"

        assert "custom_mkfs_command" in docstring, (
            "Documentation should mention 'custom_mkfs_command' parameter"
        )


class TestCustomMkfsCommandMCPTools:
    """Test that MCP tool schemas include custom_mkfs_command."""

    def test_fstests_vm_boot_and_run_schema_has_custom_mkfs_command(self):
        """Verify fstests_vm_boot_and_run tool schema has custom_mkfs_command."""
        from kerneldev_mcp import server

        source = inspect.getsource(server)

        # Find the fstests_vm_boot_and_run tool definition
        lines = source.split("\n")
        tool_start = None
        tool_end = None

        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_and_run"' in line:
                tool_start = i
            elif tool_start and 'name="fstests_vm_boot_custom"' in line:
                tool_end = i
                break

        assert tool_start is not None, "Could not find fstests_vm_boot_and_run tool"

        # Get the tool definition section
        tool_def = "\n".join(
            lines[tool_start:tool_end] if tool_end else lines[tool_start : tool_start + 200]
        )

        assert '"custom_mkfs_command"' in tool_def or "'custom_mkfs_command'" in tool_def, (
            "fstests_vm_boot_and_run schema should include 'custom_mkfs_command' property"
        )

        # Check description mentions key info
        assert "mkfs" in tool_def.lower(), "Schema should describe custom_mkfs_command usage"

    def test_fstests_vm_boot_custom_schema_has_custom_mkfs_command(self):
        """Verify fstests_vm_boot_custom tool schema has custom_mkfs_command."""
        from kerneldev_mcp import server

        source = inspect.getsource(server)

        # Find the fstests_vm_boot_custom tool definition
        lines = source.split("\n")
        tool_start = None
        tool_end = None

        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_custom"' in line:
                tool_start = i
            elif tool_start and 'name="fstests_groups_list"' in line:
                tool_end = i
                break

        assert tool_start is not None, "Could not find fstests_vm_boot_custom tool"

        # Get the tool definition section
        tool_def = "\n".join(
            lines[tool_start:tool_end] if tool_end else lines[tool_start : tool_start + 200]
        )

        assert '"custom_mkfs_command"' in tool_def or "'custom_mkfs_command'" in tool_def, (
            "fstests_vm_boot_custom schema should include 'custom_mkfs_command' property"
        )


class TestCustomMkfsCommandHandler:
    """Test that handlers properly pass custom_mkfs_command."""

    def test_fstests_vm_boot_and_run_handler_reads_custom_mkfs_command(self):
        """Verify handler reads custom_mkfs_command from arguments."""
        from kerneldev_mcp import server

        source = inspect.getsource(server.call_tool)

        # Find the fstests_vm_boot_and_run handler section
        lines = source.split("\n")
        handler_start = None
        handler_end = None

        for i, line in enumerate(lines):
            if "fstests_vm_boot_and_run" in line and "elif name" in line:
                handler_start = i
            elif handler_start and "elif name ==" in line and "fstests_vm_boot_and_run" not in line:
                handler_end = i
                break

        assert handler_start is not None, "Could not find fstests_vm_boot_and_run handler"

        handler_code = "\n".join(
            lines[handler_start:handler_end]
            if handler_end
            else lines[handler_start : handler_start + 150]
        )

        # Check that custom_mkfs_command is read from arguments
        assert "custom_mkfs_command" in handler_code, (
            "Handler should read custom_mkfs_command from arguments"
        )

        # Check it's passed to boot_with_fstests
        assert (
            "custom_mkfs_command=custom_mkfs_command" in handler_code
            or "custom_mkfs_command=arguments" in handler_code
        ), "Handler should pass custom_mkfs_command to boot_with_fstests"

    def test_fstests_vm_boot_custom_handler_reads_custom_mkfs_command(self):
        """Verify fstests_vm_boot_custom handler reads custom_mkfs_command."""
        from kerneldev_mcp import server

        source = inspect.getsource(server.call_tool)

        # Find the fstests_vm_boot_custom handler section
        lines = source.split("\n")
        handler_start = None
        handler_end = None

        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
            elif handler_start and "elif name ==" in line and "fstests_vm_boot_custom" not in line:
                handler_end = i
                break

        assert handler_start is not None, "Could not find fstests_vm_boot_custom handler"

        handler_code = "\n".join(
            lines[handler_start:handler_end]
            if handler_end
            else lines[handler_start : handler_start + 150]
        )

        # Check that custom_mkfs_command is read from arguments
        assert "custom_mkfs_command" in handler_code, (
            "Handler should read custom_mkfs_command from arguments"
        )


class TestCustomMkfsCommandScriptGeneration:
    """Test that script generation correctly handles custom_mkfs_command."""

    def test_script_uses_custom_mkfs_command_when_provided(self):
        """Verify script generation uses custom command when provided."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Generate script with custom mkfs command
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="bcachefs",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.bcachefs",
            )

            # Check that custom command is in the script
            assert "mkfs.bcachefs" in script, "Script should include the custom mkfs command"

            # Should not have the case statement for known filesystems
            assert "case" not in script or "mkfs.bcachefs" in script, (
                "Script with custom command should use it directly"
            )

    def test_script_appends_test_dev_to_custom_command(self):
        """Verify $TEST_DEV is appended if not in custom command."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Custom command without $TEST_DEV
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="nilfs2",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.nilfs2 -L test",
            )

            # Should have the command with $TEST_DEV appended
            assert "mkfs.nilfs2 -L test $TEST_DEV" in script, (
                "Script should append $TEST_DEV to custom command"
            )

    def test_script_preserves_test_dev_in_custom_command(self):
        """Verify $TEST_DEV is not duplicated if already in custom command."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Custom command with $TEST_DEV already included
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="custom",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.myfs -f $TEST_DEV -o special",
            )

            # Should not have duplicated $TEST_DEV
            assert "mkfs.myfs -f $TEST_DEV -o special" in script, (
                "Script should preserve custom command with $TEST_DEV as-is"
            )

            # Count occurrences of $TEST_DEV in the mkfs line
            mkfs_lines = [line for line in script.split("\n") if "mkfs.myfs" in line]
            assert len(mkfs_lines) > 0, "Should have mkfs.myfs line"

    def test_script_uses_case_statement_without_custom_command(self):
        """Verify script uses case statement when no custom command provided."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Generate script without custom command
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="ext4",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command=None,
            )

            # Should have case statement for known filesystems
            assert "case" in script, "Script without custom command should use case statement"

            assert "mkfs.ext4" in script, "Script should include mkfs.ext4 for ext4 fstype"

    def test_unknown_fstype_without_custom_command_defaults_to_ext4(self):
        """Verify unknown fstype without custom command defaults to ext4."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Generate script with unknown fstype and no custom command
            # This should log a warning and default to ext4
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="unknownfs",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command=None,
            )

            # Should use case statement (defaults to ext4)
            assert "case" in script, (
                "Unknown fstype without custom command should use case statement"
            )

    def test_unknown_fstype_with_custom_command_uses_custom(self):
        """Verify unknown fstype with custom command uses the custom command."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Generate script with unknown fstype but custom command provided
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="unknownfs",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.unknownfs",
            )

            # Should use custom command, not case statement
            assert "mkfs.unknownfs" in script, (
                "Script should use custom mkfs command for unknown fstype"
            )


class TestCustomMkfsCommandErrorMessages:
    """Test error messages related to custom_mkfs_command."""

    def test_script_error_message_suggests_custom_mkfs_command(self):
        """Verify error message for unsupported fstype mentions custom_mkfs_command."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Generate script without custom command (will use case statement)
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="ext4",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command=None,
            )

            # The * case in the case statement should mention custom_mkfs_command
            assert "custom_mkfs_command" in script, (
                "Error message in script should suggest using custom_mkfs_command parameter"
            )


class TestCustomMkfsCommandEdgeCases:
    """Test edge cases for custom_mkfs_command."""

    def test_empty_string_custom_mkfs_command_uses_case_statement(self):
        """Verify empty string custom_mkfs_command falls through to case statement."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Empty string should be falsy, so case statement used
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="ext4",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="",
            )

            # Should use case statement (empty string is falsy)
            assert "case" in script, (
                "Empty custom_mkfs_command should fall through to case statement"
            )

    def test_whitespace_only_custom_mkfs_command(self):
        """Verify whitespace-only custom_mkfs_command is handled."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Whitespace-only is truthy but problematic
            # The script will contain the whitespace command, which will fail
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="custom",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="   ",
            )

            # Should use the whitespace command (truthy), not case statement
            assert "case" not in script or "   " in script, (
                "Whitespace custom_mkfs_command should be used (truthy string)"
            )

    def test_custom_mkfs_with_known_fstype_logs_info(self):
        """Verify using custom_mkfs_command with known fstype is allowed."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Using custom command for ext4 should work (overrides built-in)
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="ext4",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.ext4 -O metadata_csum",
            )

            # Should use the custom command, not the built-in
            assert "mkfs.ext4 -O metadata_csum" in script, (
                "Custom command should override built-in for known fstype"
            )
            # Should NOT have the case statement
            assert "case" not in script, "Custom command should bypass case statement"

    def test_custom_mkfs_command_with_shell_special_chars(self):
        """Verify custom_mkfs_command with shell special characters."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Command with options containing special shell characters
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="custom",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.myfs --label='test fs'",
            )

            # Should preserve the command as-is
            assert "mkfs.myfs --label='test fs'" in script, (
                "Custom command with shell special chars should be preserved"
            )


class TestCustomMkfsCommandIntegration:
    """Integration tests for custom_mkfs_command with different filesystem types."""

    @pytest.mark.parametrize(
        "fstype,mkfs_cmd",
        [
            ("bcachefs", "mkfs.bcachefs"),
            ("nilfs2", "mkfs.nilfs2"),
            ("reiserfs", "mkfs.reiserfs"),
            ("jfs", "mkfs.jfs -q"),
            ("minix", "mkfs.minix"),
        ],
    )
    def test_various_custom_filesystems(self, fstype, mkfs_cmd):
        """Verify custom_mkfs_command works for various filesystem types."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            script = boot_mgr._generate_fstests_device_setup_script(
                fstype=fstype,
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command=mkfs_cmd,
            )

            assert mkfs_cmd in script, (
                f"Script should include custom mkfs command '{mkfs_cmd}' for {fstype}"
            )

    def test_custom_command_with_braces_syntax(self):
        """Verify custom command with ${TEST_DEV} braces syntax works."""
        from kerneldev_mcp.boot_manager import BootManager

        with tempfile.TemporaryDirectory() as tmpdir:
            boot_mgr = BootManager(Path(tmpdir))

            # Use braces syntax for variable
            script = boot_mgr._generate_fstests_device_setup_script(
                fstype="custom",
                io_scheduler="mq-deadline",
                fstests_path="/path/to/fstests",
                custom_mkfs_command="mkfs.myfs ${TEST_DEV}",
            )

            # Should preserve the braces syntax
            assert "mkfs.myfs ${TEST_DEV}" in script, (
                "Script should preserve ${TEST_DEV} braces syntax"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
