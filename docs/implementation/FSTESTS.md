# Filesystem Testing with fstests

## Overview

This document describes how to use the kerneldev-mcp server's integrated fstests support to test Linux filesystems with proper baseline comparison workflow.

## What is fstests?

fstests (formerly xfstests) is the standard regression test suite for Linux filesystems, providing ~1,500 tests covering all major filesystems. It's **required** for filesystem patch submission to ensure no regressions are introduced.

**Important**: fstests will NOT pass 100% on most systems. The standard workflow is **baseline comparison** - comparing test results before and after your changes to identify actual regressions.

## Kernel Configuration Requirements

### Why Kernel Configuration Matters

Many fstests tests require specific kernel features to be enabled. Running fstests with a minimal or incorrectly configured kernel will result in many tests being skipped or failing. Critical features include:

- **DM_LOG_WRITES** - Required for write-order verification tests
- **Quota support** - Required for quota tests
- **POSIX ACLs** - Required for ACL tests
- **Filesystem encryption** - Required for encryption tests
- **Device mapper** - Required for snapshot and RAID tests
- **Compression support** - Required for compression tests

### Recommended Configuration Templates

kerneldev-mcp provides pre-built configuration templates optimized for filesystem testing:

#### General Filesystem Testing

For testing multiple filesystems (ext4, XFS, BTRFS, F2FS):

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "filesystem",
    "debug_level": "basic"
  }
}
```

This template includes:
- All major filesystems (ext4, XFS, BTRFS, F2FS)
- DM_LOG_WRITES for write-order verification
- Quota support (CONFIG_QUOTA, CONFIG_QFMT_V2)
- POSIX ACLs (CONFIG_FS_POSIX_ACL)
- Encryption (CONFIG_FS_ENCRYPTION) and verity (CONFIG_FS_VERITY)
- Compression support (zlib, lzo, zstd, lz4)
- Device mapper (CONFIG_DM_SNAPSHOT, CONFIG_DM_THIN_PROVISIONING, CONFIG_DM_LOG_WRITES)
- Loop devices (CONFIG_BLK_DEV_LOOP)
- Network filesystems (NFS, CIFS)

#### BTRFS-Specific Testing

For BTRFS development with comprehensive debugging:

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",
    "debug_level": "full_debug"
  }
}
```

This template includes all the above plus:
- BTRFS debugging (CONFIG_BTRFS_DEBUG, CONFIG_BTRFS_ASSERT)
- BTRFS integrity checking (CONFIG_BTRFS_FS_CHECK_INTEGRITY)
- BTRFS sanity tests (CONFIG_BTRFS_FS_RUN_SANITY_TESTS)
- BTRFS reference verification (CONFIG_BTRFS_FS_REF_VERIFY)

#### With Sanitizers (Recommended for Development)

For catching memory corruption and undefined behavior:

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "filesystem",  # or "btrfs"
    "debug_level": "sanitizers"
  }
}
```

This adds:
- KASAN (Kernel Address Sanitizer) for memory corruption detection
- UBSAN (Undefined Behavior Sanitizer)
- KCOV for code coverage (useful with fuzzing)

### Applying Configuration to Kernel

After generating a config, apply it to your kernel:

```python
{
  "tool": "apply_config",
  "params": {
    "kernel_path": "/path/to/linux",
    "config_source": "inline",
    "config_content": "<config from get_config_template>"
  }
}
```

Then build your kernel:

```python
{
  "tool": "build_kernel",
  "params": {
    "kernel_path": "/path/to/linux",
    "jobs": 16
  }
}
```

### Critical CONFIG Options

If you're using a custom configuration, ensure these are enabled:

**Essential for most tests:**
- `CONFIG_BLOCK=y`
- `CONFIG_FILE_LOCKING=y`
- `CONFIG_FS_POSIX_ACL=y`
- `CONFIG_BLK_DEV_LOOP=y`

**Required for device mapper tests:**
- `CONFIG_MD=y` - Multiple device support
- `CONFIG_BLK_DEV_DM=y` - Device mapper core
- `CONFIG_DM_LOG_WRITES=y` - Write-order verification
- `CONFIG_DM_SNAPSHOT=m` - Snapshot tests
- `CONFIG_DM_THIN_PROVISIONING=m` - Thin provisioning tests

**High-value device mapper tests (90+ tests):**
- `CONFIG_DM_FLAKEY=m` - Error injection and failure simulation
- `CONFIG_DM_DUST=m` - Bad sector simulation

**Required for quota tests:**
- `CONFIG_QUOTA=y`
- `CONFIG_QUOTACTL=y`
- `CONFIG_QFMT_V2=y`

**Required for encryption tests:**
- `CONFIG_FS_ENCRYPTION=y`
- `CONFIG_CRYPTO_AES=y`

**Fault injection tests (10+ tests):**
- `CONFIG_FAULT_INJECTION=y` - Fault injection framework
- `CONFIG_FAIL_MAKE_REQUEST=y` - Block layer fault injection
- `CONFIG_SCSI_DEBUG=m` - SCSI debug driver for device testing
- `CONFIG_TRANSPARENT_HUGEPAGE=y` - Transparent huge page tests

### Verifying Your Configuration

Use the comprehensive environment check tool:

```python
{
  "tool": "fstests_check_environment",
  "params": {
    "kernel_path": "/path/to/linux",
    "fstests_path": "~/.kerneldev-mcp/fstests",
    "check_kernel_config": true
  }
}
```

This will verify:
- Kernel configuration has required options
- fstests is installed and built
- Devices are configured (if local.config exists)
- Runtime dependencies (fsverity-utils, duperemove) are installed
- virtme-ng is available for VM testing

## Quick Start

### 1. Check if fstests is installed

```python
{
  "tool": "fstests_setup_check"
}
```

### 2. Install fstests (if needed)

```python
{
  "tool": "fstests_setup_install"
}
```

This will clone and build fstests to `~/.kerneldev-mcp/fstests`.

### 3. Setup test devices

**Option A: Automatic loop devices** (recommended for development)

```python
{
  "tool": "fstests_setup_devices",
  "params": {
    "mode": "loop",
    "test_size": "10G",
    "scratch_size": "10G",
    "fstype": "ext4"
  }
}
```

**Option B: Use existing devices**

```python
{
  "tool": "fstests_setup_devices",
  "params": {
    "mode": "existing",
    "test_dev": "/dev/vdb",
    "scratch_dev": "/dev/vdc",
    "fstype": "btrfs"
  }
}
```

### 4. Configure fstests

```python
{
  "tool": "fstests_setup_configure",
  "params": {
    "test_dev": "/dev/loop0",
    "scratch_dev": "/dev/loop1",
    "fstype": "ext4",
    "test_dir": "/mnt/test",
    "scratch_dir": "/mnt/scratch"
  }
}
```

### 5. Run tests and create baseline

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "quick"],
    "save_baseline": true,
    "baseline_name": "upstream-master",
    "kernel_version": "6.12-rc1"
  }
}
```

### 6. Make kernel changes and test

After making your kernel changes, rebuild and run tests again:

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "quick"]
  }
}
```

### 7. Compare against baseline

```python
{
  "tool": "fstests_baseline_compare",
  "params": {
    "baseline_name": "upstream-master"
  }
}
```

## Available Tools

### fstests_setup_check

Check if fstests is installed and get version information.

**Parameters:**
- `fstests_path` (optional): Path to fstests installation

**Returns:**
- Installation status and version

### fstests_setup_install

Clone and build fstests from git.

**Parameters:**
- `install_path` (optional): Where to install (default: `~/.kerneldev-mcp/fstests`)
- `git_url` (optional): Git repository URL (default: kernel.org)

**Returns:**
- Installation status and version

### fstests_setup_devices

Setup test and scratch devices for fstests.

**Parameters:**
- `mode`: "loop" or "existing"
- For loop mode:
  - `test_size`: Size like "10G" (default: "10G")
  - `scratch_size`: Size like "10G" (default: "10G")
- For existing mode:
  - `test_dev`: Path to test device
  - `scratch_dev`: Path to scratch device
- Common:
  - `fstype`: Filesystem type (ext4, btrfs, xfs, f2fs)
  - `mount_options`: Mount options (optional)
  - `mkfs_options`: mkfs options (optional)

**Returns:**
- Device configuration and status

### fstests_setup_configure

Create or update fstests local.config file.

**Parameters:**
- `fstests_path` (optional): Path to fstests
- `test_dev`: Test device path
- `scratch_dev`: Scratch device path
- `fstype`: Filesystem type
- `test_dir`: Test mount point (default: "/mnt/test")
- `scratch_dir`: Scratch mount point (default: "/mnt/scratch")
- `mount_options` (optional): Mount options
- `mkfs_options` (optional): mkfs options

**Returns:**
- Configuration file content and status

### fstests_run

Run fstests and capture results.

**Parameters:**
- `fstests_path` (optional): Path to fstests
- `tests`: Array of tests to run
  - Individual tests: `["generic/001", "generic/002"]`
  - Groups: `["-g", "quick"]`
  - Multiple groups: `["-g", "quick,auto"]`
- `exclude_file` (optional): Path to exclude file
- `randomize` (optional): Randomize test order (default: false)
- `iterations` (optional): Number of times to run (default: 1)
- `timeout` (optional): Timeout in seconds
- `save_baseline` (optional): Save as baseline (default: false)
- `baseline_name` (optional): Name for baseline
- `kernel_version` (optional): Kernel version for metadata

**Returns:**
- Test results with pass/fail/notrun counts
- Detailed failure information

### fstests_groups_list

List available test groups.

**Returns:**
- Dictionary of group names and descriptions

Common groups:
- `auto` - Suitable for automatic testing (excludes dangerous)
- `quick` - Fast smoke tests (~5-10 minutes)
- `all` - All tests including dangerous ones
- `dangerous` - Tests that may crash/hang the kernel
- `stress` - Long-running stress tests
- `aio` - Async I/O tests
- `attr` - Extended attributes
- `acl` - Access control lists
- `quota` - Quota tests
- `encrypt` - Encryption tests
- `compress` - Compression tests

### fstests_baseline_get

Get information about a stored baseline.

**Parameters:**
- `baseline_name`: Name of baseline

**Returns:**
- Baseline metadata and summary

### fstests_baseline_compare

Compare test results against a baseline to detect regressions.

**Parameters:**
- `baseline_name`: Name of baseline to compare against
- `current_results_file` (optional): Path to results file

**Returns:**
- Comparison report showing:
  - New failures (regressions)
  - New passes (improvements)
  - Still failing (pre-existing)
  - Regression status

### fstests_baseline_list

List all stored baselines.

**Returns:**
- List of baselines with metadata

### fstests_vm_boot_and_run

Boot kernel in VM with fstests and run tests (TODO: not yet implemented).

**Parameters:**
- `kernel_path`: Path to kernel source
- `fstests_path`: Path to fstests
- `tests`: Tests to run
- `fstype`: Filesystem type
- `timeout`: Timeout in seconds
- `memory`: VM memory size
- `cpus`: Number of CPUs

## Baseline Comparison Workflow

### Why Baseline Comparison?

fstests will NOT pass 100% on most systems because:
- Tests for features not enabled in your kernel
- Known flaky tests
- Tests for specific hardware you don't have
- Tests exposing known unfixed bugs

The **accepted practice** is baseline comparison:
1. Run tests on upstream kernel (baseline)
2. Apply your changes and rebuild
3. Run same tests
4. Compare - only NEW failures are your problem

### Complete Workflow Example

#### Step 1: Create baseline on upstream kernel

```bash
# Boot upstream kernel
# Then run:
```

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "auto"],
    "save_baseline": true,
    "baseline_name": "upstream-6.12-rc1",
    "kernel_version": "6.12-rc1"
  }
}
```

#### Step 2: Make your changes

```bash
# Apply your patches
# Rebuild kernel
# Reboot into patched kernel
```

#### Step 3: Run same tests

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "auto"]
  }
}
```

#### Step 4: Compare results

```python
{
  "tool": "fstests_baseline_compare",
  "params": {
    "baseline_name": "upstream-6.12-rc1"
  }
}
```

The comparison will show:
- **New failures** (REGRESSIONS) - These are YOUR problem!
- **New passes** (IMPROVEMENTS) - Nice!
- **Still failing** (PRE-EXISTING) - Not your fault
- **Regression status** - Safe to submit or not

### Managing Multiple Baselines

You can maintain separate baselines for:
- Different kernel versions
- Different filesystem configurations
- Different test groups

```python
# List all baselines
{
  "tool": "fstests_baseline_list"
}

# Get specific baseline info
{
  "tool": "fstests_baseline_get",
  "params": {
    "baseline_name": "upstream-6.12-rc1"
  }
}
```

## Testing Different Filesystems

### ext4

```python
{
  "tool": "fstests_setup_devices",
  "params": {
    "mode": "loop",
    "fstype": "ext4",
    "mkfs_options": "-b 4096"
  }
}
```

### btrfs

```python
{
  "tool": "fstests_setup_devices",
  "params": {
    "mode": "loop",
    "fstype": "btrfs",
    "mount_options": "-o compress=zstd"
  }
}
```

### xfs

```python
{
  "tool": "fstests_setup_devices",
  "params": {
    "mode": "loop",
    "fstype": "xfs"
  }
}
```

## Test Selection Strategies

### Quick Smoke Test

For fast iteration during development:

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "quick"]
  }
}
```

### Comprehensive Testing

For pre-submission testing:

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "auto"]
  }
}
```

### Specific Test

To debug a specific failure:

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["generic/001"]
  }
}
```

### Multiple Tests

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["generic/001", "generic/002", "generic/003"]
  }
}
```

### With Exclusions

Use an exclude file to skip known failures:

```python
{
  "tool": "fstests_run",
  "params": {
    "tests": ["-g", "auto"],
    "exclude_file": "/path/to/exclude.txt"
  }
}
```

## Best Practices

### For Kernel Developers

1. **Always use baseline comparison**
   - Never expect 100% pass rate
   - Only new failures are regressions

2. **Test before and after**
   - Baseline on upstream
   - Test with your changes
   - Compare results

3. **Handle flaky tests**
   - Run suspicious tests multiple times
   - If it's flaky in baseline too, it's not your fault

4. **Use appropriate test groups**
   - `quick` for iteration
   - `auto` for pre-submission
   - Avoid `dangerous` unless necessary

### For CI/CD

1. **Cache baselines**
   - Store baseline results
   - Update periodically

2. **Fail on regressions only**
   - Don't fail on pre-existing failures
   - Use baseline comparison

3. **Test multiple configurations**
   - Different filesystems
   - Different mount options
   - Different block sizes

## Troubleshooting

### fstests not installed

```
✗ fstests is not installed at ~/.kerneldev-mcp/fstests
```

**Solution**: Use `fstests_setup_install` tool

### Device setup fails

```
✗ Failed to create test loop device
```

**Solution**: Check you have permissions (may need sudo), or use existing devices

### Tests hang

```
Error: Timeout after 300s
```

**Solution**: Increase timeout parameter, or check for kernel deadlocks in dmesg

### Many test failures

This is normal! Use baseline comparison to identify only the NEW failures.

### Missing runtime dependencies

Some tests require additional tools that may not be installed by default:

```
generic/xxx: [not run] fsverity not installed
generic/yyy: [not run] duperemove not found
```

**Solution**: Install runtime dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get install -y fsverity-utils duperemove
```

**Fedora/RHEL:**
```bash
sudo dnf install -y fsverity-utils duperemove
```

**What these tools do:**
- **fsverity-utils**: Tools for fs-verity (filesystem integrity verification)
  - Required for encryption and verity tests (generic/574, generic/575, etc.)
- **duperemove**: File deduplication tool for btrfs and XFS
  - Required for deduplication tests (generic/505, btrfs/xxx, etc.)

**Note**: The `fstests_setup_install` tool will show these in the dependency hint if the build fails. However, these are runtime dependencies and may need to be installed separately even if fstests builds successfully.

### Required test users

fstests requires specific users for permission and ownership tests. When running tests in VMs via `fstests_vm_boot_and_run` or `fstests_vm_boot_custom`, these users are **automatically created**:
- `fsgqa` - Primary test user
- `fsgqa2` - Secondary test user for multi-user tests

For manual/local fstests runs, create these users before running tests:
```bash
sudo useradd -m fsgqa
sudo useradd -m fsgqa2
```

## Storage and Cleanup

### Baseline Storage

Baselines are stored in:
```
~/.kerneldev-mcp/fstests-baselines/
├── baseline-name-1/
│   ├── baseline.json
│   └── check.log
└── baseline-name-2/
    └── ...
```

### Loop Device Cleanup

When using loop devices, cleanup is needed:

```bash
# Unmount
sudo umount /mnt/test
sudo umount /mnt/scratch

# Detach loop devices
sudo losetup -d /dev/loop0
sudo losetup -d /dev/loop1

# Remove image files
rm /tmp/kerneldev-fstests/*.img
```

## Integration with Kernel Development Workflow

### Typical Development Flow

1. **Setup** (one time)
   ```
   - Install fstests
   - Setup devices
   - Configure fstests
   ```

2. **Create baseline** (per kernel version)
   ```
   - Boot upstream kernel
   - Run tests with save_baseline=true
   ```

3. **Development iteration**
   ```
   - Make changes
   - Rebuild kernel
   - Run tests
   - Compare against baseline
   - Fix regressions
   - Repeat
   ```

4. **Pre-submission**
   ```
   - Run comprehensive tests (-g auto)
   - Verify no regressions
   - Document any known issues
   ```

## References

- [fstests documentation](https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git)
- [Kernel filesystem testing guide](https://docs.kernel.org/filesystems/)
- [linux-dev-context fstests guide](https://github.com/josefbacik/linux-dev-context/blob/main/subsystems/filesystems/fstests.md)
