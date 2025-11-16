#!/usr/bin/env python3
"""
Complete workflow test: config generation, application, and building.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.config_manager import ConfigManager
from kerneldev_mcp.build_manager import KernelBuilder


def test_complete_workflow():
    """Test complete workflow from config to build."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping")
        return False

    print("=" * 60)
    print("COMPLETE WORKFLOW TEST")
    print("=" * 60)
    print()

    # Step 1: Generate configuration
    print("Step 1: Generating configuration...")
    manager = ConfigManager(kernel_path)
    config = manager.generate_config(
        target="virtualization", debug_level="minimal", fragments=["virtme"]
    )
    print(f"  ✓ Generated config with {len(config.options)} options")

    # Step 2: Apply configuration
    print("\nStep 2: Applying configuration...")
    success = manager.apply_config(config, kernel_path)
    if success:
        print("  ✓ Configuration applied and validated")
    else:
        print("  ⚠ Configuration applied with warnings")

    # Step 3: Check build requirements
    print("\nStep 3: Checking build requirements...")
    builder = KernelBuilder(kernel_path)

    version = builder.get_kernel_version()
    print(f"  Kernel version: {version}")

    has_config = builder.check_config()
    print(f"  Has .config: {has_config}")

    # Step 4: Build scripts (fast test)
    print("\nStep 4: Building kernel scripts...")
    result = builder.build(jobs=4, target="scripts", timeout=300)

    print(f"  {result.summary()}")

    if not result.success:
        print("\n  Errors:")
        for i, error in enumerate(result.errors[:5], 1):
            print(f"    {i}. {error}")
        return False

    # Step 5: Build a small target
    print("\nStep 5: Building init/main.o...")
    result = builder.build(jobs=4, target="init/main.o", timeout=120)

    print(f"  {result.summary()}")

    if not result.success:
        print("\n  Errors:")
        for i, error in enumerate(result.errors[:5], 1):
            print(f"    {i}. {error}")
        return False

    print("\n" + "=" * 60)
    print("✓ COMPLETE WORKFLOW TEST PASSED")
    print("=" * 60)
    return True


def main():
    """Run the workflow test."""
    kernel_path = Path.home() / "linux"
    if not kernel_path.exists():
        print(f"❌ Kernel not found at {kernel_path}")
        print("   Please ensure Linux kernel source is at ~/linux")
        return 1

    try:
        success = test_complete_workflow()
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
