# Kernel Boot Testing with virtme-ng

This document describes the kernel boot testing and validation capabilities added to kerneldev-mcp.

## Overview

The kerneldev-mcp server now supports automated kernel boot testing using virtme-ng, allowing AI assistants to validate that compiled kernels boot successfully and check for errors, panics, or issues during boot.

## Features

### Boot Testing Capabilities

- **Automated Boot**: Boot kernels in isolated VMs using virtme-ng
- **D mesg Analysis**: Capture and parse kernel boot messages
- **Error Detection**: Automatically detect errors, warnings, panics, and oops
- **Boot Validation**: Confirm kernel boots successfully without critical issues
- **Configurable Resources**: Control VM memory, CPUs, and timeout settings

### Dmesg Analysis

The system analyzes dmesg output and categorizes messages by severity:

1. **Panics**: Kernel panics and fatal errors (emerg level)
2. **Oops**: Kernel oops and BUG messages (crit level)
3. **Errors**: Error conditions and failures (err level)
4. **Warnings**: Warning messages (warn level)

## Implementation Details

### Core Components

#### 1. DmesgMessage Class

Represents a single kernel log message:

```python
@dataclass
class DmesgMessage:
    timestamp: Optional[float]  # Seconds since boot
    level: str  # emerg, alert, crit, err, warn, notice, info, debug
    subsystem: Optional[str]  # Kernel subsystem (BTRFS, EXT4, etc.)
    message: str
```

#### 2. BootResult Class

Contains boot test results:

```python
@dataclass
class BootResult:
    success: bool
    duration: float
    boot_completed: bool
    kernel_version: Optional[str]
    errors: List[DmesgMessage]
    warnings: List[DmesgMessage]
    panics: List[DmesgMessage]
    oops: List[DmesgMessage]
    dmesg_output: str
    exit_code: int
    timeout_occurred: bool
```

**Key Properties:**
- `has_critical_issues`: True if panics or oops detected
- `error_count`, `warning_count`, `panic_count`, `oops_count`: Counts by category
- `summary()`: Human-readable summary of boot result

#### 3. DmesgParser Class

Parses and analyzes dmesg output:

- **parse_dmesg_line()**: Parse individual dmesg lines
- **analyze_dmesg()**: Categorize all messages by severity
- Supports multiple dmesg formats (timestamped, log levels, subsystems)
- Pattern-based detection of panics, oops, errors, and warnings

#### 4. BootManager Class

Manages kernel boot testing:

```python
class BootManager:
    def __init__(self, kernel_path: Path)
    def check_virtme_ng(self) -> bool
    def boot_test(
        self,
        timeout: int = 60,
        memory: str = "2G",
        cpus: int = 2,
        cross_compile: Optional[CrossCompileConfig] = None,
        extra_args: Optional[List[str]] = None,
        use_host_kernel: bool = False
    ) -> BootResult
```

#### 5. PTY Support

virtme-ng requires a valid pseudo-terminal (PTS). The implementation includes a PTY runner:

```python
def _run_with_pty(cmd: List[str], cwd: Path, timeout: int) -> Tuple[int, str]
```

This creates a pseudo-terminal and runs virtme-ng with proper terminal support.

## Usage Examples

### Python API

```python
from pathlib import Path
from kerneldev_mcp.boot_manager import BootManager

# Initialize boot manager
kernel_path = Path("/path/to/linux")
boot_manager = BootManager(kernel_path)

# Check virtme-ng is available
if not boot_manager.check_virtme_ng():
    print("virtme-ng not installed")
    exit(1)

# Boot test the kernel
result = boot_manager.boot_test(
    timeout=120,
    memory="4G",
    cpus=4
)

# Check results
if result.has_critical_issues:
    print(f"CRITICAL: {result.panic_count} panics, {result.oops_count} oops")
elif result.error_count > 0:
    print(f"Errors found: {result.error_count}")
elif result.warning_count > 0:
    print(f"Boot OK with {result.warning_count} warnings")
else:
    print("Clean boot - no issues detected!")

# Print summary
print(result.summary())

# Access detailed information
for panic in result.panics:
    print(f"PANIC: {panic}")

for error in result.errors:
    print(f"ERROR: {error}")
```

### Using Host Kernel

If the local kernel isn't built, use the host kernel:

```python
result = boot_manager.boot_test(
    timeout=90,
    memory="2G",
    cpus=2,
    use_host_kernel=True
)
```

### Cross-Compilation Boot Testing

Test cross-compiled kernels:

```python
from kerneldev_mcp.config_manager import CrossCompileConfig

cross = CrossCompileConfig(arch="arm64")
result = boot_manager.boot_test(
    timeout=120,
    cross_compile=cross
)
```

### MCP Tools

#### boot_kernel_test

Boot and validate a kernel:

```json
{
  "name": "boot_kernel_test",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "timeout": 120,
    "memory": "4G",
    "cpus": 4
  }
}
```

**With Host Kernel:**
```json
{
  "name": "boot_kernel_test",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "use_host_kernel": true,
    "timeout": 90
  }
}
```

**Cross-Compilation:**
```json
{
  "name": "boot_kernel_test",
  "arguments": {
    "kernel_path": "/home/user/linux",
    "cross_compile_arch": "arm64",
    "timeout": 150
  }
}
```

#### check_virtme_ng

Check if virtme-ng is available:

```json
{
  "name": "check_virtme_ng",
  "arguments": {}
}
```

## Testing

### Unit Tests

The implementation includes 22 comprehensive unit tests in `tests/test_boot_manager.py`:

**Dmesg Parsing Tests:**
- Simple line parsing
- Log level detection
- Subsystem extraction
- Error detection
- Warning detection
- Panic detection
- Oops detection

**Analysis Tests:**
- Clean boot analysis
- Boot with errors
- Boot with panics
- Boot with oops

**BootResult Tests:**
- Property calculations
- Critical issue detection
- Summary generation

**Run Tests:**
```bash
pytest tests/test_boot_manager.py -v
```

All tests pass successfully.

### Integration Test

`test_kernel_boot.py` provides end-to-end validation:

```bash
python3 test_kernel_boot.py
```

This test:
1. Checks virtme-ng availability
2. Detects if kernel is built
3. Falls back to host kernel if needed
4. Runs boot test with timeout
5. Analyzes and reports results

## Requirements

### Essential

- **virtme-ng**: Install with `pip install virtme-ng` or `dnf install virtme-ng`
- **busybox**: Required for initramfs (`dnf install busybox`)
- **QEMU/KVM**: For virtualization
- **Python 3.8+**: With pty, select modules

### Optional

- **virtiofsd**: For faster filesystem operations
- **Cross-compilers**: For cross-architecture testing

### Installation

```bash
# Install virtme-ng
pip install virtme-ng

# Install dependencies (Fedora)
sudo dnf install qemu-kvm busybox virtiofsd

# Install dependencies (Ubuntu)
sudo apt install qemu-kvm busybox-static virtiofsd
```

## Configuration

### Timeout Settings

Boot timeouts depend on:
- System performance
- Kernel configuration
- VM resources

**Recommended timeouts:**
- **Minimal kernel**: 30-60 seconds
- **Standard kernel**: 60-120 seconds
- **Debug kernel**: 120-300 seconds

### Memory and CPU

**Recommended resources:**
- **Memory**: 2-4GB for most kernels
- **CPUs**: 2-4 cores for good performance
- **Increase for**:
  - Large kernels with many modules
  - Debug kernels with KASAN/KCOV
  - Stress testing scenarios

## Limitations and Considerations

### Known Limitations

1. **PTY Requirement**: virtme-ng requires a valid pseudo-terminal
2. **Boot Time**: First boot may be slow due to initialization
3. **Resource Usage**: Requires sufficient RAM and CPU
4. **KVM Access**: Best performance with KVM virtualization

### When Boot Testing May Fail

- Kernel not configured or built
- Missing dependencies (busybox, virtiofsd)
- Insufficient resources (memory, timeout)
- KVM not available (falls back to slower emulation)
- Invalid kernel configuration

### Troubleshooting

**virtme-ng not found:**
```bash
pip install virtme-ng
```

**busybox not found:**
```bash
sudo dnf install busybox  # Fedora
sudo apt install busybox-static  # Ubuntu
```

**Boot timeout:**
- Increase timeout parameter
- Reduce memory/CPU usage
- Check kernel configuration
- Use minimal kernel config for testing

**PTY errors:**
- The implementation includes PTY support
- Should work from Python subprocess
- If issues persist, run in tmux/screen

## Dmesg Pattern Detection

### Panic Patterns

```python
PANIC_PATTERNS = [
    re.compile(r"Kernel panic", re.IGNORECASE),
    re.compile(r"BUG: unable to handle", re.IGNORECASE),
    re.compile(r"general protection fault", re.IGNORECASE),
]
```

### Oops Patterns

```python
OOPS_PATTERNS = [
    re.compile(r"BUG:", re.IGNORECASE),
    re.compile(r"Oops:", re.IGNORECASE),
    re.compile(r"unable to handle kernel", re.IGNORECASE),
]
```

### Error Patterns

```python
ERROR_PATTERNS = [
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bfailure\b", re.IGNORECASE),
]
```

### Warning Patterns

```python
WARNING_PATTERNS = [
    re.compile(r"\bwarning\b", re.IGNORECASE),
    re.compile(r"\bWARN", re.IGNORECASE),
]
```

## Best Practices

1. **Build Before Testing**: Ensure kernel is built (`vmlinux` exists)
2. **Use Appropriate Timeouts**: Allow enough time for boot
3. **Check Critical Issues First**: Panics/oops indicate serious problems
4. **Monitor Warnings**: May indicate configuration issues
5. **Test Incrementally**: Test after significant changes
6. **Use Host Kernel for Quick Tests**: Faster than building
7. **Increase Resources for Debug Kernels**: KASAN/UBSAN need more memory

## Integration with Other Features

### With Build System

```python
from kerneldev_mcp.build_manager import KernelBuilder
from kerneldev_mcp.boot_manager import BootManager

# Build kernel
builder = KernelBuilder(kernel_path)
build_result = builder.build(jobs=8)

if build_result.success:
    # Boot test the built kernel
    boot_manager = BootManager(kernel_path)
    boot_result = boot_manager.boot_test()

    if boot_result.has_critical_issues:
        print("Build succeeded but kernel has boot issues!")
```

### With Cross-Compilation

```python
from kerneldev_mcp.config_manager import CrossCompileConfig

cross = CrossCompileConfig(arch="arm64")

# Build for ARM64
build_result = builder.build(cross_compile=cross, target="Image")

# Boot test (requires QEMU ARM64 support)
boot_result = boot_manager.boot_test(cross_compile=cross)
```

## Files

**Implementation:**
- `src/kerneldev_mcp/boot_manager.py` - Main implementation (500+ lines)

**Tests:**
- `tests/test_boot_manager.py` - Unit tests (22 tests)
- `test_kernel_boot.py` - Integration test

**Documentation:**
- `BOOT_TESTING.md` - This file

## Summary

The boot testing feature enables:
- Automated kernel boot validation
- Dmesg capture and intelligent parsing
- Error/warning/panic/oops detection
- Configurable VM resources
- Cross-architecture support (via virtme-ng)
- Integration with build and configuration systems

This significantly improves the kernel development workflow by catching boot-time issues early and providing detailed diagnostic information about kernel initialization problems.
