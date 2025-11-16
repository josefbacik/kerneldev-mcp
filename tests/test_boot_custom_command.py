"""
Unit tests for fstests_vm_boot_custom tool.

Tests ensure that:
1. boot_with_custom_command method has correct signature and parameters
2. MCP tool schema is properly defined
3. Handler correctly processes command/script_file/interactive modes
4. Method is accessible without import errors
"""

import inspect
import re
from pathlib import Path
import pytest


class TestBootCustomCommandSignature:
    """Test method signature and parameters."""

    def test_boot_with_custom_command_method_exists(self):
        """Verify boot_with_custom_command method exists in BootManager."""
        from kerneldev_mcp.boot_manager import BootManager

        assert hasattr(BootManager, "boot_with_custom_command"), (
            "BootManager should have boot_with_custom_command method"
        )

        method = getattr(BootManager, "boot_with_custom_command")
        assert callable(method), "boot_with_custom_command should be callable"

    def test_boot_with_custom_command_has_required_parameters(self):
        """Verify method has all required parameters."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager.boot_with_custom_command)
        params = sig.parameters

        # Required parameters
        required_params = ["self", "fstests_path"]
        for param in required_params:
            assert param in params, f"boot_with_custom_command must have '{param}' parameter"

        # Optional parameters with defaults
        optional_params = {
            "command": None,
            "script_file": None,
            "fstype": "ext4",
            "timeout": 300,
            "memory": "4G",
            "cpus": 4,
            "cross_compile": None,
            "force_9p": False,
            "io_scheduler": "mq-deadline",
            "use_tmpfs": False,
        }

        for param_name, expected_default in optional_params.items():
            assert param_name in params, (
                f"boot_with_custom_command should have '{param_name}' parameter"
            )

            param = params[param_name]
            if expected_default is not None:
                assert param.default == expected_default, (
                    f"'{param_name}' default should be {expected_default}, got {param.default}"
                )

    def test_boot_with_custom_command_is_async(self):
        """Verify method is async to avoid blocking event loop."""
        from kerneldev_mcp.boot_manager import BootManager
        import asyncio

        method = BootManager.boot_with_custom_command
        assert asyncio.iscoroutinefunction(method), (
            "boot_with_custom_command must be async to avoid blocking MCP event loop"
        )

    def test_boot_with_custom_command_has_documentation(self):
        """Verify method has comprehensive documentation."""
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_custom_command.__doc__
        assert docstring is not None, "boot_with_custom_command should have documentation"

        # Check key concepts are documented
        required_docs = [
            "fstests_path",
            "command",
            "script",
            "fstype",
            "device",
        ]

        docstring_lower = docstring.lower()
        for keyword in required_docs:
            assert keyword in docstring_lower, f"Documentation should mention '{keyword}'"


class TestBootCustomCommandMCPTool:
    """Test MCP tool definition and schema."""

    def test_fstests_vm_boot_custom_tool_exists(self):
        """Verify fstests_vm_boot_custom tool is defined in server."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server)
        assert 'name="fstests_vm_boot_custom"' in source, (
            "fstests_vm_boot_custom tool should be defined in server"
        )

    def test_tool_schema_has_required_fields(self):
        """Verify tool schema includes all required fields."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server)
        lines = source.split("\n")

        # Find tool definition
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_custom"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find fstests_vm_boot_custom tool definition"

        # Get tool schema (next ~100 lines)
        tool_def = "\n".join(lines[tool_start : tool_start + 100])

        # Required schema fields
        required_fields = [
            '"kernel_path"',
            '"fstests_path"',
            '"command"',
            '"script_file"',
            '"fstype"',
            '"timeout"',
            '"memory"',
            '"cpus"',
            '"io_scheduler"',
        ]

        for field in required_fields:
            assert field in tool_def, f"Tool schema should include {field} property"

    def test_tool_description_mentions_key_features(self):
        """Verify tool description is comprehensive."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server)
        lines = source.split("\n")

        # Find tool definition
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_custom"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find tool definition"

        # Get description (should be in docstring right after name)
        tool_def = "\n".join(lines[tool_start : tool_start + 50])

        # Key features that should be mentioned
        key_features = [
            "custom",
            "device",
            "command",
            "script",
        ]

        tool_def_lower = tool_def.lower()
        for feature in key_features:
            assert feature in tool_def_lower, f"Tool description should mention '{feature}'"

    def test_tool_has_required_parameters_marked(self):
        """Verify required parameters are marked in schema."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server)
        lines = source.split("\n")

        # Find tool definition
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_custom"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find tool definition"

        # Get schema (next ~150 lines to capture required field)
        tool_def = "\n".join(lines[tool_start : tool_start + 150])

        # Should have required array with kernel_path and fstests_path
        assert '"required"' in tool_def, "Tool schema should specify required parameters"

        # Both kernel_path and fstests_path should be required
        assert "kernel_path" in tool_def and "fstests_path" in tool_def, (
            "Both kernel_path and fstests_path should be in required array"
        )


class TestBootCustomCommandHandler:
    """Test MCP tool handler in call_tool function."""

    def test_handler_exists(self):
        """Verify handler for fstests_vm_boot_custom exists."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server.call_tool)
        assert "fstests_vm_boot_custom" in source, (
            "call_tool should have handler for fstests_vm_boot_custom"
        )

    def test_handler_checks_kernel_path_exists(self):
        """Verify handler validates kernel path."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server.call_tool)
        lines = source.split("\n")

        # Find handler section
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find handler"

        # Get handler code
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Should check if kernel path exists
        assert "kernel_path.exists()" in handler_code, (
            "Handler should validate that kernel_path exists"
        )

    def test_handler_checks_fstests_path_exists(self):
        """Verify handler validates fstests path."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server.call_tool)
        lines = source.split("\n")

        # Find handler section
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find handler"

        # Get handler code
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Should check if fstests path exists
        assert "fstests_path.exists()" in handler_code, (
            "Handler should validate that fstests_path exists"
        )

    def test_handler_processes_optional_command_and_script(self):
        """Verify handler properly handles command and script_file parameters."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server.call_tool)
        lines = source.split("\n")

        # Find handler section
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find handler"

        # Get handler code
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Should get command and script_file from arguments
        assert (
            'arguments.get("command")' in handler_code or "arguments.get('command')" in handler_code
        ), "Handler should retrieve command parameter"

        assert "script_file" in handler_code, "Handler should handle script_file parameter"

    def test_handler_calls_boot_with_custom_command(self):
        """Verify handler calls boot_with_custom_command method."""
        from kerneldev_mcp import server
        import inspect

        source = inspect.getsource(server.call_tool)
        lines = source.split("\n")

        # Find handler section
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find handler"

        # Get handler code
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Should call boot_with_custom_command
        assert "boot_with_custom_command" in handler_code, (
            "Handler should call boot_with_custom_command method"
        )

        # Should await the call (since it's async)
        assert "await" in handler_code, (
            "Handler should await the async boot_with_custom_command call"
        )


class TestBootCustomCommandBasicFunctionality:
    """Basic smoke tests to catch runtime errors."""

    def test_method_is_accessible_without_errors(self):
        """
        Verify method can be accessed without import or scoping errors.

        Regression test for import scoping bugs.
        """
        from kerneldev_mcp.boot_manager import BootManager
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            kernel_path = Path(tmpdir)

            # Should not raise any import-related errors
            boot_mgr = BootManager(kernel_path)

            # Verify method exists and is callable
            assert hasattr(boot_mgr, "boot_with_custom_command")
            assert callable(boot_mgr.boot_with_custom_command)

            # Get the method to ensure no NameError or scoping issues
            method = boot_mgr.boot_with_custom_command
            assert method is not None


class TestBootCustomCommandEnvironmentSetup:
    """Test that environment setup is properly implemented."""

    def test_method_sets_up_device_environment(self):
        """Verify method implementation includes device setup."""
        from kerneldev_mcp import boot_manager
        import inspect

        source = inspect.getsource(boot_manager.BootManager.boot_with_custom_command)

        # Should create loop devices
        assert "loop" in source.lower(), "Method should create loop devices for fstests environment"

        # Should export environment variables
        assert "TEST_DEV" in source or "export" in source.lower(), (
            "Method should export environment variables for custom command"
        )

    def test_method_supports_all_three_modes(self):
        """Verify method supports command, script, and interactive modes."""
        from kerneldev_mcp import boot_manager
        import inspect

        source = inspect.getsource(boot_manager.BootManager.boot_with_custom_command)

        # Should handle command parameter
        assert "if command:" in source or "elif command:" in source, (
            "Method should handle command parameter"
        )

        # Should handle script_file parameter
        assert "script_file" in source, "Method should handle script_file parameter"

        # Should have logic for interactive mode (when neither is provided)
        # This might be an else clause or a check for both being None
        assert "else:" in source or "shell" in source.lower(), (
            "Method should support interactive shell mode"
        )

    def test_method_creates_timestamped_results_directory(self):
        """Verify method creates results directory with timestamp."""
        from kerneldev_mcp import boot_manager
        import inspect

        source = inspect.getsource(boot_manager.BootManager.boot_with_custom_command)

        # Should create results directory
        assert "results" in source.lower(), "Method should create results directory"

        # Should use timestamp for uniqueness
        assert "timestamp" in source.lower() or "datetime" in source.lower(), (
            "Method should use timestamp for results directory"
        )

        # Should include 'custom' in path to differentiate from fstests runs
        assert "custom" in source, "Method should use 'custom' prefix for results directory"


class TestBootWithCustomCommandDatetimeUsage:
    """Test datetime usage consistency in boot methods."""

    def test_boot_with_custom_command_no_local_datetime_import(self):
        """Ensure boot_with_custom_command uses module-level datetime import.

        Regression test for bug where local 'from datetime import datetime'
        shadowed module-level 'import datetime', causing confusion about
        which 'datetime' to use (module vs class).
        """
        from kerneldev_mcp.boot_manager import BootManager
        import inspect

        source = inspect.getsource(BootManager.boot_with_custom_command)
        assert "from datetime import datetime" not in source, (
            "Method should use module-level 'import datetime', not local import"
        )

    def test_boot_with_fstests_no_local_datetime_import(self):
        """Ensure boot_with_fstests uses module-level datetime import.

        Regression test for bug where local 'from datetime import datetime'
        shadowed module-level 'import datetime', causing confusion about
        which 'datetime' to use (module vs class).
        """
        from kerneldev_mcp.boot_manager import BootManager
        import inspect

        source = inspect.getsource(BootManager.boot_with_fstests)
        assert "from datetime import datetime" not in source, (
            "Method should use module-level 'import datetime', not local import"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
