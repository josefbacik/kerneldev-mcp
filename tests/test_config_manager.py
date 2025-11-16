"""
Tests for configuration management.
"""

import pytest
from pathlib import Path
from kerneldev_mcp.config_manager import (
    ConfigOption,
    KernelConfig,
    ConfigManager,
    CrossCompileConfig,
)


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
        target="networking", debug_level="basic", architecture="x86_64"
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
        target="virtualization", debug_level="minimal", fragments=["kasan"]
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
        additional_options={"CONFIG_CUSTOM_OPTION": "y", "CONFIG_ANOTHER": "42"},
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
    merged = manager.merge_configs(base=base, fragments=["kasan"])

    assert merged.get_option("CONFIG_NET").value == "y"
    assert merged.get_option("CONFIG_KASAN").value == "y"


def test_cross_compile_config_arm64():
    """Test CrossCompileConfig for ARM64."""
    cross = CrossCompileConfig(arch="arm64")

    # Should auto-detect toolchain prefix
    assert cross.arch == "arm64"
    assert cross.cross_compile_prefix == "aarch64-linux-gnu-"
    assert cross.use_llvm is False


def test_cross_compile_config_arm():
    """Test CrossCompileConfig for ARM."""
    cross = CrossCompileConfig(arch="arm")

    assert cross.arch == "arm"
    assert cross.cross_compile_prefix == "arm-linux-gnueabihf-"


def test_cross_compile_config_riscv():
    """Test CrossCompileConfig for RISC-V."""
    cross = CrossCompileConfig(arch="riscv")

    assert cross.arch == "riscv"
    assert cross.cross_compile_prefix == "riscv64-linux-gnu-"


def test_cross_compile_config_custom_prefix():
    """Test CrossCompileConfig with custom prefix."""
    cross = CrossCompileConfig(arch="arm64", cross_compile_prefix="my-custom-toolchain-")

    assert cross.cross_compile_prefix == "my-custom-toolchain-"


def test_cross_compile_config_llvm():
    """Test CrossCompileConfig with LLVM."""
    cross = CrossCompileConfig(arch="arm64", use_llvm=True)

    assert cross.arch == "arm64"
    assert cross.use_llvm is True
    # When using LLVM, cross_compile_prefix should not be set
    assert cross.cross_compile_prefix is None


def test_cross_compile_config_to_make_env():
    """Test converting CrossCompileConfig to environment variables."""
    # GCC cross-compilation
    cross = CrossCompileConfig(arch="arm64")
    env = cross.to_make_env()

    assert env["ARCH"] == "arm64"
    assert env["CROSS_COMPILE"] == "aarch64-linux-gnu-"
    assert "LLVM" not in env

    # LLVM cross-compilation
    cross_llvm = CrossCompileConfig(arch="arm64", use_llvm=True)
    env_llvm = cross_llvm.to_make_env()

    assert env_llvm["ARCH"] == "arm64"
    assert env_llvm["LLVM"] == "1"
    assert "CROSS_COMPILE" not in env_llvm


def test_cross_compile_config_to_make_args():
    """Test converting CrossCompileConfig to make arguments."""
    # GCC cross-compilation
    cross = CrossCompileConfig(arch="arm64")
    args = cross.to_make_args()

    assert "ARCH=arm64" in args
    assert "CROSS_COMPILE=aarch64-linux-gnu-" in args
    assert "LLVM=1" not in args

    # LLVM cross-compilation
    cross_llvm = CrossCompileConfig(arch="riscv", use_llvm=True)
    args_llvm = cross_llvm.to_make_args()

    assert "ARCH=riscv" in args_llvm
    assert "LLVM=1" in args_llvm
    # Should not have CROSS_COMPILE with LLVM
    assert not any("CROSS_COMPILE" in arg for arg in args_llvm)


def test_cross_compile_config_native():
    """Test CrossCompileConfig for native x86_64."""
    cross = CrossCompileConfig(arch="x86_64")

    # x86_64 native compilation should not have cross_compile_prefix
    assert cross.arch == "x86_64"
    assert cross.cross_compile_prefix is None

    env = cross.to_make_env()
    assert env["ARCH"] == "x86_64"
    assert "CROSS_COMPILE" not in env


def test_modify_kernel_config_no_config(tmp_path):
    """Test modify_kernel_config with no existing .config."""
    manager = ConfigManager()

    result = manager.modify_kernel_config(kernel_path=tmp_path, options={"CONFIG_DEBUG_INFO": "y"})

    assert not result["success"]
    assert len(result["errors"]) > 0
    assert "No .config found" in result["errors"][0]


def test_modify_kernel_config_basic(tmp_path):
    """Test basic config modification."""
    manager = ConfigManager()

    # Create a minimal .config
    config = KernelConfig()
    config.set_option("CONFIG_NET", "y")
    config.set_option("CONFIG_DEBUG_INFO", None)  # Not set
    config.to_file(tmp_path / ".config")

    # Create minimal Makefile to mock kernel tree
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
# Mock Makefile for testing
VERSION = 6
PATCHLEVEL = 16
SUBLEVEL = 0

olddefconfig:
\t@echo "Running olddefconfig"
""")

    # Modify config
    result = manager.modify_kernel_config(
        kernel_path=tmp_path,
        options={
            "CONFIG_DEBUG_INFO": "y",  # Change from not set to y
            "CONFIG_KASAN": "y",  # Add new option
        },
    )

    # Check result
    assert len(result["changes"]) >= 1  # At least DEBUG_INFO should change

    # Find the DEBUG_INFO change
    debug_info_change = None
    for change in result["changes"]:
        if change[0] == "CONFIG_DEBUG_INFO":
            debug_info_change = change
            break

    assert debug_info_change is not None
    assert debug_info_change[1] == "not set"  # old value
    assert debug_info_change[2] == "y"  # new value


def test_modify_kernel_config_with_prefix(tmp_path):
    """Test that CONFIG_ prefix is optional."""
    manager = ConfigManager()

    # Create a minimal .config
    config = KernelConfig()
    config.set_option("CONFIG_NET", "y")
    config.to_file(tmp_path / ".config")

    # Create minimal Makefile
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
olddefconfig:
\t@echo "Running olddefconfig"
""")

    # Modify config without CONFIG_ prefix
    result = manager.modify_kernel_config(
        kernel_path=tmp_path,
        options={
            "DEBUG_KERNEL": "y",  # No CONFIG_ prefix
        },
    )

    # Should have added CONFIG_DEBUG_KERNEL
    assert any("CONFIG_DEBUG_KERNEL" in change[0] for change in result["changes"])


def test_modify_kernel_config_unset_option(tmp_path):
    """Test unsetting a config option."""
    manager = ConfigManager()

    # Create a minimal .config with an option enabled
    config = KernelConfig()
    config.set_option("CONFIG_DEBUG_INFO", "y")
    config.to_file(tmp_path / ".config")

    # Create minimal Makefile
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
olddefconfig:
\t@echo "Running olddefconfig"
""")

    # Unset the option
    result = manager.modify_kernel_config(
        kernel_path=tmp_path,
        options={
            "CONFIG_DEBUG_INFO": None  # Unset
        },
    )

    # Should show change from y to not set
    assert len(result["changes"]) >= 1
    debug_change = None
    for change in result["changes"]:
        if change[0] == "CONFIG_DEBUG_INFO":
            debug_change = change
            break

    assert debug_change is not None
    assert debug_change[1] == "y"
    assert debug_change[2] == "not set"


def test_modify_kernel_config_no_changes(tmp_path):
    """Test when options already have requested values."""
    manager = ConfigManager()

    # Create a minimal .config
    config = KernelConfig()
    config.set_option("CONFIG_NET", "y")
    config.to_file(tmp_path / ".config")

    # Create minimal Makefile
    makefile = tmp_path / "Makefile"
    makefile.write_text("""
olddefconfig:
\t@echo "Running olddefconfig"
""")

    # Request same value
    result = manager.modify_kernel_config(
        kernel_path=tmp_path,
        options={
            "CONFIG_NET": "y"  # Already set to y
        },
    )

    # Should have no changes
    assert len(result["changes"]) == 0
