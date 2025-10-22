# Build Features

The kerneldev-mcp server now includes comprehensive kernel building capabilities with error detection and reporting.

## Overview

The build features allow you to:
- Build the Linux kernel directly from the MCP server
- Validate build success/failure
- Capture and parse compile errors and warnings
- Get detailed error reporting with file/line information
- Control build parameters (parallelism, timeout, targets)
- Clean build artifacts

## MCP Tools

### 1. `build_kernel`

Build the Linux kernel and validate the build.

```python
{
  "tool": "build_kernel",
  "params": {
    "kernel_path": "~/linux",
    "jobs": 8,                    # Optional: parallel jobs (default: CPU count)
    "verbose": False,             # Optional: detailed output
    "keep_going": False,          # Optional: continue despite errors
    "target": "all",              # Optional: all, vmlinux, modules, bzImage, Image
    "build_dir": "/tmp/build",    # Optional: out-of-tree build
    "timeout": 3600,              # Optional: timeout in seconds
    "clean_first": False          # Optional: clean before building
  }
}
```

**Returns:**
- Build summary (success/failure, duration)
- List of compile errors with file:line:column information
- List of warnings
- Build artifact locations

**Example output:**
```
✓ Build succeeded in 456.2s (12 warnings)

Build artifacts:
  vmlinux: /home/user/linux/vmlinux
  System.map: /home/user/linux/System.map
```

Or on failure:
```
✗ Build failed in 123.4s (3 errors, 5 warnings)

Errors (3):
  1. drivers/net/test.c:42:10: error: 'foo' undeclared
  2. fs/btrfs/inode.c:234:5: error: conflicting types for 'bar'
  3. init/main.c:100:1: error: expected ';' before 'int'

Warnings (5):
  1. drivers/usb/core/hub.c:567:12: warning: unused variable 'ret'
  ...
```

### 2. `check_build_requirements`

Check if kernel source is ready to build.

```python
{
  "tool": "check_build_requirements",
  "params": {
    "kernel_path": "~/linux"
  }
}
```

**Returns:**
```
Kernel path: /home/user/linux
✓ Valid kernel source tree
✓ Kernel version: 6.16.0
✓ Kernel is configured (.config exists)
✓ make available
✓ gcc available
✓ ld available
```

### 3. `clean_kernel_build`

Clean kernel build artifacts.

```python
{
  "tool": "clean_kernel_build",
  "params": {
    "kernel_path": "~/linux",
    "clean_type": "clean",       # clean, mrproper, or distclean
    "build_dir": "/tmp/build"    # Optional: for out-of-tree builds
  }
}
```

**Clean types:**
- `clean` - Remove build artifacts, keep .config
- `mrproper` - Remove everything including .config
- `distclean` - mrproper + editor backups

## Build Error Detection

The build system automatically detects and parses:

### GCC/Clang Errors
```
drivers/net/test.c:42:10: error: 'foo' undeclared
```
Parsed as:
- File: `drivers/net/test.c`
- Line: `42`
- Column: `10`
- Type: `error`
- Message: `'foo' undeclared`

### GCC/Clang Warnings
```
fs/btrfs/inode.c:234:5: warning: unused variable 'ret'
```

### Linker Errors
```
init/main.o:123: undefined reference to `some_function'
```

### Make Errors
```
make[2]: *** [drivers/net/test.o] Error 1
```

## Usage Examples

### Example 1: Complete Build Workflow

```python
# 1. Check requirements
check_build_requirements(kernel_path="~/linux")

# 2. Generate and apply config
config = get_config_template(target="btrfs", debug_level="sanitizers")
apply_config(kernel_path="~/linux", config_source="inline", config_content=config)

# 3. Build
build_kernel(
    kernel_path="~/linux",
    jobs=16,
    timeout=7200  # 2 hours
)
```

### Example 2: Incremental Build with Error Handling

```python
# Build with keep_going to see all errors
result = build_kernel(
    kernel_path="~/linux",
    jobs=8,
    keep_going=True  # Continue despite errors
)

# If build fails, errors are reported
# Fix errors, then rebuild
build_kernel(kernel_path="~/linux")
```

### Example 3: Fast Iteration

```python
# Clean first for fresh build
build_kernel(
    kernel_path="~/linux",
    jobs=16,
    target="modules",     # Build only modules
    clean_first=True
)
```

### Example 4: Out-of-Tree Build

```python
# Build to separate directory
build_kernel(
    kernel_path="~/linux",
    build_dir="/tmp/kernel-build",
    jobs=16
)
```

### Example 5: Single File Build for Testing

```python
# Build just one file to test
build_kernel(
    kernel_path="~/linux",
    target="drivers/net/ethernet/intel/e1000e/netdev.o",
    jobs=1
)
```

## Build Manager API

The `KernelBuilder` class can also be used directly in Python:

```python
from pathlib import Path
from kerneldev_mcp.build_manager import KernelBuilder

# Initialize
builder = KernelBuilder(Path("~/linux"))

# Check if configured
if not builder.check_config():
    print("Kernel not configured")

# Get version
version = builder.get_kernel_version()
print(f"Building kernel {version}")

# Build
result = builder.build(
    jobs=16,
    verbose=False,
    timeout=3600
)

if result.success:
    print(f"Build succeeded in {result.duration:.1f}s")
else:
    print(f"Build failed with {result.error_count} errors")
    for error in result.errors:
        print(f"  {error}")
```

## Build Result Object

The `BuildResult` object contains:

```python
@dataclass
class BuildResult:
    success: bool                    # True if build succeeded
    duration: float                  # Build time in seconds
    errors: List[BuildError]         # Compile errors
    warnings: List[BuildError]       # Compile warnings
    output: str                      # Full build output
    exit_code: int                   # Make exit code

    @property
    def error_count(self) -> int     # Number of errors

    @property
    def warning_count(self) -> int   # Number of warnings

    def summary(self) -> str         # Human-readable summary
```

## Build Error Object

Each error/warning is represented as:

```python
@dataclass
class BuildError:
    file: str                        # Source file path
    line: Optional[int]              # Line number
    column: Optional[int]            # Column number
    error_type: str                  # 'error', 'warning', 'fatal'
    message: str                     # Error message
    context: Optional[str]           # Source context
```

## Performance Considerations

### Parallel Builds
- Default: Uses all CPU cores (`os.cpu_count()`)
- Recommended: 1.5x to 2x CPU count for I/O-bound systems
- Example: 16-core system → use 24-32 jobs

### Timeouts
- Small target (single file): 60-120 seconds
- Scripts: 300 seconds (5 minutes)
- Modules: 1800 seconds (30 minutes)
- Full kernel: 3600-7200 seconds (1-2 hours)

### Out-of-Tree Builds
- Faster: Build directory on tmpfs or fast SSD
- Cleaner: Keep source tree pristine
- Parallel: Multiple configs from same source

## Integration with Configuration

The build features work seamlessly with configuration management:

```python
# Generate config for specific testing
config = get_config_template(
    target="networking",
    debug_level="sanitizers"
)

# Apply config
apply_config(
    kernel_path="~/linux",
    config_source="inline",
    config_content=config
)

# Build with error detection
result = build_kernel(
    kernel_path="~/linux",
    jobs=16
)

# Errors are automatically captured and reported
```

## Error Handling Best Practices

1. **Use keep_going for debugging**: See all errors at once
   ```python
   build_kernel(kernel_path="~/linux", keep_going=True)
   ```

2. **Set appropriate timeouts**: Avoid hanging builds
   ```python
   build_kernel(kernel_path="~/linux", timeout=1800)
   ```

3. **Check requirements first**: Validate before building
   ```python
   check_build_requirements(kernel_path="~/linux")
   ```

4. **Clean when needed**: Fresh build for config changes
   ```python
   build_kernel(kernel_path="~/linux", clean_first=True)
   ```

5. **Build small targets first**: Test before full build
   ```python
   build_kernel(kernel_path="~/linux", target="scripts")
   ```

## Common Build Targets

- `all` - Full kernel and modules (default)
- `vmlinux` - Just the kernel image
- `modules` - Just the modules
- `bzImage` - Compressed x86 kernel (arch/x86/boot/bzImage)
- `Image` - ARM64/other kernel image
- `scripts` - Build scripts (fast, good for testing)
- `{path}/{file}.o` - Build single object file
- `{path}/` - Build entire directory

## Testing Build Features

Run the provided test scripts:

```bash
# Basic build functionality tests
python3 test_build.py

# Real kernel build tests
python3 test_real_kernel_build.py
```

## Limitations

1. **No interactive builds**: Builds must complete without user input
2. **Error parsing**: May not catch all error formats (custom toolchains)
3. **Resource limits**: Respects system resources, may OOM on large builds
4. **No progress tracking**: Build runs to completion or timeout

## Future Enhancements

Potential improvements:
- Background/asynchronous builds
- Progress tracking and streaming output
- Build artifact caching
- Cross-compilation support
- Distributed builds (distcc/icecream)
- Build performance analysis
- Automatic error suggestions
- Integration with git bisect for regression finding
