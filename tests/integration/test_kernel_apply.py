#!/usr/bin/env python3
"""
Test applying configuration to actual kernel and running olddefconfig.
"""

import sys
from pathlib import Path
import shutil

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.config_manager import ConfigManager


def test_apply_to_kernel():
    """Test applying config to real kernel."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"Kernel not found at {kernel_path}, skipping apply test")
        return

    print("=" * 60)
    print("Testing Configuration Apply to Real Kernel")
    print("=" * 60)
    print()

    # Backup existing .config if it exists
    config_path = kernel_path / ".config"
    backup_path = kernel_path / ".config.backup.mcp"

    if config_path.exists():
        print(f"Backing up existing .config to {backup_path}")
        shutil.copy2(config_path, backup_path)

    try:
        manager = ConfigManager(kernel_path=kernel_path)

        # Generate a simple virtualization config
        print("Generating virtualization config with basic debug...")
        config = manager.generate_config(target="virtualization", debug_level="basic")

        print(f"Applying config to {kernel_path}...")
        success = manager.apply_config(
            config=config, kernel_path=kernel_path, merge_with_existing=False
        )

        if success:
            print("✓ Configuration applied successfully!")
            print()

            # Show some stats
            if config_path.exists():
                lines = config_path.read_text().splitlines()
                print(f"Generated .config statistics:")
                print(f"  Total lines: {len(lines)}")

                enabled = sum(1 for line in lines if line.startswith("CONFIG_") and "=y" in line)
                modules = sum(1 for line in lines if line.startswith("CONFIG_") and "=m" in line)
                disabled = sum(1 for line in lines if "is not set" in line)

                print(f"  Built-in (y): {enabled}")
                print(f"  Modules (m): {modules}")
                print(f"  Disabled: {disabled}")
        else:
            print("⚠ Configuration applied with warnings (olddefconfig failed)")

    finally:
        # Restore backup
        if backup_path.exists():
            print(f"\nRestoring original .config from backup...")
            shutil.copy2(backup_path, config_path)
            backup_path.unlink()
            print("✓ Original .config restored")

    print()
    print("=" * 60)
    print("✓ Apply test completed")
    print("=" * 60)


if __name__ == "__main__":
    test_apply_to_kernel()
