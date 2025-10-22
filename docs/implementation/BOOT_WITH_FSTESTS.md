# boot_kernel_with_fstests Implementation

## Overview

The `boot_kernel_with_fstests` tool boots a custom kernel in a VM using virtme-ng and runs fstests inside the VM, capturing both kernel boot status and test results.

**Key Feature**: Automatic device setup inside the VM - no root access required on the host!

## How It Works

### 1. Validation Phase

The tool validates:
- ✅ virtme-ng is installed (`vng --version`)
- ✅ Kernel is built (`vmlinux` exists)
- ✅ fstests is installed and fully built (`check` script and critical binaries exist)

### 2. Script Generation

Creates a comprehensive bash script that runs inside the VM:
```bash
#!/bin/bash
set +e

# 1. Create loop device backing files in /tmp (512MB each)
dd if=/dev/zero of=/tmp/test.img bs=1M count=512
dd if=/dev/zero of=/tmp/scratch.img bs=1M count=512

# 2. Setup loop devices
TEST_DEV=$(losetup -f --show /tmp/test.img)
SCRATCH_DEV=$(losetup -f --show /tmp/scratch.img)

# 3. Format filesystems (auto-detected: ext4 for generic tests, btrfs for btrfs tests)
mkfs.ext4 -F $TEST_DEV
mkfs.ext4 -F $SCRATCH_DEV

# 4. Create mount points in /tmp (writable by everyone)
mkdir -p /tmp/test /tmp/scratch

# 5. Create fstests local.config with auto-detected devices
cat > /path/to/fstests/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_DEV=$SCRATCH_DEV
export SCRATCH_MNT=/tmp/scratch
export FSTYP=ext4
EOF

# 6. Run tests
cd /path/to/fstests
./check -g quick

# 7. Cleanup loop devices and images
umount /tmp/test /tmp/scratch
losetup -d $TEST_DEV $SCRATCH_DEV
rm -f /tmp/test.img /tmp/scratch.img
```

### 3. VM Boot with virtme-ng

Builds a `vng` command:
```bash
vng --verbose \
    --memory 4G \
    --cpus 4 \
    --rwdir /path/to/fstests \
    -- bash /tmp/run-fstests.sh
```

**Key virtme-ng options:**
- `--verbose`: Capture full console output for parsing
- `--memory 4G`: Allocate memory for VM
- `--cpus 4`: Number of CPUs
- `--rwdir /path/to/fstests`: Share fstests directory with VM (read-write)
- `-- bash /tmp/run-fstests.sh`: Execute the test script

### 4. Result Capture

The implementation:
1. **Captures console output** from virtme-ng (includes kernel boot + fstests output)
2. **Parses fstests results** using `FstestsManager.parse_check_output()`
3. **Analyzes kernel messages** using `DmesgParser.analyze_dmesg()`
4. **Stores results** in `BootResult` and `FstestsRunResult`

### 5. Output Formatting

Returns combined information:
- Kernel boot status (success/failure, panics, oops)
- Test pass/fail counts
- Detailed failure information
- Boot log file location

## Usage Example

```python
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "/home/user/linux",
    "fstests_path": "/home/user/.kerneldev-mcp/fstests",
    "tests": ["-g", "quick"],
    "timeout": 300,
    "memory": "4G",
    "cpus": 4
  }
}
```

## Parameters

### Required
- `kernel_path`: Path to kernel source directory (must contain built vmlinux)
- `fstests_path`: Path to fstests installation

### Optional
- `tests`: Array of test arguments (default: `["-g", "quick"]`)
  - Examples:
    - `["-g", "quick"]` - Quick smoke tests
    - `["-g", "auto"]` - Full auto test suite
    - `["generic/001", "generic/002"]` - Specific tests
- `timeout`: Total timeout in seconds (default: 300)
- `memory`: VM memory size (default: "4G")
- `cpus`: Number of CPUs (default: 4)

## Return Values

### BootResult

Contains kernel boot analysis:
```python
BootResult(
    success=True/False,           # Overall success
    duration=45.2,                 # Time in seconds
    boot_completed=True/False,     # Did kernel boot?
    kernel_version="6.12.0-rc1",  # Detected kernel version
    errors=[...],                  # Kernel errors from dmesg
    warnings=[...],                # Kernel warnings
    panics=[...],                  # Kernel panics detected
    oops=[...],                    # Oops detected
    dmesg_output="...",           # Full console output
    exit_code=0,                   # VM exit code
    timeout_occurred=False,        # Did it timeout?
    log_file_path=Path(...)       # Saved boot log
)
```

### FstestsRunResult

Contains test execution results:
```python
FstestsRunResult(
    success=True/False,           # All tests passed?
    total_tests=50,               # Number of tests run
    passed=45,                    # Tests that passed
    failed=3,                     # Tests that failed
    notrun=2,                     # Tests skipped
    test_results=[...],           # Individual test results
    duration=120.5,               # Test duration
    check_log=Path(...)          # fstests check.log
)
```

## Implementation Details

### Automatic Device Setup Inside VM

The tool automatically sets up test devices **inside the VM**, eliminating the need for root access on the host:

1. **Loop Device Creation**:
   - Creates 512MB backing files in `/tmp/test.img` and `/tmp/scratch.img`
   - Uses `losetup -f --show` to attach them to loop devices
   - No host-side configuration required

2. **Filesystem Detection**:
   - Automatically detects filesystem type from test names
   - If test path contains "btrfs", uses `mkfs.btrfs`
   - Otherwise defaults to `mkfs.ext4`
   - Can be extended to support xfs, f2fs, etc.

3. **Mount Point Setup**:
   - Creates mount points in `/tmp/test` and `/tmp/scratch`
   - These are writable by everyone, no permission issues
   - Automatically cleaned up after tests complete

4. **Configuration Generation**:
   - Creates `local.config` with auto-detected devices
   - All paths are in `/tmp`, ensuring writability
   - No manual configuration required by user

### Directory Sharing

Uses virtme-ng's `--rwdir` to share the fstests directory:
- fstests directory is mounted **read-write** in the VM
- Changes to `results/` directory are visible on the host
- Test files can be accessed by fstests inside VM

### Script Execution

The test script is written to `/tmp/run-fstests.sh` on the **host** and executed inside the VM:
- Script is executable (`chmod 0o755`)
- Cleaned up after execution (in `finally` block)
- Uses absolute path to fstests directory

### Exit Code Handling

```python
# Exit code 0 = all tests passed
# Exit code 1 = some tests failed (but completed)
# Both are considered "boot success" if no kernel panics
boot_success = (exit_code == 0 or exit_code == 1) and len(panics) == 0
```

### Result Storage

Stores the last fstests result in `BootManager._last_fstests_result` for potential future use with comparison tools.

## Error Handling

### virtme-ng Not Found
```
ERROR: virtme-ng (vng) not found. Install with: pip install virtme-ng
```

### Kernel Not Built
```
ERROR: Kernel not built. vmlinux not found at /path/to/linux/vmlinux
```

### fstests Not Found
```
ERROR: fstests not found at /path/to/fstests
```

### Timeout
```
ERROR: Test timed out after 300s
```

All errors include:
- Clear error message
- Saved boot log (even for partial output)
- Proper `BootResult` with `success=False`

## Output Format

```
=== Kernel Boot with fstests ===

✓ Boot successful, no issues detected (45.2s)

Full boot log: /tmp/kerneldev-boot-logs/boot-20250122-143022-success.log

=== fstests Results ===

✓ 45/50 passed (90.0% pass rate, 120.5s)

Failed Tests (3):
  1. generic/003
     output mismatch (see generic/003.out.bad)
  2. generic/010
     timeout
  3. generic/025
     kernel oops

Not Run (2):
  1. generic/002
     requires feature XYZ
  2. generic/015
     test device too small

Full results: /path/to/fstests/results/check.log
```

## Integration with Baseline Workflow

While `boot_kernel_with_fstests` doesn't directly save baselines, the results can be used with baseline tools:

1. **Run baseline on upstream kernel:**
```python
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "/home/user/linux",
    "fstests_path": "/home/user/.kerneldev-mcp/fstests",
    "tests": ["-g", "auto"]
  }
}
# Manually save results as baseline
```

2. **Run with your changes:**
```python
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "/home/user/linux",
    "fstests_path": "/home/user/.kerneldev-mcp/fstests",
    "tests": ["-g", "auto"]
  }
}
```

3. **Compare results** (manually or with future comparison tool)

## Advantages Over Direct fstests Execution

### 1. **Isolated Environment**
- Tests run in VM, cannot damage host system
- Fresh environment for each run
- No leftover state between runs

### 2. **Kernel Analysis**
- Detects kernel panics/oops automatically
- Full dmesg analysis
- Boot success validation

### 3. **Combined Results**
- Single command for boot + test
- Unified output format
- Correlation between kernel issues and test failures

### 4. **Reproducibility**
- Consistent VM environment
- Same resources (CPU, memory) each time
- No host system dependencies

## Limitations

### 1. **Device Limitations**
- Currently relies on fstests being pre-configured with devices
- Does not automatically setup test/scratch devices in VM
- User must configure `local.config` before running

### 2. **Timeout Handling**
- Single timeout for entire operation (boot + tests)
- Long test runs may timeout
- No partial result recovery after timeout

### 3. **Resource Constraints**
- VM memory/CPU is fixed for entire run
- Cannot dynamically adjust resources
- Large test suites may need more resources

## Future Enhancements

### Planned Improvements

1. **Automatic Device Setup**
```python
# Future: Create loop devices inside VM automatically
boot_with_fstests(
    ...,
    auto_setup_devices=True,
    test_device_size="10G",
    scratch_device_size="10G"
)
```

2. **Baseline Integration**
```python
# Future: Direct baseline saving
boot_with_fstests(
    ...,
    save_baseline=True,
    baseline_name="upstream-master"
)
```

3. **Streaming Output**
```python
# Future: Stream test progress in real-time
boot_with_fstests(
    ...,
    stream_output=True  # Print tests as they run
)
```

4. **Multi-Config Testing**
```python
# Future: Test multiple filesystem configs
boot_with_fstests(
    ...,
    configs=["ext4-4k", "ext4-1k", "btrfs"]
)
```

## Troubleshooting

### Tests Not Found in VM

**Problem:** fstests directory not accessible in VM

**Solution:** Ensure `--rwdir` path is correct and fstests directory exists

### Permission Denied

**Problem:** Cannot execute `sudo ./check` in VM

**Solution:** virtme-ng runs as root by default, this should work. Check fstests installation.

### Timeout Too Short

**Problem:** Tests timeout before completion

**Solution:** Increase timeout parameter:
```python
{
  "timeout": 600  # 10 minutes
}
```

### Out of Memory in VM

**Problem:** VM runs out of memory during tests

**Solution:** Increase memory allocation:
```python
{
  "memory": "8G"
}
```

## Testing the Implementation

### Basic Test
```bash
# 1. Build a kernel
cd ~/linux
make defconfig
make -j$(nproc)

# 2. Install fstests
vng -- "cd /path/to/fstests && make"

# 3. Run via MCP
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "~/linux",
    "fstests_path": "~/.kerneldev-mcp/fstests",
    "tests": ["-g", "quick"],
    "timeout": 120
  }
}
```

### Expected Output
- Kernel boots successfully
- fstests runs
- Results show passed/failed/notrun counts
- Log files are saved

## Summary

The `boot_kernel_with_fstests` implementation provides:

✅ **Complete integration** of kernel boot testing + fstests
✅ **Isolation** through virtme-ng virtualization
✅ **Comprehensive results** combining boot analysis and test results
✅ **Error handling** for common failure scenarios
✅ **Logging** of all output for debugging

This makes it easy to validate filesystem changes with a single command while ensuring kernel stability.
