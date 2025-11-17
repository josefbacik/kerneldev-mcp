"""
Tests for build management.
"""

import pytest
from pathlib import Path
from kerneldev_mcp.build_manager import (
    BuildError,
    BuildResult,
    BuildOutputParser,
    KernelBuilder,
    format_build_errors,
)


def test_build_error_str():
    """Test BuildError string representation."""
    error = BuildError(
        file="drivers/test.c", line=42, column=10, error_type="error", message="syntax error"
    )
    assert str(error) == "drivers/test.c:42:10: error: syntax error"


def test_build_result_summary():
    """Test BuildResult summary."""
    # Successful build
    result = BuildResult(
        success=True,
        duration=120.5,
        warnings=[BuildError("test.c", 1, 1, "warning", "unused variable")],
    )
    summary = result.summary()
    assert "✓ Build succeeded" in summary
    assert "120.5s" in summary
    assert "1 warnings" in summary

    # Failed build
    result = BuildResult(
        success=False,
        duration=30.0,
        errors=[BuildError("test.c", 1, 1, "error", "undefined reference")],
        warnings=[],
    )
    summary = result.summary()
    assert "✗ Build failed" in summary
    assert "1 errors" in summary


def test_parse_gcc_error():
    """Test parsing GCC error format."""
    line = "drivers/net/ethernet/intel/e1000e/netdev.c:3456:12: error: 'foo' undeclared"

    error = BuildOutputParser._parse_line(line)
    assert error is not None
    assert error.file == "drivers/net/ethernet/intel/e1000e/netdev.c"
    assert error.line == 3456
    assert error.column == 12
    assert error.error_type == "error"
    assert "undeclared" in error.message


def test_parse_gcc_warning():
    """Test parsing GCC warning format."""
    line = "fs/btrfs/inode.c:1234:5: warning: unused variable 'ret'"

    error = BuildOutputParser._parse_line(line)
    assert error is not None
    assert error.file == "fs/btrfs/inode.c"
    assert error.line == 1234
    assert error.error_type == "warning"


def test_parse_linker_error():
    """Test parsing linker error format."""
    line = "init/main.o:123: undefined reference to `some_function'"

    error = BuildOutputParser._parse_line(line)
    assert error is not None
    assert "undefined reference" in error.message


def test_parse_output():
    """Test parsing build output with multiple errors."""
    output = """
CC      drivers/net/test.o
drivers/net/test.c:10:5: error: 'foo' undeclared
drivers/net/test.c:20:10: warning: unused variable 'bar'
LD      drivers/net/test.ko
make[2]: *** [drivers/net/test.o] Error 1
"""

    errors, warnings = BuildOutputParser.parse_output(output)

    # Should find the error
    assert len(errors) >= 1
    assert any("undeclared" in e.message for e in errors)

    # Should find the warning
    assert len(warnings) >= 1
    assert any("unused variable" in w.message for w in warnings)


def test_format_build_errors():
    """Test formatting build errors."""
    result = BuildResult(
        success=False,
        duration=45.2,
        errors=[
            BuildError("test.c", 10, 5, "error", "syntax error"),
            BuildError("test.c", 20, 3, "error", "type mismatch"),
        ],
        warnings=[
            BuildError("test.c", 5, 1, "warning", "unused variable"),
        ],
    )

    formatted = format_build_errors(result, max_errors=10)

    assert "✗ Build failed" in formatted
    assert "2)" in formatted  # Second error
    assert "syntax error" in formatted
    assert "unused variable" in formatted


def test_kernel_builder_initialization():
    """Test KernelBuilder initialization."""
    # Should fail with non-existent path
    with pytest.raises(ValueError):
        KernelBuilder(Path("/nonexistent/path"))


def test_kernel_builder_check_config(tmp_path):
    """Test checking for kernel configuration."""
    # Create a fake kernel directory
    kernel_dir = tmp_path / "linux"
    kernel_dir.mkdir()

    builder = KernelBuilder(kernel_dir)

    # No config initially
    assert not builder.check_config()

    # Create .config
    (kernel_dir / ".config").write_text("CONFIG_FOO=y\n")

    # Should detect config now
    assert builder.check_config()


def test_format_build_errors_shows_raw_output_on_parse_failure():
    """Test that raw build output is shown when error parsing fails.

    This is a regression test for the issue where build failures with
    unparseable error formats would show "✗ Build failed (0 errors)"
    without any useful diagnostic information.
    """
    # Simulate a build failure with output that doesn't match our error patterns
    # For example, a Python script error during the build process
    raw_output = """
make[1]: Entering directory '/home/user/linux'
  CALL    scripts/checksyscalls.sh
  DESCEND objtool
Traceback (most recent call last):
  File "scripts/generate-config.py", line 15, in <module>
    import nonexistent_module
ModuleNotFoundError: No module named 'nonexistent_module'
make[1]: *** [Makefile:1234: prepare] Error 1
make: *** [Makefile:456: __sub-make] Error 2
"""

    result = BuildResult(
        success=False,
        duration=5.2,
        errors=[],  # No errors parsed
        warnings=[],
        output=raw_output,
        exit_code=2,
    )

    formatted = format_build_errors(result, max_errors=10)

    # Should show the failure summary
    assert "✗ Build failed" in formatted

    # Should show raw output since no errors were parsed
    assert "Build output (last 100 lines):" in formatted
    assert "Note: Error format not recognized by parser" in formatted
    assert "ModuleNotFoundError" in formatted
    assert "nonexistent_module" in formatted

    # Verify the actual error message is visible
    assert "Error 1" in formatted or "Error 2" in formatted


def test_format_build_errors_no_raw_output_when_errors_parsed():
    """Test that raw output is NOT shown when errors are successfully parsed."""
    raw_output = """
CC      drivers/test.c
drivers/test.c:10:5: error: 'foo' undeclared
make[1]: *** [drivers/test.o] Error 1
"""

    errors = [BuildError("drivers/test.c", 10, 5, "error", "'foo' undeclared")]

    result = BuildResult(
        success=False,
        duration=3.0,
        errors=errors,
        warnings=[],
        output=raw_output,
        exit_code=1,
    )

    formatted = format_build_errors(result, max_errors=10)

    # Should show the parsed error
    assert "✗ Build failed" in formatted
    assert "'foo' undeclared" in formatted

    # Should NOT show raw output since we parsed errors successfully
    assert "Build output (last 100 lines):" not in formatted
