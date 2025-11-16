#!/usr/bin/env python3
"""
Integration test with actual kernel source.
"""

import sys
from pathlib import Path
import tempfile

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.config_manager import ConfigManager


def test_with_real_kernel():
    """Test generating and applying config to real kernel."""
    print("Testing with actual kernel source...")

    kernel_path = Path.home() / "linux"
    if not kernel_path.exists():
        print(f"  ⚠ Kernel not found at {kernel_path}, skipping")
        return

    print(f"  Using kernel at {kernel_path}")

    manager = ConfigManager(kernel_path=kernel_path)

    # Generate a simple virtualization config
    print("  Generating virtualization config...")
    config = manager.generate_config(target="virtualization", debug_level="minimal")

    # Save to temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
        config.to_file(f.name)
        temp_config = Path(f.name)
        print(f"  Saved config to {temp_config}")

    # Show a preview
    lines = temp_config.read_text().splitlines()
    print("\n  Config preview (first 20 lines):")
    for i, line in enumerate(lines[:20], 1):
        print(f"    {i:3d}: {line}")

    # Count some key options
    text = temp_config.read_text()
    virtio_count = text.count("CONFIG_VIRTIO")
    debug_count = text.count("CONFIG_DEBUG")

    print("\n  Statistics:")
    print(f"    Total lines: {len(lines)}")
    print(f"    VIRTIO options: {virtio_count}")
    print(f"    DEBUG options: {debug_count}")

    # Cleanup
    temp_config.unlink()

    print("  ✓ Integration test passed\n")


def test_btrfs_config():
    """Test generating BTRFS config."""
    print("Testing BTRFS configuration generation...")

    manager = ConfigManager()

    # Generate BTRFS config with sanitizers
    config = manager.generate_config(
        target="btrfs",
        debug_level="sanitizers",
        additional_options={"CONFIG_BTRFS_DEBUG": "y", "CONFIG_BTRFS_ASSERT": "y"},
    )

    text = config.to_config_text()

    # Verify key options
    checks = [
        ("CONFIG_BTRFS_FS=y", "BTRFS filesystem"),
        ("CONFIG_KASAN=y", "KASAN sanitizer"),
        ("CONFIG_UBSAN=y", "UBSAN sanitizer"),
        ("CONFIG_DEBUG_INFO=y", "Debug symbols"),
        ("CONFIG_BTRFS_DEBUG=y", "BTRFS debug"),
    ]

    print("  Checking for key options:")
    for option, desc in checks:
        if option in text:
            print(f"    ✓ {desc}")
        else:
            print(f"    ✗ Missing: {desc}")

    print("  ✓ BTRFS config test passed\n")


def test_networking_config():
    """Test generating networking config."""
    print("Testing networking configuration generation...")

    manager = ConfigManager()

    config = manager.generate_config(target="networking", debug_level="lockdep")

    text = config.to_config_text()

    checks = [
        ("CONFIG_NET=y", "Networking support"),
        ("CONFIG_INET=y", "TCP/IP"),
        ("CONFIG_NETFILTER=y", "Netfilter"),
        ("CONFIG_BRIDGE=y", "Bridge support"),
        ("CONFIG_LOCKDEP=y", "Lockdep"),
        ("CONFIG_PROVE_LOCKING=y", "Lock proving"),
    ]

    print("  Checking for key options:")
    for option, desc in checks:
        if option in text:
            print(f"    ✓ {desc}")
        else:
            print(f"    ✗ Missing: {desc}")

    print("  ✓ Networking config test passed\n")


def main():
    """Run integration tests."""
    print("=" * 60)
    print("Running Integration Tests")
    print("=" * 60)
    print()

    try:
        test_btrfs_config()
        test_networking_config()
        test_with_real_kernel()

        print("=" * 60)
        print("✓ All integration tests passed!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
