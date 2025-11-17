"""
Unit tests for extra_args parameter support in virtme-ng commands.

Tests ensure that:
1. extra_args parameter is properly defined in all virtme-ng methods
2. MCP tool schemas include extra_args
3. Documentation mentions extra_args
4. Parameters are properly passed through the call chain
"""

import inspect
import pytest


class TestBootTestExtraArgs:
    """Test extra_args support in boot_test (boot_kernel_test MCP tool)."""

    def test_boot_test_has_extra_args_parameter(self):
        """Verify boot_test method has extra_args parameter."""
        from kerneldev_mcp.boot_manager import BootManager

        # Get method signature
        sig = inspect.signature(BootManager.boot_test)

        # Check extra_args parameter exists
        assert "extra_args" in sig.parameters, "boot_test must have 'extra_args' parameter"

        # Check it has a default value (should be None or [])
        param = sig.parameters["extra_args"]
        assert param.default is not inspect.Parameter.empty, (
            "extra_args parameter should have a default value"
        )

    def test_boot_kernel_test_mcp_tool_has_extra_args(self):
        """Verify boot_kernel_test MCP tool schema includes extra_args."""
        from kerneldev_mcp import server

        source = inspect.getsource(server)

        # Find the boot_kernel_test tool definition
        assert 'name="boot_kernel_test"' in source, "boot_kernel_test tool should be defined"

        lines = source.split("\n")
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="boot_kernel_test"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find boot_kernel_test tool definition"

        # Get tool schema (next ~100 lines)
        tool_def = "\n".join(lines[tool_start : tool_start + 100])

        # Check that extra_args is in the schema
        assert '"extra_args"' in tool_def or "'extra_args'" in tool_def, (
            "Tool schema should include 'extra_args' property"
        )

        # Check description mentions vng or virtme
        tool_def_lower = tool_def.lower()
        assert "vng" in tool_def_lower or "virtme" in tool_def_lower, (
            "extra_args description should mention it's for vng/virtme-ng arguments"
        )


class TestBootWithFstestsExtraArgs:
    """Test extra_args support in boot_with_fstests."""

    def test_boot_with_fstests_has_extra_args_parameter(self):
        """Verify boot_with_fstests method has extra_args parameter."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager.boot_with_fstests)

        # Check extra_args parameter exists
        assert "extra_args" in sig.parameters, "boot_with_fstests must have 'extra_args' parameter"

        # Check it has a default value
        param = sig.parameters["extra_args"]
        assert param.default is not inspect.Parameter.empty, (
            "extra_args parameter should have a default value"
        )

    def test_boot_with_fstests_docstring_mentions_extra_args(self):
        """Verify boot_with_fstests documentation mentions extra_args."""
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_fstests.__doc__
        assert docstring is not None, "boot_with_fstests should have documentation"

        # Check that extra_args is documented
        assert "extra_args" in docstring.lower(), (
            "Documentation should mention 'extra_args' parameter"
        )

    def test_fstests_vm_boot_and_run_has_extra_args_in_schema(self):
        """Verify fstests_vm_boot_and_run MCP tool has extra_args in schema."""
        from kerneldev_mcp import server

        source = inspect.getsource(server)

        # Find the fstests_vm_boot_and_run tool definition
        assert 'name="fstests_vm_boot_and_run"' in source, (
            "fstests_vm_boot_and_run tool should be defined"
        )

        lines = source.split("\n")
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_and_run"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find fstests_vm_boot_and_run tool definition"

        # Get tool schema (next ~150 lines to cover full schema)
        tool_def = "\n".join(lines[tool_start : tool_start + 150])

        # Check that extra_args is in the schema
        assert '"extra_args"' in tool_def or "'extra_args'" in tool_def, (
            "Tool schema should include 'extra_args' property"
        )

        # Check type is array
        assert '"type": "array"' in tool_def or "'type': 'array'" in tool_def, (
            "extra_args should be of type 'array'"
        )

    def test_boot_with_fstests_passes_extra_args_to_vng(self):
        """Verify boot_with_fstests passes extra_args to vng command."""
        from kerneldev_mcp.boot_manager import BootManager

        # Get source code
        source = inspect.getsource(BootManager.boot_with_fstests)

        # Check that extra_args is extended to cmd
        assert "extra_args" in source, "Method should reference extra_args"
        assert "cmd.extend(extra_args)" in source or "cmd.extend( extra_args )" in source, (
            "Method should extend extra_args to vng command"
        )


class TestBootWithCustomCommandExtraArgs:
    """Test extra_args support in boot_with_custom_command."""

    def test_boot_with_custom_command_has_extra_args_parameter(self):
        """Verify boot_with_custom_command method has extra_args parameter."""
        from kerneldev_mcp.boot_manager import BootManager

        sig = inspect.signature(BootManager.boot_with_custom_command)

        # Check extra_args parameter exists
        assert "extra_args" in sig.parameters, (
            "boot_with_custom_command must have 'extra_args' parameter"
        )

        # Check it has a default value
        param = sig.parameters["extra_args"]
        assert param.default is not inspect.Parameter.empty, (
            "extra_args parameter should have a default value"
        )

    def test_boot_with_custom_command_docstring_mentions_extra_args(self):
        """Verify boot_with_custom_command documentation mentions extra_args."""
        from kerneldev_mcp.boot_manager import BootManager

        docstring = BootManager.boot_with_custom_command.__doc__
        assert docstring is not None, "boot_with_custom_command should have documentation"

        # Check that extra_args is documented
        assert "extra_args" in docstring.lower(), (
            "Documentation should mention 'extra_args' parameter"
        )

    def test_fstests_vm_boot_custom_has_extra_args_in_schema(self):
        """Verify fstests_vm_boot_custom MCP tool has extra_args in schema."""
        from kerneldev_mcp import server

        source = inspect.getsource(server)

        # Find the fstests_vm_boot_custom tool definition
        assert 'name="fstests_vm_boot_custom"' in source, (
            "fstests_vm_boot_custom tool should be defined"
        )

        lines = source.split("\n")
        tool_start = None
        for i, line in enumerate(lines):
            if 'name="fstests_vm_boot_custom"' in line:
                tool_start = i
                break

        assert tool_start is not None, "Could not find fstests_vm_boot_custom tool definition"

        # Get tool schema (next ~150 lines to cover full schema)
        tool_def = "\n".join(lines[tool_start : tool_start + 150])

        # Check that extra_args is in the schema
        assert '"extra_args"' in tool_def or "'extra_args'" in tool_def, (
            "Tool schema should include 'extra_args' property"
        )

        # Check type is array
        assert '"type": "array"' in tool_def or "'type': 'array'" in tool_def, (
            "extra_args should be of type 'array'"
        )

    def test_boot_with_custom_command_passes_extra_args_to_vng(self):
        """Verify boot_with_custom_command passes extra_args to vng command."""
        from kerneldev_mcp.boot_manager import BootManager

        # Get source code
        source = inspect.getsource(BootManager.boot_with_custom_command)

        # Check that extra_args is extended to cmd
        assert "extra_args" in source, "Method should reference extra_args"
        assert "cmd.extend(extra_args)" in source or "cmd.extend( extra_args )" in source, (
            "Method should extend extra_args to vng command"
        )


class TestExtraArgsHandlerIntegration:
    """Test that MCP handlers properly extract and pass extra_args."""

    def test_fstests_vm_boot_and_run_handler_extracts_extra_args(self):
        """Verify fstests_vm_boot_and_run handler extracts extra_args from arguments."""
        from kerneldev_mcp import server
        import inspect

        # Get source code of call_tool function
        source = inspect.getsource(server.call_tool)

        # Find the fstests_vm_boot_and_run handler section
        lines = source.split("\n")
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_and_run" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find fstests_vm_boot_and_run handler"

        # Get handler code (next ~100 lines)
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Check that handler extracts extra_args
        assert "extra_args" in handler_code, "Handler should extract extra_args from arguments"
        assert 'arguments.get("extra_args"' in handler_code, (
            "Handler should use arguments.get() to extract extra_args"
        )

        # Check that it passes extra_args to boot_with_fstests
        # Look for the boot_with_fstests call (might be 20-50 lines after handler start)
        call_section = "\n".join(lines[handler_start : handler_start + 150])
        assert "boot_with_fstests" in call_section, "Handler should call boot_with_fstests"
        assert "extra_args=extra_args" in call_section, (
            "Handler should pass extra_args to boot_with_fstests"
        )

    def test_fstests_vm_boot_custom_handler_extracts_extra_args(self):
        """Verify fstests_vm_boot_custom handler extracts extra_args from arguments."""
        from kerneldev_mcp import server
        import inspect

        # Get source code of call_tool function
        source = inspect.getsource(server.call_tool)

        # Find the fstests_vm_boot_custom handler section
        lines = source.split("\n")
        handler_start = None
        for i, line in enumerate(lines):
            if "fstests_vm_boot_custom" in line and "elif name" in line:
                handler_start = i
                break

        assert handler_start is not None, "Could not find fstests_vm_boot_custom handler"

        # Get handler code (next ~100 lines)
        handler_code = "\n".join(lines[handler_start : handler_start + 100])

        # Check that handler extracts extra_args
        assert "extra_args" in handler_code, "Handler should extract extra_args from arguments"
        assert 'arguments.get("extra_args"' in handler_code, (
            "Handler should use arguments.get() to extract extra_args"
        )

        # Check that it passes extra_args to boot_with_custom_command
        call_section = "\n".join(lines[handler_start : handler_start + 150])
        assert "boot_with_custom_command" in call_section, (
            "Handler should call boot_with_custom_command"
        )
        assert "extra_args=extra_args" in call_section, (
            "Handler should pass extra_args to boot_with_custom_command"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
