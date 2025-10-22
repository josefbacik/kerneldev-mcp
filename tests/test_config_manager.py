"""
Tests for configuration management.
"""
import pytest
from pathlib import Path
from kerneldev_mcp.config_manager import ConfigOption, KernelConfig, ConfigManager


def test_config_option_to_config_line():
    """Test converting ConfigOption to .config format."""
    # Enabled option
    opt = ConfigOption(name="CONFIG_NET", value="y")
    assert opt.to_config_line() == "CONFIG_NET=y"

    # Module option
    opt = ConfigOption(name="CONFIG_E1000E", value="m")
    assert opt.to_config_line() == "CONFIG_E1000E=m"

    # Disabled option
    opt = ConfigOption(name="CONFIG_DEBUG", value=None)
    assert opt.to_config_line() == "# CONFIG_DEBUG is not set"

    # String value
    opt = ConfigOption(name="CONFIG_LOCALVERSION", value="-custom")
    assert opt.to_config_line() == "CONFIG_LOCALVERSION=-custom"


def test_config_option_from_config_line():
    """Test parsing .config lines into ConfigOption."""
    # Enabled option
    opt = ConfigOption.from_config_line("CONFIG_NET=y")
    assert opt.name == "CONFIG_NET"
    assert opt.value == "y"

    # Disabled option
    opt = ConfigOption.from_config_line("# CONFIG_DEBUG is not set")
    assert opt.name == "CONFIG_DEBUG"
    assert opt.value is None

    # String value
    opt = ConfigOption.from_config_line('CONFIG_LOCALVERSION="-custom"')
    assert opt.name == "CONFIG_LOCALVERSION"
    assert opt.value == "-custom"


def test_kernel_config_set_get():
    """Test setting and getting config options."""
    config = KernelConfig()

    config.set_option("CONFIG_NET", "y")
    opt = config.get_option("CONFIG_NET")
    assert opt.value == "y"

    # Should work without CONFIG_ prefix
    config.set_option("DEBUG_KERNEL", "y")
    opt = config.get_option("DEBUG_KERNEL")
    assert opt.value == "y"


def test_kernel_config_merge():
    """Test merging configurations."""
    config1 = KernelConfig()
    config1.set_option("CONFIG_NET", "y")
    config1.set_option("CONFIG_DEBUG", "y")

    config2 = KernelConfig()
    config2.set_option("CONFIG_DEBUG", "n")  # Different value
    config2.set_option("CONFIG_KASAN", "y")  # New option

    config1.merge(config2, overwrite=True)

    # Should have all options
    assert config1.get_option("CONFIG_NET").value == "y"
    assert config1.get_option("CONFIG_DEBUG").value == "n"  # Overwritten
    assert config1.get_option("CONFIG_KASAN").value == "y"


def test_kernel_config_to_from_text():
    """Test converting config to/from text."""
    config = KernelConfig()
    config.header_comments = ["Test configuration"]
    config.set_option("CONFIG_NET", "y")
    config.set_option("CONFIG_DEBUG", None)

    text = config.to_config_text()

    # Parse it back
    config2 = KernelConfig.from_config_text(text)

    assert config2.get_option("CONFIG_NET").value == "y"
    assert config2.get_option("CONFIG_DEBUG").value is None


def test_config_manager_generate_config():
    """Test generating a complete configuration."""
    manager = ConfigManager()

    config = manager.generate_config(
        target="networking",
        debug_level="basic",
        architecture="x86_64"
    )

    # Should have networking options
    text = config.to_config_text()
    assert "CONFIG_NET=y" in text

    # Should have basic debug options
    assert "CONFIG_DEBUG_KERNEL=y" in text


def test_config_manager_generate_with_fragments():
    """Test generating config with fragments."""
    manager = ConfigManager()

    config = manager.generate_config(
        target="virtualization",
        debug_level="minimal",
        fragments=["kasan"]
    )

    text = config.to_config_text()

    # Should have virtualization options
    assert "CONFIG_VIRTIO=y" in text

    # Should have KASAN from fragment
    assert "CONFIG_KASAN=y" in text


def test_config_manager_generate_with_additional_options():
    """Test generating config with additional options."""
    manager = ConfigManager()

    config = manager.generate_config(
        target="boot",
        debug_level="basic",
        additional_options={
            "CONFIG_CUSTOM_OPTION": "y",
            "CONFIG_ANOTHER": "42"
        }
    )

    assert config.get_option("CONFIG_CUSTOM_OPTION").value == "y"
    assert config.get_option("CONFIG_ANOTHER").value == "42"


def test_config_manager_merge_configs():
    """Test merging configurations."""
    manager = ConfigManager()

    # Create a base config
    base = KernelConfig()
    base.set_option("CONFIG_NET", "y")

    # Merge with a fragment
    merged = manager.merge_configs(
        base=base,
        fragments=["kasan"]
    )

    assert merged.get_option("CONFIG_NET").value == "y"
    assert merged.get_option("CONFIG_KASAN").value == "y"
