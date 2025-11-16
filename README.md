# Kerneldev MCP - Kernel Development Configuration & Build Server

An MCP (Model Context Protocol) server for intelligent Linux kernel configuration management and building. This tool provides AI assistants with the ability to generate, manage, and optimize kernel configurations for different testing scenarios, plus build kernels with comprehensive error detection and reporting.

## Features

### Configuration Management

- **Pre-built Configuration Templates**: Ready-to-use configs for common testing scenarios
  - Networking testing (full TCP/IP stack, netfilter, eBPF/XDP)
  - BTRFS filesystem testing
  - General filesystem testing (ext4, XFS, BTRFS, F2FS, etc.)
  - Boot testing (minimal configs for fast iteration)
  - Virtualization testing (optimized for QEMU/KVM/virtme-ng)

- **Debug Levels**: Configurable debugging intensity
  - Minimal (production-like)
  - Basic (symbols + basic debugging)
  - Full debug (comprehensive debugging without sanitizers)
  - Sanitizers (KASAN, UBSAN, KCOV)
  - Lockdep (lock debugging and deadlock detection)
  - Performance (optimized for benchmarking)

- **Configuration Fragments**: Modular config components
  - KASAN (Kernel Address Sanitizer)
  - UBSAN (Undefined Behavior Sanitizer)
  - KCOV (code coverage for fuzzing)
  - Virtme-ng optimization
  - Performance tuning

- **Smart Configuration Management**
  - Merge multiple configs and fragments
  - Search Kconfig options
  - Validate configurations
  - Apply configs to kernel source tree

### Kernel Building

- **Build Validation**: Build kernels and validate build success
  - Parallel builds with configurable job count
  - Timeout support to prevent hanging
  - Out-of-tree build support

- **Error Detection & Reporting**: Automatically parse and report build errors
  - GCC/Clang error parsing with file:line:column information
  - Warning detection and reporting
  - Linker error detection
  - Human-readable error summaries

- **Build Management**:
  - Build specific targets (all, vmlinux, modules, etc.)
  - Clean operations (clean, mrproper, distclean)
  - Check build requirements
  - Get kernel version information

### Filesystem Testing (fstests)

- **Automated fstests Integration**: Complete support for filesystem regression testing
  - Install and manage fstests from git
  - Setup test/scratch devices (loop devices or existing)
  - Configure and run tests
  - Baseline comparison workflow for regression detection
  - Support for all major filesystems (ext4, btrfs, xfs, f2fs)

- **Baseline Management**: Track test results across kernel versions
  - Save baselines with metadata
  - Compare results to detect regressions
  - Identify new failures vs pre-existing issues
  - Essential for filesystem patch development

See [docs/implementation/FSTESTS.md](docs/implementation/FSTESTS.md) for detailed filesystem testing documentation.

### LVM Device Pools (Physical Storage)

- **High-Performance Testing**: Use LVM-managed physical storage instead of slow loop devices
  - **9-10× performance improvement** for I/O-intensive tests (475K vs 50K IOPS)
  - Only ~5% overhead compared to raw device
  - LVM provides snapshots, resizing, and thin provisioning

- **One-Time Setup**: Configure once, use everywhere
  - Comprehensive safety validation (10-point checklist)
  - All operations use sudo (no special permissions needed)
  - VG name is persistent across reboots (auto-discovered by LVM)
  - Transactional operations with rollback on failure

- **LVM Features**:
  - **Snapshots**: Create backups before risky tests, rollback if kernel corrupts data
  - **Dynamic Resizing**: Grow or shrink volumes without recreating pool
  - **Thin Provisioning**: Overcommit storage when needed
  - **Maximum Flexibility**: Industry-standard volume management

- **MCP Tools Available**:
  - `device_pool_setup`: Create LVM pools with safety checks
  - `device_pool_status`: Health check and volume info
  - `device_pool_teardown`: Safe removal with cleanup
  - `device_pool_list`: List all pools
  - `device_pool_resize`: Resize logical volumes
  - `device_pool_snapshot`: Snapshot management

**Quick Start:**
```bash
# Identify available disk
lsblk

# Create LVM pool via MCP (uses sudo)
device_pool_setup --device=/dev/nvme1n1

# Enable auto-use
export KERNELDEV_DEVICE_POOL=default

# All tests now use LVM volumes automatically!
fstests_vm_boot_and_run --kernel=/path/to/kernel --fstests=/path/to/fstests
```

**How it works:**
- Creates VG with name `kerneldev-default-vg` (persistent across reboots)
- Each test auto-creates unique LVs (timestamp + random in name)
- Multiple Claudes can share same pool concurrently
- LVs auto-deleted after tests (unless you use `keep_volumes=true`)

**Documentation:**
- [Device Pool Setup Guide](docs/device-pool-setup-guide.md) - Complete setup instructions
- [Architecture](docs/DEVICE-POOL-ARCHITECTURE.md) - Concurrency model and design details

## Installation

```bash
# Clone the repository
cd kerneldev-mcp

# Install in development mode
pip install -e .

# Or install dependencies manually
pip install mcp pydantic
```

## Usage

### As MCP Server

Add to your MCP client configuration (e.g., Claude Desktop):

```json
{
  "mcpServers": {
    "kerneldev": {
      "command": "python",
      "args": ["-m", "kerneldev_mcp.server"]
    }
  }
}
```

### Available MCP Tools

#### Configuration Tools

#### 1. `list_config_presets`
List all available configuration presets.

```python
# Example call
{
  "tool": "list_config_presets",
  "params": {
    "category": "target"  # Optional: "target", "debug", or "fragment"
  }
}
```

#### 2. `get_config_template`
Generate a complete kernel configuration from templates.

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",           # Required: networking, btrfs, filesystem, boot, virtualization
    "debug_level": "sanitizers",  # Optional: minimal, basic, full_debug, sanitizers, lockdep, performance
    "architecture": "x86_64",     # Optional: x86_64, arm64, arm, riscv
    "additional_options": {       # Optional: extra CONFIG options
      "CONFIG_BTRFS_DEBUG": "y",
      "CONFIG_BTRFS_ASSERT": "y"
    },
    "fragments": ["kasan", "kcov"] # Optional: additional fragments to merge
  }
}
```

#### 3. `create_config_fragment`
Create a custom configuration fragment.

```python
{
  "tool": "create_config_fragment",
  "params": {
    "name": "my_debug",
    "options": {
      "CONFIG_DEBUG_CUSTOM": "y",
      "CONFIG_EXTRA_CHECKS": "y"
    },
    "description": "My custom debug options"
  }
}
```

#### 4. `merge_configs`
Merge multiple configuration fragments.

```python
{
  "tool": "merge_configs",
  "params": {
    "base": "target/networking",  # Base config (template name or file path)
    "fragments": ["kasan", "lockdep"],  # Fragments to merge
    "output": "/path/to/output.config"  # Optional: save to file
  }
}
```

#### 5. `apply_config`
Apply configuration to kernel source tree.

```python
{
  "tool": "apply_config",
  "params": {
    "kernel_path": "~/linux",
    "config_source": "target/btrfs",  # Template name or file path
    "merge_with_existing": false      # Optional: merge with existing .config
  }
}
```

#### 6. `validate_config`
Validate a kernel configuration.

```python
{
  "tool": "validate_config",
  "params": {
    "config_path": "~/linux/.config",
    "kernel_path": "~/linux"  # Optional: for Kconfig validation
  }
}
```

#### 7. `search_config_options`
Search for kernel configuration options.

```python
{
  "tool": "search_config_options",
  "params": {
    "query": "KASAN",
    "kernel_path": "~/linux"
  }
}
```

#### 8. `generate_build_config`
Generate optimized build configuration and commands.

#### Build Tools

#### 9. `build_kernel`
Build the Linux kernel and validate the build.

```python
{
  "tool": "build_kernel",
  "params": {
    "kernel_path": "~/linux",
    "jobs": 16,                   # Optional: parallel jobs
    "verbose": False,             # Optional: detailed output
    "keep_going": False,          # Optional: continue despite errors
    "target": "all",              # Optional: make target
    "build_dir": "/tmp/build",    # Optional: out-of-tree build
    "timeout": 3600,              # Optional: timeout in seconds
    "clean_first": False          # Optional: clean before building
  }
}
```

Returns build status with errors/warnings parsed and formatted.

#### 10. `check_build_requirements`
Check if kernel source is ready to build.

```python
{
  "tool": "check_build_requirements",
  "params": {
    "kernel_path": "~/linux"
  }
}
```

Returns validation of kernel source, configuration, and build tools.

#### 11. `clean_kernel_build`
Clean kernel build artifacts.

```python
{
  "tool": "clean_kernel_build",
  "params": {
    "kernel_path": "~/linux",
    "clean_type": "clean"  # clean, mrproper, or distclean
  }
}
```

```python
{
  "tool": "generate_build_config",
  "params": {
    "target": "btrfs",
    "optimization": "speed",  # speed, debug, or size
    "ccache": true,
    "out_of_tree": true,
    "kernel_path": "~/linux"
  }
}
```

### MCP Resources

Access configuration templates directly:

- `config://presets` - JSON list of all available presets
- `config://templates/target/{name}` - Target configurations (networking, btrfs, etc.)
- `config://templates/debug/{name}` - Debug level configurations
- `config://templates/fragment/{name}` - Configuration fragments

## Configuration Templates

### Targets

1. **networking** - Comprehensive network stack testing
   - Full TCP/IP, IPv6, netfilter, eBPF/XDP
   - Virtual networking (veth, bridges, VLANs)
   - Traffic control and QoS

2. **btrfs** - BTRFS filesystem development
   - BTRFS with all features and debugging
   - Device mapper, RAID, snapshots
   - Compression and checksumming

3. **filesystem** - General filesystem testing
   - All major filesystems (ext4, XFS, BTRFS, F2FS)
   - Network filesystems (NFS, CIFS)
   - FUSE, overlay, quota support

4. **boot** - Minimal boot testing
   - Fast boot for iteration
   - Console and early debugging
   - Virtio drivers for QEMU

5. **virtualization** - Virtualization testing
   - KVM support
   - VirtIO drivers (optimized for virtme-ng)
   - VirtioFS and 9P filesystem

### Debug Levels

1. **minimal** - Production-like build with minimal overhead
2. **basic** - Debug symbols + basic debugging (CONFIG_DEBUG_INFO, FRAME_POINTER)
3. **full_debug** - Comprehensive debugging without sanitizers
4. **sanitizers** - Memory sanitizers (KASAN, UBSAN, KCOV, KFENCE)
5. **lockdep** - Lock debugging and deadlock detection
6. **performance** - Optimized for performance testing and benchmarking

### Fragments

- **kasan** - Kernel Address Sanitizer (memory error detection)
- **ubsan** - Undefined Behavior Sanitizer
- **kcov** - Code coverage for fuzzing (syzkaller)
- **virtme** - Optimizations for virtme-ng testing
- **performance** - Performance monitoring and profiling

## Example Workflows

### 1. Configure kernel for BTRFS testing with KASAN

```python
# Generate config
get_config_template(
    target="btrfs",
    debug_level="sanitizers",
    fragments=["kasan"]
)

# Apply to kernel
apply_config(
    kernel_path="~/linux",
    config_source="target/btrfs"
)

# Build
# Output will include build commands
```

### 2. Quick virtme-ng testing setup

```python
get_config_template(
    target="virtualization",
    debug_level="minimal",
    fragments=["virtme"]
)
```

### 3. Network testing with lockdep

```python
get_config_template(
    target="networking",
    debug_level="lockdep"
)
```

### 4. Custom configuration

```python
# Start with base
merge_configs(
    base="target/filesystem",
    fragments=["kasan", "ubsan", "kcov"]
)

# Add custom options
get_config_template(
    target="filesystem",
    debug_level="sanitizers",
    additional_options={
        "CONFIG_CUSTOM_FEATURE": "y"
    }
)
```

## Integration with Kernel Development Workflow

This MCP server is designed to work with the [linux-dev-context](https://github.com/your-repo/linux-dev-context) project, which provides comprehensive kernel development context files for AI assistants.

Typical workflow:
1. Use this MCP to generate kernel config for your testing target
2. Apply config to kernel source
3. Build kernel (optionally with virtme-ng)
4. Test using appropriate test suite (fstests, network tests, etc.)

## Testing

```bash
# Run tests
pytest tests/

# Run specific test file
pytest tests/test_config_manager.py

# Run with verbose output
pytest -v
```

## Project Structure

```
kerneldev-mcp/
├── src/
│   ├── kerneldev_mcp/
│   │   ├── __init__.py
│   │   ├── server.py           # Main MCP server
│   │   ├── config_manager.py   # Config generation and merging
│   │   ├── templates.py        # Template management
│   └── config_templates/
│       ├── targets/            # Target configurations
│       ├── debug/              # Debug level configurations
│       └── fragments/          # Modular fragments
├── tests/
│   ├── test_templates.py
│   ├── test_config_manager.py
│   └── test_server.py
├── pyproject.toml
└── README.md
```

## Contributing

Contributions welcome! Please:
1. Add new configuration templates for additional testing scenarios
2. Improve existing templates based on kernel development best practices
3. Add tests for new functionality
4. Update documentation

### Development Setup

After cloning the repository, set up git hooks to ensure code quality:

```bash
./setup-hooks.sh
```

This configures:
- **pre-commit hook**: Runs unit tests before allowing commits
  - Ensures all tests pass before code is committed
  - Helps catch regressions early
  - Can be bypassed with `git commit --no-verify` (not recommended)

The hooks are stored in the `hooks/` directory and are version-controlled, ensuring all contributors use the same quality checks.

## License

GPL-2.0 (to match Linux kernel licensing)

## References

- [Linux Kernel Documentation](https://www.kernel.org/doc/html/latest/)
- [Kernel Configuration (Kconfig)](https://www.kernel.org/doc/html/latest/kbuild/kconfig.html)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [virtme-ng](https://github.com/arighi/virtme-ng) - Fast kernel testing tool

## Support

For issues and questions:
- GitHub Issues: [your-repo/kerneldev-mcp/issues]
- Related: [linux-dev-context](https://github.com/your-repo/linux-dev-context)
