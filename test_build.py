#!/usr/bin/env python3
"""
Test build functionality without pytest dependency.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.build_manager import (
    BuildError,
    BuildResult,
    BuildOutputParser,
    KernelBuilder,
    format_build_errors
)


def test_build_error():
    """Test BuildError class."""
    print("Testing BuildError...")

    error = BuildError(
        file="drivers/test.c",
        line=42,
        column=10,
        error_type="error",
        message="syntax error"
    )

    assert str(error) == "drivers/test.c:42:10: error: syntax error"
    print("  ✓ BuildError string representation works")

    print("✓ BuildError tests passed\n")


def test_build_result():
    """Test BuildResult class."""
    print("Testing BuildResult...")

    # Successful build
    result = BuildResult(
        success=True,
        duration=120.5,
        warnings=[BuildError("test.c", 1, 1, "warning", "unused variable")]
    )

    summary = result.summary()
    assert "✓ Build succeeded" in summary
    assert "120.5s" in summary
    print("  ✓ Successful build summary works")

    # Failed build
    result = BuildResult(
        success=False,
        duration=30.0,
        errors=[BuildError("test.c", 1, 1, "error", "undefined reference")],
    )

    summary = result.summary()
    assert "✗ Build failed" in summary
    assert "1 errors" in summary
    print("  ✓ Failed build summary works")

    print("✓ BuildResult tests passed\n")


def test_output_parser():
    """Test BuildOutputParser."""
    print("Testing BuildOutputParser...")

    # Test GCC error
    line = "drivers/net/test.c:3456:12: error: 'foo' undeclared"
    error = BuildOutputParser._parse_line(line)
    assert error is not None
    assert error.file == "drivers/net/test.c"
    assert error.line == 3456
    assert error.column == 12
    assert error.error_type == "error"
    print("  ✓ GCC error parsing works")

    # Test GCC warning
    line = "fs/btrfs/inode.c:1234:5: warning: unused variable 'ret'"
    error = BuildOutputParser._parse_line(line)
    assert error is not None
    assert error.error_type == "warning"
    print("  ✓ GCC warning parsing works")

    # Test full output
    output = """
CC      drivers/net/test.o
drivers/net/test.c:10:5: error: 'foo' undeclared
drivers/net/test.c:20:10: warning: unused variable 'bar'
LD      drivers/net/test.ko
"""
    errors, warnings = BuildOutputParser.parse_output(output)
    assert len(errors) >= 1
    assert len(warnings) >= 1
    print("  ✓ Full output parsing works")

    print("✓ BuildOutputParser tests passed\n")


def test_format_errors():
    """Test error formatting."""
    print("Testing error formatting...")

    result = BuildResult(
        success=False,
        duration=45.2,
        errors=[
            BuildError("test.c", 10, 5, "error", "syntax error"),
            BuildError("test.c", 20, 3, "error", "type mismatch"),
        ],
        warnings=[
            BuildError("test.c", 5, 1, "warning", "unused variable"),
        ]
    )

    formatted = format_build_errors(result, max_errors=10)
    assert "✗ Build failed" in formatted
    assert "syntax error" in formatted
    assert "unused variable" in formatted
    print("  ✓ Error formatting works")

    print("✓ Error formatting tests passed\n")


def test_kernel_builder():
    """Test KernelBuilder class."""
    print("Testing KernelBuilder...")

    # Test with non-existent path
    try:
        KernelBuilder(Path("/nonexistent/path"))
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  ✓ Properly rejects non-existent path")

    # Test with real kernel if available
    kernel_path = Path.home() / "linux"
    if kernel_path.exists():
        print(f"  Found kernel at {kernel_path}")
        builder = KernelBuilder(kernel_path)

        # Check version
        version = builder.get_kernel_version()
        if version:
            print(f"  ✓ Kernel version: {version}")

        # Check config
        has_config = builder.check_config()
        print(f"  ✓ Config exists: {has_config}")
    else:
        print(f"  ⚠ No kernel at {kernel_path}, skipping real tests")

    print("✓ KernelBuilder tests passed\n")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Running Build Functionality Tests")
    print("=" * 60)
    print()

    try:
        test_build_error()
        test_build_result()
        test_output_parser()
        test_format_errors()
        test_kernel_builder()

        print("=" * 60)
        print("✓ All build tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
