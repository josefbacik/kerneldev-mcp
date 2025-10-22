#!/usr/bin/env python3
"""
Test building actual kernel in ~/linux.
This test will attempt a real kernel build.
"""
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.build_manager import KernelBuilder, format_build_errors
from kerneldev_mcp.config_manager import ConfigManager


def test_check_requirements():
    """Test checking build requirements."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return False

    print("=" * 60)
    print("Checking Build Requirements")
    print("=" * 60)
    print()

    builder = KernelBuilder(kernel_path)

    # Get version
    version = builder.get_kernel_version()
    print(f"Kernel version: {version}")

    # Check config
    has_config = builder.check_config()
    print(f"Has .config: {has_config}")

    if not has_config:
        print("\nNo .config found. Creating minimal config...")
        manager = ConfigManager(kernel_path)
        config = manager.generate_config("virtualization", "minimal")
        manager.apply_config(config, kernel_path)
        print("✓ Config applied")

    print()
    return True


def test_build_small_target():
    """Test building a small kernel target."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return

    print("=" * 60)
    print("Testing Small Target Build (init/main.o)")
    print("=" * 60)
    print()

    builder = KernelBuilder(kernel_path)

    print("Building init/main.o...")
    start = time.time()

    result = builder.build(
        jobs=4,
        target="init/main.o",
        timeout=120  # 2 minute timeout for single file
    )

    duration = time.time() - start

    print(f"\nBuild completed in {duration:.1f}s")
    print(result.summary())

    if not result.success:
        print("\nErrors:")
        for i, error in enumerate(result.errors[:5], 1):
            print(f"  {i}. {error}")

    if result.warnings:
        print(f"\nWarnings: {len(result.warnings)}")
        for i, warning in enumerate(result.warnings[:3], 1):
            print(f"  {i}. {warning}")

    print()
    return result.success


def test_build_scripts():
    """Test building kernel scripts (fast)."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return

    print("=" * 60)
    print("Testing Scripts Build (make scripts)")
    print("=" * 60)
    print()

    builder = KernelBuilder(kernel_path)

    print("Building scripts...")
    start = time.time()

    result = builder.build(
        jobs=4,
        target="scripts",
        timeout=300  # 5 minute timeout
    )

    duration = time.time() - start

    print(f"\nBuild completed in {duration:.1f}s")
    print(result.summary())

    if not result.success:
        print("\nErrors:")
        for i, error in enumerate(result.errors[:10], 1):
            print(f"  {i}. {error}")

    print()
    return result.success


def test_clean():
    """Test cleaning build artifacts."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return

    print("=" * 60)
    print("Testing Clean Operation")
    print("=" * 60)
    print()

    builder = KernelBuilder(kernel_path)

    print("Running 'make clean'...")
    success = builder.clean("clean")

    if success:
        print("✓ Clean succeeded")
    else:
        print("✗ Clean failed")

    print()
    return success


def test_error_handling():
    """Test handling of build errors."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return

    print("=" * 60)
    print("Testing Error Handling")
    print("=" * 60)
    print()

    # Try to build with a very short timeout to force failure
    builder = KernelBuilder(kernel_path)

    print("Building with 1 second timeout (should fail)...")
    result = builder.build(
        jobs=1,
        target="init/main.o",
        timeout=1  # Very short timeout
    )

    print(result.summary())

    if not result.success:
        print("✓ Correctly detected timeout/failure")
        if result.errors:
            print(f"  Captured {len(result.errors)} error(s)")
    else:
        print("⚠ Build unexpectedly succeeded")

    print()


def main():
    """Run all build tests."""
    print("\n")
    print("=" * 60)
    print("REAL KERNEL BUILD TESTS")
    print("=" * 60)
    print("\n")

    # Check if kernel exists
    kernel_path = Path.home() / "linux"
    if not kernel_path.exists():
        print(f"❌ Kernel not found at {kernel_path}")
        print("   Please ensure Linux kernel source is at ~/linux")
        return 1

    try:
        # Check requirements
        if not test_check_requirements():
            return 1

        # Test scripts build (fast)
        test_build_scripts()

        # Test small target
        test_build_small_target()

        # Test error handling
        test_error_handling()

        # Note: We don't clean at the end to preserve build state

        print("=" * 60)
        print("✓ All real kernel build tests completed!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
