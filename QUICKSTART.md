# Quickstart Guide

Get started with kerneldev-mcp in 5 minutes.

## Installation

```bash
cd kerneldev-mcp
pip install -e .
```

Or install dependencies manually:
```bash
pip install mcp pydantic
```

## Verify Installation

```bash
# Run basic tests
python3 test_basic.py

# Should see:
# ✓ All tests passed!
```

## Usage Option 1: Command Line Testing

### Generate a Configuration

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, 'src')
from kerneldev_mcp.config_manager import ConfigManager

# Create manager
manager = ConfigManager()

# Generate BTRFS testing config with sanitizers
config = manager.generate_config(
    target="btrfs",
    debug_level="sanitizers"
)

# Print to stdout
print(config.to_config_text())
EOF
```

### Apply to Kernel

```bash
python3 << 'EOF'
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from kerneldev_mcp.config_manager import ConfigManager

# Create manager with kernel path
manager = ConfigManager(kernel_path=Path.home() / "linux")

# Generate and apply config
config = manager.generate_config(
    target="virtualization",
    debug_level="basic"
)

manager.apply_config(
    config=config,
    kernel_path=Path.home() / "linux"
)

print("✓ Config applied to ~/linux/.config")
EOF
```

### Build Kernel

```bash
cd ~/linux
make -j$(nproc)
```

## Usage Option 2: MCP Server

### Configure Claude Desktop

Edit your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "kerneldev": {
      "command": "python",
      "args": ["-m", "kerneldev_mcp.server"],
      "cwd": "/path/to/kerneldev-mcp"
    }
  }
}
```

### Restart Claude Desktop

The server will now be available as a tool.

### Example Prompts

**User**: "Configure a kernel for BTRFS testing with KASAN"

**Claude**: I'll generate a BTRFS configuration with KASAN memory sanitizer...
```
<uses get_config_template tool>
target: btrfs
debug_level: sanitizers
```

**User**: "Apply that config to ~/linux and give me build commands"

**Claude**:
```
<uses apply_config tool>
<uses generate_build_config tool>
```

## Available Targets

- **networking** - Full TCP/IP stack, netfilter, eBPF
- **btrfs** - BTRFS filesystem with all features
- **filesystem** - Multi-filesystem testing (ext4, XFS, etc.)
- **boot** - Minimal fast boot
- **virtualization** - QEMU/KVM/virtme-ng optimized

## Available Debug Levels

- **minimal** - Production-like, no overhead
- **basic** - Debug symbols, basic debugging
- **full_debug** - Comprehensive debugging
- **sanitizers** - KASAN, UBSAN, KCOV
- **lockdep** - Lock debugging
- **performance** - Perf events, optimized

## Quick Examples

### Example 1: Network Testing

```python
from kerneldev_mcp.config_manager import ConfigManager

manager = ConfigManager()
config = manager.generate_config("networking", "lockdep")
config.to_file("/tmp/net.config")
```

### Example 2: Fast virtme-ng Setup

```python
config = manager.generate_config(
    target="virtualization",
    debug_level="minimal",
    fragments=["virtme"]
)
```

### Example 3: Custom Options

```python
config = manager.generate_config(
    target="btrfs",
    debug_level="basic",
    additional_options={
        "CONFIG_BTRFS_DEBUG": "y",
        "CONFIG_BTRFS_ASSERT": "y"
    }
)
```

### Example 4: Merge Fragments

```python
merged = manager.merge_configs(
    base="target/filesystem",
    fragments=["kasan", "ubsan"]
)
```

## Test Your Configuration

After generating and applying a config:

```bash
cd ~/linux

# Review config
make menuconfig

# Build
make -j$(nproc)

# Test with virtme-ng (fast!)
vng --build
vng -- uname -r
```

## Common Workflows

### Workflow 1: BTRFS Development
```bash
# Generate config
python3 -c "
import sys; sys.path.insert(0, 'src')
from kerneldev_mcp.config_manager import ConfigManager
ConfigManager().generate_config('btrfs', 'sanitizers').to_file('btrfs.config')
"

# Apply
cp btrfs.config ~/linux/.config
cd ~/linux && make olddefconfig && make -j$(nproc)

# Test
vng --build
```

### Workflow 2: Network Testing
```bash
# Generate + apply in one step
python3 << 'EOF'
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from kerneldev_mcp.config_manager import ConfigManager

manager = ConfigManager()
config = manager.generate_config("networking", "lockdep")
manager.apply_config(config, Path.home() / "linux")
EOF

# Build
cd ~/linux && make -j$(nproc)

# Test
vng --build -- ip link show
```

### Workflow 3: Quick Iteration
```bash
# Minimal config for speed
python3 -c "
import sys; sys.path.insert(0, 'src')
from kerneldev_mcp.config_manager import ConfigManager
ConfigManager().generate_config('virtualization', 'minimal', fragments=['virtme']).to_file('/tmp/quick.config')
"

# Apply and build
cp /tmp/quick.config ~/linux/.config
cd ~/linux && make -j$(nproc)

# Very fast boot
vng --build
```

### Workflow 4: High-Performance Testing with null_blk

```bash
# Use MCP tool to boot with ultra-fast null_blk devices
# Requires Linux 5.0+ with null_blk module
boot_kernel_test(
    kernel_path="~/linux",
    devices=[
        {"size": "10G", "backing": "null_blk", "env_var": "TEST_DEV"}
    ],
    command="fio --name=test --rw=randread --bs=4k --direct=1 --filename=$TEST_DEV"
)
# 7M+ IOPS vs 50K with loop devices!
```

**Configure Memory Limits**:
```bash
# Adjust if you have more/less RAM
export KERNELDEV_NULL_BLK_MAX_SIZE=16    # Max 16GB per device
export KERNELDEV_NULL_BLK_TOTAL=32       # Max 32GB total

# Devices automatically fall back to tmpfs or disk if null_blk unavailable
```

## Troubleshooting

### "Module 'mcp' not found"
```bash
pip install mcp pydantic
```

### "Kernel path does not exist"
Set the correct kernel path:
```python
manager = ConfigManager(kernel_path=Path("/your/kernel/path"))
```

### "olddefconfig failed"
Check kernel source is valid:
```bash
cd ~/linux
make defconfig  # Should work
```

## Next Steps

1. Read [README.md](README.md) for full documentation
2. See [examples/example_usage.md](examples/example_usage.md) for more scenarios
3. Check [TESTING.md](TESTING.md) for test results
4. Review configuration templates in `src/config_templates/`

## Support

- Check documentation in this repository
- Review example configurations
- Read kernel documentation: https://kernel.org/doc/

## Quick Reference

| Task | Command |
|------|---------|
| List presets | `python3 -c "import sys; sys.path.insert(0,'src'); from kerneldev_mcp.templates import TemplateManager; print(TemplateManager().list_presets())"` |
| Generate config | `manager.generate_config(target, debug_level)` |
| Apply to kernel | `manager.apply_config(config, kernel_path)` |
| Merge configs | `manager.merge_configs(base, fragments)` |
| Run tests | `python3 test_basic.py` |

Enjoy streamlined kernel configuration! 🚀
