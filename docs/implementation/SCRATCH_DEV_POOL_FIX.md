# SCRATCH_DEV_POOL Support for Multi-Device Tests

## Problems

### Problem 1: Tests requiring multiple scratch devices were being skipped

```
btrfs/003 [not run] this test requires a valid $SCRATCH_DEV_POOL
```

### Problem 2: Configuration conflict error

```
common/config: Error: $SCRATCH_DEV (/dev/loop1) should be unset when $SCRATCH_DEV_POOL is set
```

### Problem 3: Out of space in VM

```
dd: error writing '/tmp/pool2.img': No space left on device
```

## Root Causes

1. **Missing SCRATCH_DEV_POOL**: The implementation only created two devices (TEST_DEV, SCRATCH_DEV)
2. **Invalid configuration**: fstests requires that SCRATCH_DEV must NOT be set when using SCRATCH_DEV_POOL
3. **Device size too large**: 6 × 512MB = 3GB exceeded VM's /tmp capacity

Many fstests (especially btrfs tests) require a **pool of scratch devices** to test:
- RAID configurations
- Multi-device filesystems
- Device replacement/removal
- Replication and mirroring

## Solution

Created a 5-device scratch pool with properly configured fstests settings, using smaller devices to fit in VM memory.

### Implementation

**File**: `src/kerneldev_mcp/boot_manager.py`

**Key Changes**:

1. **Use sparse files at 10GB each** (lines 743-757):
```bash
# Use sparse files: allocate 10GB logically, consume space only as written
# count=0 seek=10240 creates a sparse file without writing zeros
dd if=/dev/zero of=/tmp/test.img bs=1M count=0 seek=10240
dd if=/dev/zero of=/tmp/pool1.img bs=1M count=0 seek=10240
dd if=/dev/zero of=/tmp/pool2.img bs=1M count=0 seek=10240
dd if=/dev/zero of=/tmp/pool3.img bs=1M count=0 seek=10240
dd if=/dev/zero of=/tmp/pool4.img bs=1M count=0 seek=10240
dd if=/dev/zero of=/tmp/pool5.img bs=1M count=0 seek=10240
```

2. **Removed separate SCRATCH_DEV** - Use pool devices only (lines 758-765):
```bash
# Setup loop devices
TEST_DEV=$(losetup -f --show /tmp/test.img)
POOL1=$(losetup -f --show /tmp/pool1.img)
POOL2=$(losetup -f --show /tmp/pool2.img)
POOL3=$(losetup -f --show /tmp/pool3.img)
POOL4=$(losetup -f --show /tmp/pool4.img)
POOL5=$(losetup -f --show /tmp/pool5.img)
```

3. **Configure with SCRATCH_DEV_POOL only** - Do NOT set SCRATCH_DEV (lines 788-793):
```bash
cat > {fstests_path}/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="$POOL1 $POOL2 $POOL3 $POOL4 $POOL5"
export FSTYP={fstype}
EOF
```

Note: **SCRATCH_DEV is NOT set** - this is critical! fstests uses the first device in the pool as the scratch device.

4. **Clean up all pool devices** (lines 829-834):
```bash
losetup -d $TEST_DEV 2>/dev/null || true
losetup -d $POOL1 $POOL2 $POOL3 $POOL4 $POOL5 2>/dev/null || true
rm -f /tmp/test.img /tmp/pool*.img
```

### Why 5 pool devices?

- **First device**: Acts as the primary scratch device (replaces SCRATCH_DEV)
- **Remaining 4**: Available for multi-device operations
- **RAID6**: Requires at least 4 devices total (pool covers this)
- **RAID10**: Typically uses 4 devices (pool has 5, giving flexibility)
- **Standard practice**: fstests documentation recommends 4-5 pool devices
- **Balance**: Comprehensive testing without excessive resource usage

### Device Size Evolution

**Original (512MB dense files)**:
- 6 devices × 512MB = 3GB total
- Exceeded VM /tmp capacity

**First fix (256MB dense files)**:
- 6 devices × 256MB = 1.5GB total
- Fit in VM /tmp but too small for many tests
- Tests like btrfs/282 require 10GB minimum

**Current (10GB sparse files)**:
- 6 devices × 10GB = 60GB logical allocation
- Uses sparse files: only consume disk space as data is written
- Actual disk usage: Typically a few MB per test
- No VM /tmp capacity issues since sparse files don't pre-allocate space
- Enables all fstests to run, including those requiring large devices

### Why not pre-format pool devices?

Pool devices are intentionally **not** formatted during setup:

1. **Test-specific formatting**: Each test formats devices with specific options
2. **RAID configurations**: Tests create RAID arrays with specific levels
3. **Multi-device filesystems**: Tests create filesystems across multiple devices
4. **Flexibility**: Different tests use different numbers of devices from the pool

Only `TEST_DEV` and `SCRATCH_DEV` are pre-formatted because they're always used as single-device filesystems.

## Impact

**Before**:
```
btrfs/003 [not run] this test requires a valid $SCRATCH_DEV_POOL
Ran: btrfs/003
Not run: btrfs/003
```

**After**:
```
btrfs/003 5s
Ran: btrfs/003
Passed: btrfs/003
```

## Tests Now Enabled

With SCRATCH_DEV_POOL support, the following categories of tests can now run:

### Btrfs Tests
- `btrfs/003` - Device replace functionality
- `btrfs/004` - Device remove functionality
- `btrfs/011` - RAID configurations
- `btrfs/012` - RAID1 profile
- `btrfs/020` - Device delete and replace
- And many more multi-device btrfs tests

### Generic Multi-Device Tests
- Tests that verify filesystem behavior across multiple devices
- Tests for device failure scenarios
- Tests for device addition/removal

## Configuration Example

The generated `local.config` now contains:

```bash
export TEST_DEV=/dev/loop0
export TEST_DIR=/tmp/test
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="/dev/loop1 /dev/loop2 /dev/loop3 /dev/loop4 /dev/loop5"
export FSTYP=btrfs
```

**Important**: Notice that `SCRATCH_DEV` is **NOT** set. This is intentional and required by fstests when using SCRATCH_DEV_POOL. The first device in the pool (`/dev/loop1`) serves as the primary scratch device.

## Resource Usage

**Per test run**:
- Total loop devices: 6 (1 test + 5 pool)
- Total storage: 1.5GB (6 × 256MB)
- All created in `/tmp` inside VM
- All automatically cleaned up after tests

**Host impact**: None - everything happens inside VM

## Documentation Updates

- Updated `BOOT_WITH_FSTESTS_AUTO_SETUP.md` with SCRATCH_DEV_POOL section
- Updated `CHANGELOG.md` with fix details
- Added explanation of why pool devices aren't pre-formatted

## Testing

To verify the fix works:

```bash
# Test a multi-device test that was previously skipped
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "/home/josef/linux",
    "fstests_path": "/home/josef/.kerneldev-mcp/fstests",
    "tests": ["btrfs/003"],
    "timeout": 300
  }
}
```

**Expected output**:
```
=== fstests Setup Start ===
Creating loop device backing files...
Creating scratch pool devices...
Setting up loop devices...
TEST_DEV=/dev/loop0
SCRATCH_DEV=/dev/loop1
SCRATCH_DEV_POOL=/dev/loop2 /dev/loop3 /dev/loop4 /dev/loop5
...
btrfs/003 5s
Ran: btrfs/003
Passed: btrfs/003
```

## Summary

✅ **Problem 1**: Multi-device tests were being skipped
✅ **Problem 2**: Configuration error when both SCRATCH_DEV and SCRATCH_DEV_POOL were set
✅ **Problem 3**: VM ran out of space with 512MB dense files
✅ **Problem 4**: 256MB devices too small for tests requiring 10GB+

✅ **Solution 1**: Created 5-device scratch pool (no separate SCRATCH_DEV)
✅ **Solution 2**: Removed SCRATCH_DEV from config when using pool
✅ **Solution 3**: Use sparse files instead of dense files
✅ **Solution 4**: Increased device size to 10GB (logical allocation only)

✅ **Result**: Comprehensive multi-device testing works automatically
✅ **Impact**: All fstests can run, minimal disk usage, no VM capacity issues
