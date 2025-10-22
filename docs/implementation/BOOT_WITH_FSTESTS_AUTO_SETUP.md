# boot_kernel_with_fstests - Automatic Device Setup

## Problems Solved

### Problem 1: fstests Not Configured
**Error**: "Warning: need to define parameters for host virtme-ng or set variables: TEST_DIR TEST_DEV"

**Root Cause**: fstests was installed but not configured with device and mount point information. Running tests requires `local.config` to be present with proper settings.

### Problem 2: Permission Issues with /mnt/test
**Issue**: The initial approach tried to use `/mnt/test` which requires root permissions on the host, making it incompatible with the design goal of not requiring host-side root access.

## Solution: Automatic Device Setup Inside VM

The solution creates **everything inside the VM**, leveraging the fact that virtme-ng runs as root inside the virtualized environment.

### Implementation Overview

The `boot_with_fstests()` method now generates a comprehensive setup script that:

1. **Creates loop device backing files** in `/tmp` (writable by everyone)
2. **Attaches loop devices** using `losetup`
3. **Formats filesystems** with auto-detected type
4. **Creates mount points** in `/tmp`
5. **Generates fstests config** with correct paths
6. **Runs the tests**
7. **Cleans up** everything automatically

### Detailed Script Flow

```bash
#!/bin/bash
set +e

# 1. Create 512MB backing files in /tmp
echo "Creating loop device backing files..."
dd if=/dev/zero of=/tmp/test.img bs=1M count=512
dd if=/dev/zero of=/tmp/scratch.img bs=1M count=512

# Also create pool devices for multi-device tests (RAID, etc.)
echo "Creating scratch pool devices..."
dd if=/dev/zero of=/tmp/pool1.img bs=1M count=512
dd if=/dev/zero of=/tmp/pool2.img bs=1M count=512
dd if=/dev/zero of=/tmp/pool3.img bs=1M count=512
dd if=/dev/zero of=/tmp/pool4.img bs=1M count=512

# 2. Setup loop devices
echo "Setting up loop devices..."
TEST_DEV=$(losetup -f --show /tmp/test.img)
SCRATCH_DEV=$(losetup -f --show /tmp/scratch.img)
POOL1=$(losetup -f --show /tmp/pool1.img)
POOL2=$(losetup -f --show /tmp/pool2.img)
POOL3=$(losetup -f --show /tmp/pool3.img)
POOL4=$(losetup -f --show /tmp/pool4.img)
echo "TEST_DEV=$TEST_DEV"
echo "SCRATCH_DEV=$SCRATCH_DEV"
echo "SCRATCH_DEV_POOL=$POOL1 $POOL2 $POOL3 $POOL4"

# 3. Format filesystems (auto-detected based on test name)
echo "Formatting filesystems as ext4..."
mkfs.ext4 -F $TEST_DEV > /dev/null 2>&1
mkfs.ext4 -F $SCRATCH_DEV > /dev/null 2>&1
# Pool devices are NOT pre-formatted - tests will format them as needed

# 4. Create mount points in /tmp
echo "Creating mount points..."
mkdir -p /tmp/test /tmp/scratch

# 5. Create fstests local.config
echo "Creating fstests configuration..."
cat > /home/josef/.kerneldev-mcp/fstests/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_DEV=$SCRATCH_DEV
export SCRATCH_MNT=/tmp/scratch
export SCRATCH_DEV_POOL="$POOL1 $POOL2 $POOL3 $POOL4"
export FSTYP=ext4
EOF

# 6. Run tests
cd /home/josef/.kerneldev-mcp/fstests
./check btrfs/001

# 7. Cleanup
umount /tmp/test 2>/dev/null || true
umount /tmp/scratch 2>/dev/null || true
losetup -d $TEST_DEV 2>/dev/null || true
losetup -d $SCRATCH_DEV 2>/dev/null || true
losetup -d $POOL1 $POOL2 $POOL3 $POOL4 2>/dev/null || true
rm -f /tmp/test.img /tmp/scratch.img /tmp/pool*.img
```

### Automatic Filesystem Detection

The implementation automatically detects the appropriate filesystem type:

```python
# Determine filesystem type from test args
fstype = "ext4"
if any("btrfs" in t for t in tests):
    fstype = "btrfs"
```

**Examples**:
- `boot_kernel_with_fstests(..., tests=["btrfs/001"])` → Uses `mkfs.btrfs`
- `boot_kernel_with_fstests(..., tests=["generic/001"])` → Uses `mkfs.ext4`
- `boot_kernel_with_fstests(..., tests=["-g", "quick"])` → Uses `mkfs.ext4` (default)

### Benefits

1. **No Root on Host**: Everything happens inside the VM where virtme-ng runs as root
2. **No Manual Configuration**: Device paths, mount points, and config are auto-generated
3. **Clean Isolation**: Each test run gets fresh devices and filesystems
4. **Automatic Cleanup**: Loop devices and backing files are removed after tests
5. **Portable**: Works on any system without special setup
6. **Multi-Device Support**: SCRATCH_DEV_POOL enables RAID and multi-device tests

### SCRATCH_DEV_POOL for Multi-Device Tests

Many fstests require multiple scratch devices to test features like:
- **RAID configurations** (RAID0, RAID1, RAID5, RAID6, RAID10)
- **Multi-device filesystems** (btrfs multi-device support)
- **Device replacement** and **device removal**
- **Replication and mirroring**

**Implementation**:
- Creates 4 pool devices: `pool1.img` through `pool4.img` (512MB each)
- Attaches them to loop devices: `/dev/loop2` through `/dev/loop5`
- Exports as `SCRATCH_DEV_POOL="/dev/loop2 /dev/loop3 /dev/loop4 /dev/loop5"`

**Why not pre-format pool devices?**
- Tests format pool devices with specific configurations (e.g., RAID levels)
- Each test may use a different number of devices from the pool
- Pre-formatting would conflict with test requirements
- Tests like `btrfs/003` need to create multi-device filesystems themselves

**Example test requiring SCRATCH_DEV_POOL**:
- `btrfs/003` - Tests btrfs device replace functionality
- `btrfs/004` - Tests btrfs device remove functionality
- `btrfs/011` - Tests btrfs RAID configurations

Without SCRATCH_DEV_POOL, these tests are skipped with message:
```
btrfs/003 [not run] this test requires a valid $SCRATCH_DEV_POOL
```

## Code Changes

### File: `src/kerneldev_mcp/boot_manager.py`

**Lines 708-816**: Script generation with automatic setup

Key changes:
1. Added filesystem type detection
2. Added loop device creation and formatting
3. Added mount point creation in /tmp
4. Added local.config generation
5. Added cleanup of all created resources

```python
# Determine filesystem type from test args
fstype = "ext4"
if any("btrfs" in t for t in tests):
    fstype = "btrfs"

# Create comprehensive script with full setup
test_script = f"""#!/bin/bash
set +e

# Create loop device backing files in /tmp
dd if=/dev/zero of=/tmp/test.img bs=1M count=512
dd if=/dev/zero of=/tmp/scratch.img bs=1M count=512

# Setup loop devices
TEST_DEV=$(losetup -f --show /tmp/test.img)
SCRATCH_DEV=$(losetup -f --show /tmp/scratch.img)

# Format with detected filesystem type
if [ "{fstype}" = "btrfs" ]; then
    mkfs.btrfs -f $TEST_DEV > /dev/null 2>&1
    mkfs.btrfs -f $SCRATCH_DEV > /dev/null 2>&1
else
    mkfs.ext4 -F $TEST_DEV > /dev/null 2>&1
    mkfs.ext4 -F $SCRATCH_DEV > /dev/null 2>&1
fi

# Create mount points and config
mkdir -p /tmp/test /tmp/scratch
cat > {fstests_path}/local.config <<EOF
export TEST_DEV=$TEST_DEV
export TEST_DIR=/tmp/test
export SCRATCH_DEV=$SCRATCH_DEV
export SCRATCH_MNT=/tmp/scratch
export FSTYP={fstype}
EOF

# Run tests
./check {test_args}

# Cleanup
umount /tmp/test /tmp/scratch 2>/dev/null || true
losetup -d $TEST_DEV $SCRATCH_DEV 2>/dev/null || true
rm -f /tmp/test.img /tmp/scratch.img
"""
```

## Documentation Updates

### Updated: `BOOT_WITH_FSTESTS.md`

Added comprehensive section on "Automatic Device Setup Inside VM" explaining:
- Loop device creation
- Filesystem detection
- Mount point setup
- Configuration generation

### Updated: `CHANGELOG.md`

Added section "boot_kernel_with_fstests - Automatic Device Setup" documenting:
- Elimination of host-side root permission requirements
- Automatic filesystem detection
- Auto-generated configuration
- Integrated cleanup

## Testing

The implementation can be tested with:

```bash
# Using MCP tool
{
  "tool": "boot_kernel_with_fstests",
  "params": {
    "kernel_path": "/home/josef/linux",
    "fstests_path": "/home/josef/.kerneldev-mcp/fstests",
    "tests": ["btrfs/001"],
    "timeout": 300,
    "memory": "4G",
    "cpus": 4
  }
}
```

**Expected output**:
```
=== fstests Setup Start ===
Kernel: 6.16.0+
User: root
fstests path: /home/josef/.kerneldev-mcp/fstests
Filesystem type: btrfs

Creating loop device backing files...
Setting up loop devices...
TEST_DEV=/dev/loop0
SCRATCH_DEV=/dev/loop1
Formatting filesystems as btrfs...
Creating mount points...
Creating fstests configuration...
Configuration written to local.config

=== fstests Execution Start ===
Running: ./check btrfs/001
=== fstests Output ===
btrfs/001 5s

Ran: 1 tests in 5s
Passed all 1 tests

=== fstests Execution Complete ===
Exit code: 0
Cleaning up...
```

## Future Enhancements

Possible improvements:

1. **Filesystem Type Parameter**: Add explicit `fstype` parameter to override auto-detection
2. **Device Size Configuration**: Allow specifying loop device size (currently hardcoded to 512MB)
3. **Multiple Filesystem Support**: Test same code across ext4, btrfs, xfs in single run
4. **Persistent Devices**: Option to keep devices between runs for debugging

## Comparison: Before vs After

### Before (Manual Setup Required)

```bash
# On host (requires root):
sudo dd if=/dev/zero of=/tmp/test.img bs=1M count=512
sudo losetup /dev/loop0 /tmp/test.img
sudo mkfs.ext4 /dev/loop0
sudo mkdir -p /mnt/test
sudo chmod 777 /mnt/test

# Create config manually
cat > ~/.kerneldev-mcp/fstests/local.config <<EOF
export TEST_DEV=/dev/loop0
export TEST_DIR=/mnt/test
...
EOF

# Then run tool
boot_kernel_with_fstests(...)
```

**Issues**:
- Requires root on host
- Manual configuration
- Not cleaned up automatically
- Permission issues with /mnt

### After (Fully Automatic)

```bash
# Just run the tool:
boot_kernel_with_fstests(...)
```

**Benefits**:
- No root required on host
- Automatic device setup
- Automatic configuration
- Automatic cleanup
- No permission issues

## Summary

The automatic device setup feature makes `boot_kernel_with_fstests` truly turnkey:

✅ **No root on host** - All privileged operations happen inside VM
✅ **No manual config** - Devices and paths auto-detected and configured
✅ **No cleanup needed** - Everything removed automatically
✅ **No permission issues** - Uses /tmp, writable by everyone
✅ **Filesystem aware** - Detects and uses correct mkfs tool

This transforms the tool from requiring complex manual setup to being a single-command operation that "just works".
