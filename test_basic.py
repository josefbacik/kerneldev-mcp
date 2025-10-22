#!/usr/bin/env python3
"""
Basic functionality test without pytest dependency.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from kerneldev_mcp.templates import TemplateManager
from kerneldev_mcp.config_manager import ConfigManager, KernelConfig


def test_template_manager():
    """Test template manager basic functionality."""
    print("Testing TemplateManager...")

    manager = TemplateManager()

    # Test listing presets
    presets = manager.list_presets()
    print(f"  Found {len(presets)} presets")
    assert len(presets) > 0, "Should have presets"

    # Test getting targets
    targets = manager.get_targets()
    print(f"  Targets: {', '.join(targets)}")
    assert "networking" in targets
    assert "btrfs" in targets

    # Test getting debug levels
    debug_levels = manager.get_debug_levels()
    print(f"  Debug levels: {', '.join(debug_levels)}")
    assert "basic" in debug_levels
    assert "sanitizers" in debug_levels

    # Test loading a template
    template = manager.get_target_template("networking")
    assert template is not None
    content = template.load()
    assert "CONFIG_NET=y" in content
    print("  ✓ Successfully loaded networking template")

    print("✓ TemplateManager tests passed\n")


def test_config_manager():
    """Test config manager basic functionality."""
    print("Testing ConfigManager...")

    manager = ConfigManager()

    # Test generating a simple config
    config = manager.generate_config(
        target="networking",
        debug_level="basic"
    )
    assert config is not None
    text = config.to_config_text()
    assert "CONFIG_NET=y" in text
    assert "CONFIG_DEBUG_KERNEL=y" in text
    print("  ✓ Generated networking config with basic debug")

    # Test generating with fragments
    config = manager.generate_config(
        target="btrfs",
        debug_level="minimal",
        fragments=["kasan"]
    )
    text = config.to_config_text()
    assert "CONFIG_BTRFS_FS=y" in text
    assert "CONFIG_KASAN=y" in text
    print("  ✓ Generated btrfs config with KASAN fragment")

    # Test generating with additional options
    config = manager.generate_config(
        target="boot",
        debug_level="basic",
        additional_options={
            "CONFIG_CUSTOM": "y"
        }
    )
    assert config.get_option("CONFIG_CUSTOM").value == "y"
    print("  ✓ Generated config with additional options")

    print("✓ ConfigManager tests passed\n")


def test_kernel_config():
    """Test KernelConfig class."""
    print("Testing KernelConfig...")

    # Test creating and setting options
    config = KernelConfig()
    config.set_option("CONFIG_NET", "y")
    config.set_option("CONFIG_DEBUG", None)

    text = config.to_config_text()
    assert "CONFIG_NET=y" in text
    assert "# CONFIG_DEBUG is not set" in text
    print("  ✓ Config option setting works")

    # Test parsing
    config2 = KernelConfig.from_config_text(text)
    assert config2.get_option("CONFIG_NET").value == "y"
    assert config2.get_option("CONFIG_DEBUG").value is None
    print("  ✓ Config parsing works")

    # Test merging
    config3 = KernelConfig()
    config3.set_option("CONFIG_KASAN", "y")
    config.merge(config3)
    assert config.get_option("CONFIG_KASAN").value == "y"
    print("  ✓ Config merging works")

    print("✓ KernelConfig tests passed\n")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Running Basic Functionality Tests")
    print("=" * 60)
    print()

    try:
        test_template_manager()
        test_kernel_config()
        test_config_manager()

        print("=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
