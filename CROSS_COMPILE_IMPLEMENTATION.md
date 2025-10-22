# Cross-Compilation Implementation

This document describes the cross-compilation support added to kerneldev-mcp.

## Overview

The kerneldev-mcp server now supports cross-compilation for multiple architectures, allowing users to build Linux kernels for different target platforms from their development machine.

## Features

### Supported Architectures

- **ARM64** (aarch64) - 64-bit ARM
- **ARM** (arm) - 32-bit ARM
- **RISC-V** (riscv) - RISC-V 64-bit
- **PowerPC** (powerpc) - PowerPC 64-bit Little Endian
- **MIPS** (mips) - MIPS architecture
- **x86_64** - Native x86-64 compilation
- **x86** - 32-bit x86

### Toolchain Support

The implementation supports two toolchain types:

1. **GCC Cross-Compilation**: Traditional cross-compilation using architecture-specific GCC toolchains
2. **LLVM/Clang**: Simplified cross-compilation using LLVM's unified toolchain

## Implementation Details

### 1. CrossCompileConfig Class

A new dataclass `CrossCompileConfig` was added to `config_manager.py`:

```python
@dataclass
class CrossCompileConfig:
    arch: str  # Target architecture
    cross_compile_prefix: Optional[str] = None  # Toolchain prefix
    use_llvm: bool = False  # Use LLVM instead of GCC
```

**Features:**
- Auto-detection of toolchain prefixes based on architecture
- Conversion to make arguments (`ARCH=...`, `CROSS_COMPILE=...`, `LLVM=1`)
- Support for custom toolchain prefixes

**Default Toolchain Mappings:**
- arm64 → aarch64-linux-gnu-
- arm → arm-linux-gnueabihf-
- riscv → riscv64-linux-gnu-
- powerpc → powerpc64le-linux-gnu-
- mips → mips-linux-gnu-
- x86_64/x86 → None (native)

### 2. Updated config_manager.py

The `apply_config()` method now accepts an optional `cross_compile` parameter:

```python
def apply_config(
    self,
    config: Union[KernelConfig, str, Path],
    kernel_path: Optional[Path] = None,
    merge_with_existing: bool = False,
    cross_compile: Optional[CrossCompileConfig] = None
) -> bool:
```

When provided, cross-compilation arguments are passed to `make olddefconfig` to properly resolve dependencies for the target architecture.

### 3. Updated build_manager.py

The `build()` and `clean()` methods now support cross-compilation:

```python
def build(
    self,
    jobs: Optional[int] = None,
    verbose: bool = False,
    keep_going: bool = False,
    target: str = "all",
    build_dir: Optional[Path] = None,
    make_args: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    cross_compile: Optional["CrossCompileConfig"] = None
) -> BuildResult:
```

Cross-compilation arguments are prepended to the make command, ensuring they apply to all build operations.

### 4. Updated MCP Tools (server.py)

Three MCP tools were updated with cross-compilation parameters:

#### apply_config
- `cross_compile_arch`: Target architecture
- `cross_compile_prefix`: Optional custom toolchain prefix
- `use_llvm`: Use LLVM toolchain

#### build_kernel
- Same cross-compilation parameters as apply_config
- Provides architecture-specific build artifact paths in output

#### clean_kernel_build
- Same cross-compilation parameters
- Ensures clean operations respect target architecture

### 5. Helper Function

A helper function `_parse_cross_compile_args()` was added to parse cross-compilation arguments from MCP tool calls and create CrossCompileConfig objects.

## Usage Examples

### Example 1: Cross-Compile for ARM64 with GCC

```python
from kerneldev_mcp.config_manager import ConfigManager, CrossCompileConfig

# Create cross-compile config
cross = CrossCompileConfig(arch="arm64")

# Apply configuration
manager = ConfigManager()
config = manager.generate_config(target="networking", debug_level="basic")
manager.apply_config(
    config=config,
    kernel_path="/path/to/linux",
    cross_compile=cross
)

# Build
from kerneldev_mcp.build_manager import KernelBuilder
builder = KernelBuilder("/path/to/linux")
result = builder.build(cross_compile=cross, target="Image")
```

### Example 2: Cross-Compile for RISC-V with LLVM

```python
# Use LLVM for simplified cross-compilation
cross = CrossCompileConfig(arch="riscv", use_llvm=True)

# Apply and build as above
```

### Example 3: Via MCP Tool

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

## Testing

### Unit Tests

Eight comprehensive unit tests were added to `tests/test_config_manager.py`:

1. `test_cross_compile_config_arm64` - Verify ARM64 config with auto-detection
2. `test_cross_compile_config_arm` - Verify ARM config
3. `test_cross_compile_config_riscv` - Verify RISC-V config
4. `test_cross_compile_config_custom_prefix` - Custom toolchain prefix
5. `test_cross_compile_config_llvm` - LLVM toolchain usage
6. `test_cross_compile_config_to_make_env` - Environment variable conversion
7. `test_cross_compile_config_to_make_args` - Make argument conversion
8. `test_cross_compile_config_native` - Native x86_64 compilation

All tests pass successfully.

### Integration Test

A comprehensive integration test (`test_arm64_cross_compile.py`) validates:
- Configuration application with cross-compilation
- Kernel .config generation for ARM64
- make prepare execution with cross-compilation arguments

**Test Result:** ✓ All cross-compilation tests passed!

The test successfully:
1. Applied ARM64 configuration to ~/linux kernel
2. Verified CONFIG_ARM64=y in generated .config
3. Executed `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- prepare`
4. Completed without errors

## Benefits

1. **Simplified Workflow**: No need to manually specify ARCH and CROSS_COMPILE
2. **Auto-Detection**: Toolchain prefixes are automatically detected
3. **LLVM Support**: Easy switching to LLVM for architectures that support it
4. **Consistent API**: Same interface across config, build, and clean operations
5. **Validated**: Comprehensive unit and integration tests ensure reliability

## Requirements

For GCC cross-compilation, install the appropriate toolchain:

```bash
# Fedora/RHEL
sudo dnf install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu

# Ubuntu/Debian
sudo apt install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu
```

For LLVM cross-compilation, install LLVM/Clang (supports multiple architectures):

```bash
# Fedora/RHEL
sudo dnf install clang llvm

# Ubuntu/Debian
sudo apt install clang llvm
```

## Reference

Implementation based on Linux kernel documentation from ~/linux-dev-context/common/building.md, specifically the "Advanced Cross-Compilation" section (lines 1504-1676).

## Files Modified

1. `src/kerneldev_mcp/config_manager.py` - Added CrossCompileConfig class
2. `src/kerneldev_mcp/build_manager.py` - Updated build() and clean() methods
3. `src/kerneldev_mcp/server.py` - Updated MCP tools and added helper function
4. `tests/test_config_manager.py` - Added 8 unit tests
5. `test_arm64_cross_compile.py` - New integration test

## Future Enhancements

Potential improvements for future versions:
1. Toolchain validation (check if cross-compiler is installed)
2. Support for additional architectures (s390x, sparc, etc.)
3. Custom LLVM version specification (LLVM=-14)
4. Toolchain path configuration
5. Cross-compilation specific configuration templates
