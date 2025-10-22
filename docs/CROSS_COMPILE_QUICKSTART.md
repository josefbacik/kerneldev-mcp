# Cross-Compilation Quick Start Guide

This guide shows you how to quickly get started with cross-compiling the Linux kernel using kerneldev-mcp.

## Prerequisites

Install the cross-compiler toolchain for your target architecture:

### ARM64 (aarch64)
```bash
# Fedora/RHEL
sudo dnf install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu

# Ubuntu/Debian
sudo apt install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu
```

### ARM (32-bit)
```bash
# Fedora/RHEL
sudo dnf install gcc-arm-linux-gnu binutils-arm-linux-gnu

# Ubuntu/Debian
sudo apt install gcc-arm-linux-gnueabihf binutils-arm-linux-gnueabihf
```

### RISC-V
```bash
# Fedora/RHEL
sudo dnf install gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu

# Ubuntu/Debian
sudo apt install gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

### Or Use LLVM (All Architectures)
```bash
# Fedora/RHEL
sudo dnf install clang llvm

# Ubuntu/Debian
sudo apt install clang llvm
```

## Quick Examples

### Python API

```python
from pathlib import Path
from kerneldev_mcp.config_manager import ConfigManager, CrossCompileConfig
from kerneldev_mcp.build_manager import KernelBuilder

# 1. Set up paths
kernel_path = Path.home() / "linux"

# 2. Create cross-compile configuration for ARM64
cross = CrossCompileConfig(arch="arm64")

# 3. Generate and apply kernel configuration
manager = ConfigManager()
config = manager.generate_config(
    target="virtualization",
    debug_level="basic",
    architecture="arm64"
)

manager.apply_config(
    config=config,
    kernel_path=kernel_path,
    cross_compile=cross
)

# 4. Build the kernel
builder = KernelBuilder(kernel_path)
result = builder.build(
    jobs=8,
    target="Image",  # ARM64 uses 'Image' instead of 'bzImage'
    cross_compile=cross
)

print(result.summary())
```

### Using LLVM

```python
# Just change use_llvm to True
cross = CrossCompileConfig(arch="arm64", use_llvm=True)

# Everything else stays the same
manager.apply_config(config=config, kernel_path=kernel_path, cross_compile=cross)
builder.build(cross_compile=cross, target="Image")
```

### Custom Toolchain Prefix

```python
# If you have a custom toolchain
cross = CrossCompileConfig(
    arch="arm64",
    cross_compile_prefix="/opt/my-toolchain/bin/aarch64-custom-linux-"
)
```

## MCP Tool Usage

### apply_config Tool

```json
{
  "name": "apply_config",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "config_source": "target/virtualization",
    "cross_compile_arch": "arm64"
  }
}
```

### build_kernel Tool

```json
{
  "name": "build_kernel",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "cross_compile_arch": "arm64",
    "target": "Image",
    "jobs": 8
  }
}
```

### Using LLVM via MCP

```json
{
  "name": "build_kernel",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "cross_compile_arch": "arm64",
    "use_llvm": true,
    "target": "Image"
  }
}
```

## Architecture-Specific Build Targets

Different architectures use different build targets:

| Architecture | Target | Output Location |
|--------------|--------|----------------|
| x86_64 | bzImage | arch/x86/boot/bzImage |
| ARM64 | Image | arch/arm64/boot/Image |
| ARM | zImage | arch/arm/boot/zImage |
| RISC-V | Image | arch/riscv/boot/Image |

## Common Workflows

### 1. Cross-Compile for Raspberry Pi 4 (ARM64)

```python
cross = CrossCompileConfig(arch="arm64")
config = manager.generate_config(target="boot", debug_level="minimal")
config.set_option("CONFIG_BCM2711", "y")  # Raspberry Pi 4 specific

manager.apply_config(config, kernel_path, cross_compile=cross)
result = builder.build(cross_compile=cross, target="Image", jobs=8)
```

### 2. Cross-Compile with Debug Symbols

```python
cross = CrossCompileConfig(arch="arm64")
config = manager.generate_config(
    target="virtualization",
    debug_level="full_debug"  # Includes KASAN, debug info, etc.
)

manager.apply_config(config, kernel_path, cross_compile=cross)
result = builder.build(cross_compile=cross)
```

### 3. Clean Cross-Compiled Build

```python
cross = CrossCompileConfig(arch="arm64")
builder.clean(target="mrproper", cross_compile=cross)
```

## Verifying Cross-Compilation

After configuration, you can verify the architecture:

```bash
cd ~/linux
grep "CONFIG_ARM64=y" .config
# Should output: CONFIG_ARM64=y
```

Or check the build command that will be used:

```python
cross = CrossCompileConfig(arch="arm64")
print(' '.join(cross.to_make_args()))
# Output: ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
```

## Troubleshooting

### Toolchain Not Found

**Error:** `aarch64-linux-gnu-gcc: command not found`

**Solution:** Install the cross-compiler toolchain (see Prerequisites above)

### Wrong Architecture in .config

**Error:** `.config` contains wrong architecture settings

**Solution:** Use `mrproper` to clean everything and reconfigure:

```python
builder.clean(target="mrproper", cross_compile=cross)
manager.apply_config(config, kernel_path, cross_compile=cross)
```

### Build Fails with Missing Headers

**Error:** Missing architecture-specific headers

**Solution:** Ensure you're passing cross_compile to both apply_config and build:

```python
# Both calls need cross_compile
manager.apply_config(config, kernel_path, cross_compile=cross)
builder.build(cross_compile=cross)
```

## Testing Your Setup

Run the validation test to ensure everything works:

```bash
cd /path/to/kerneldev-mcp
python3 test_arm64_cross_compile.py
```

Expected output:
```
Testing arm64 cross-compilation with kernel at /home/user/linux
Cross-compile configuration:
  Architecture: arm64
  Toolchain prefix: aarch64-linux-gnu-
  Using LLVM: False
  Make arguments: ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-

Applying configuration...
✓ Configuration applied successfully
✓ .config created at /home/user/linux/.config
✓ CONFIG_ARM64=y verified in .config

Testing kernel preparation with cross-compilation...
Running: make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j1 prepare
✓ prepare target completed successfully with cross-compilation

✓✓✓ All cross-compilation tests passed! ✓✓✓
```

## Next Steps

- Read [CROSS_COMPILE_IMPLEMENTATION.md](CROSS_COMPILE_IMPLEMENTATION.md) for implementation details
- Check out the unit tests in `tests/test_config_manager.py` for more examples
- See the [kernel building documentation](~/linux-dev-context/common/building.md) for advanced topics

## Getting Help

If you encounter issues:

1. Check that the toolchain is installed: `which aarch64-linux-gnu-gcc`
2. Verify kernel source is present: `ls ~/linux/Makefile`
3. Run unit tests: `pytest tests/test_config_manager.py -k cross_compile -v`
4. Check the [implementation documentation](CROSS_COMPILE_IMPLEMENTATION.md)
