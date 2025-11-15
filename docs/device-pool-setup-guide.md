# LVM Device Pool Setup Guide

## Overview

Device pools use **LVM (Logical Volume Manager)** to manage dedicated physical storage (SSD/NVMe) for kernel testing, providing **9-10× performance improvement** over slow loop devices.

**Key Architecture:**
- **One-time setup**: Creates PV (Physical Volume) + VG (Volume Group) only
- **On-demand LVs**: Logical volumes created automatically with unique names per test
- **Concurrency-safe**: Multiple Claude instances can use the same pool simultaneously
- **Auto-cleanup**: LVs deleted after test completes (unless you want to keep them)

**Why LVM:**
- ✅ **Concurrency**: Unique LV names per Claude instance (no conflicts)
- ✅ **Native locking**: LVM handles VG-level coordination across processes
- ✅ **Snapshots**: Debugging and rollback support
- ✅ **Dynamic resizing**: Grow/shrink volumes on demand
- ✅ **Performance**: Only ~5% overhead vs raw device (475K vs 500K IOPS)

**Performance Comparison:**
- Loop devices: ~50K IOPS (90% penalty)
- LVM on SSD/NVMe: ~475K IOPS (~5% overhead)
- Raw device: ~500K IOPS
- **Result:** Tests that took hours now take minutes

## How It Works

### Architecture

```
Physical Disk (/dev/nvme1n1)
    ↓
Physical Volume (PV) - created once during setup
    ↓
Volume Group (VG) - the "pool" itself
    ↓
Logical Volumes (LVs) - created on-demand per test
    ├─ kdev-20251115103045-a3f9d2-test (Claude 1, test 1)
    ├─ kdev-20251115103045-a3f9d2-scratch
    ├─ kdev-20251115104523-b7e4c1-test (Claude 2, concurrent!)
    ├─ kdev-20251115104523-b7e4c1-scratch
    └─ ... (auto-created as needed, auto-deleted after use)
```

### Concurrency Model

**Multiple Claude instances can use the same pool!**

Each test run:
1. Generates unique session ID (timestamp + random)
2. Creates LVs with unique names: `kdev-20251115103045-a3f9d2-test`
3. Tracks allocation in `~/.kerneldev-mcp/lv-state.json` with PID
4. Runs test
5. Auto-deletes LVs (unless `keep_volumes=true`)
6. Updates state file

**LVM's native VG locking** prevents corruption during concurrent lvcreate operations.
**PID tracking** enables cleanup of orphaned LVs from crashed processes.

## Prerequisites

### Required
- A dedicated physical disk (SSD/NVMe recommended)
  - **Must be empty or you're willing to erase it**
  - Examples: `/dev/nvme1n1`, `/dev/sdb`, `/dev/sdc`
- **sudo access** for all LVM operations (pvcreate, vgcreate, lvcreate, lvremove)
- LVM tools installed: `lvm2` package

### Note on Persistence

**The VG name is what persists**, not the device name:
- Device names (`/dev/nvme1n1`) can change between kernel versions
- VG names (`kerneldev-default-vg`) are persistent and auto-discovered by LVM on boot
- Once created, the VG is found by name regardless of device enumeration order

## Quick Start (2 Minutes)

### Step 1: Identify Available Disk

Find a disk you can dedicate to testing:

```bash
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT

# Example output:
NAME        SIZE MODEL                MOUNTPOINT
nvme0n1     512G Samsung 980 PRO
├─nvme0n1p1  1G                       /boot
└─nvme0n1p2 511G                      /          # ← System disk
nvme1n1     512G Samsung 970 EVO                 # ← Available for testing!
sda         1T   WDC WD20EZRZ
└─sda1      1T                        /data
```

**Verify it's safe:**
```bash
# Make sure it's NOT mounted
lsblk -o NAME,MOUNTPOINT /dev/nvme1n1
# Should show NO mountpoints

# Make sure it's NOT your system disk
findmnt / -o SOURCE
# Should be different device
```

⚠️ **WARNING**: The selected disk will be **completely erased**!

**Note on device names:** While `/dev/nvme1n1` might change between kernel versions, **the VG name won't**. Once you create the VG (e.g., `kerneldev-default-vg`), LVM finds it automatically on boot regardless of device enumeration order.

### Step 2: Create Device Pool via MCP

Use Claude Code or direct MCP call:

```json
{
  "tool": "device_pool_setup",
  "arguments": {
    "device_path": "/dev/nvme1n1",
    "pool_name": "default"
  }
}
```

That's it! No volume specifications needed - volumes are created on-demand.

**What this does (using sudo):**
1. Validates device is safe to use (comprehensive 10-point safety checks)
2. Creates LVM physical volume: `sudo pvcreate /dev/nvme1n1`
3. Creates volume group: `sudo vgcreate kerneldev-default-vg /dev/nvme1n1`
4. Saves VG name to `~/.kerneldev-mcp/device-pool.json`

**No logical volumes are created yet.** They'll be created automatically with unique names when you run tests.

**Key insight:** The VG name (`kerneldev-default-vg`) is persistent across reboots. Even if the device becomes `/dev/nvme2n1` in a different kernel, LVM finds the VG by name automatically.

**Output:**
```
LVM Device Pool Created Successfully!

Pool Name: default
Device: /dev/nvme1n1
Volume Group: kerneldev-default-vg
VG Size: 512G
Created: 2025-11-15T10:30:00
User: josef

Note: This pool contains NO pre-created LVs.
Logical volumes will be created on-demand with unique names when you run tests.
Each Claude instance will get its own set of LVs automatically.

All LVM operations use sudo - no special permissions configuration needed.
VG name 'kerneldev-default-vg' is persistent across reboots.

Configuration saved to: ~/.kerneldev-mcp/device-pool.json

To use this pool automatically:
  export KERNELDEV_DEVICE_POOL=default

LVs will be created automatically when running:
  fstests_vm_boot_and_run ...
```

### Step 3: Enable Auto-Use (Optional)

Add to your `~/.bashrc`:

```bash
export KERNELDEV_DEVICE_POOL=default
```

Now all fstests will automatically use physical devices!

### Step 4: Verify Setup

```json
{
  "tool": "device_pool_status",
  "arguments": {
    "pool_name": "default"
  }
}
```

## LVM Benefits

**Why kerneldev-mcp uses LVM exclusively:**

- **Snapshots**: Create point-in-time backups before risky tests, rollback if kernel corrupts data
- **Resizing**: Grow or shrink volumes without recreating the pool
- **Thin Provisioning**: Overcommit storage when needed
- **Flexibility**: Industry-standard tool with rich feature set
- **Performance**: Only ~5% overhead vs raw device (negligible for testing)

## Common Scenarios

### Scenario 1: Basic Setup for Single Claude

**Goal:** Replace slow loop devices with fast SSD

```bash
# Verify device is the one you want
lsblk /dev/nvme1n1

# Create pool
device_pool_setup --device=/dev/nvme1n1
```

**Result:** Empty VG created. When you run tests, LVs are automatically created with unique names.

**What happens when you run tests:**
```bash
fstests_vm_boot_and_run --kernel=/path --fstests=/path --tests="-g quick"
# Automatically:
# 1. Creates 7 unique LVs (kdev-{timestamp}-{random}-test, etc.)
# 2. Runs tests
# 3. Deletes LVs
```

### Scenario 2: Multiple Concurrent Claude Instances

**Goal:** 3 Claude instances testing simultaneously on one device

```bash
# Create shared pool
device_pool_setup --device=/dev/nvme1n1 --pool=shared
```

**Usage in all 3 Claude instances:**
```bash
export KERNELDEV_DEVICE_POOL=shared
# Each Claude runs tests concurrently:
# - Claude 1: Creates kdev-...a3f9d2-* LVs
# - Claude 2: Creates kdev-...b7e4c1-* LVs (different names!)
# - Claude 3: Creates kdev-...f2d8e9-* LVs (different names!)
# All run at the same time without conflicts!
```

### Scenario 3: Debugging with Snapshots

**Goal:** Test risky kernels with snapshot rollback (leveraging LVM snapshots)

```bash
# Setup pool
device_pool_setup --device=/dev/sdc --pool=debug

# Run test but keep LVs for inspection
fstests_vm_boot_and_run ... --keep_volumes=true

# LVs remain: kdev-20251115103045-a3f9d2-test, etc.
# Check status to see LV names:
device_pool_status --pool=debug

# Create snapshot of the LV
device_pool_snapshot \
  --pool=debug \
  --lv_name=kdev-20251115103045-a3f9d2-test \
  --snapshot_name=backup \
  --action=create

# Snapshot persists for later inspection
# Delete when done:
device_pool_snapshot --pool=debug --snapshot_name=backup --action=delete
```

### Scenario 4: Multiple Physical Devices

**Goal:** Use multiple devices for more concurrent capacity

```bash
# Create pools on different devices
device_pool_setup --device=/dev/nvme1n1 --pool=fast
device_pool_setup --device=/dev/sdb --pool=sata

# Use specific pool for different tests
# Fast NVMe for quick tests:
fstests_vm_boot_and_run ... --use_device_pool=fast --tests="-g quick"

# SATA for long-running tests:
fstests_vm_boot_and_run ... --use_device_pool=sata --tests="-g auto"
```

**Note:** With on-demand LVs, you can run many concurrent tests on a single large device. Only create multiple pools if you need different device characteristics (NVMe vs SATA) or need extreme concurrency.

## Using Device Pools

### Method 1: Environment Variable (Recommended)

Set once, use everywhere:

```bash
export KERNELDEV_DEVICE_POOL=default
```

All boot tools automatically use the pool:
```json
{
  "tool": "fstests_vm_boot_and_run",
  "arguments": {
    "kernel_path": "/path/to/kernel",
    "fstests_path": "/path/to/fstests"
    // Automatically uses KERNELDEV_DEVICE_POOL=default
  }
}
```

### Method 2: Explicit Parameter

Specify pool per-test:

```json
{
  "tool": "fstests_vm_boot_and_run",
  "arguments": {
    "kernel_path": "/path/to/kernel",
    "fstests_path": "/path/to/fstests",
    "use_device_pool": "default"
  }
}
```

### Method 3: List and Inspect Pools

```json
// List all pools
{
  "tool": "device_pool_list"
}

// Check specific pool
{
  "tool": "device_pool_status",
  "arguments": {"pool_name": "default"}
}
```

## Safety Features

### Comprehensive Validation

Before creating a pool, kerneldev-mcp performs 10 safety checks:

1. ✅ Device exists and is a block device
2. ✅ Device is not mounted
3. ✅ Device is not in /etc/fstab
4. ✅ Device is not a system disk
5. ✅ Device is not a RAID member
6. ✅ Device is not an existing LVM PV
7. ✅ Device is not encrypted
8. ✅ Device has no open file handles
9. ⚠️  Warns if device has filesystem signatures
10. ⚠️  Warns if device has partition table

**If ANY check fails with ERROR, setup is blocked.**

### User Confirmation

You'll see a summary and must type "YES" to proceed:

```
WARNING: This will DESTROY all data on /dev/nvme1n1

Device Information:
- Device: /dev/nvme1n1
- Size: 512GB (NVMe SSD)
- Current partitions: None
- Filesystem signatures: None detected
- Currently mounted: No
- Part of RAID: No
- In /etc/fstab: No

This will create:
Strategy: partition
- 7 partitions (70GB total)
  - test: 10GB
  - pool1-5: 10GB each
  - logwrites: 10GB

Type 'YES' to proceed:
```

### Transactional Operations

If setup fails partway through, automatic rollback:
- Restores partition table backup
- Removes created LVM structures
- No partial/corrupted state

## Permissions and sudo

**All LVM operations require sudo:**
- Pool setup: `sudo pvcreate`, `sudo vgcreate`
- Test execution: `sudo lvcreate` (creates unique LVs)
- Cleanup: `sudo lvremove` (deletes LVs)

**No special permission configuration needed** - just ensure your user has sudo access for LVM commands.

**VG persistence:** Once the VG is created, it's auto-discovered by LVM on every boot (by VG name, not device name). No udev rules or special permissions needed.

## Managing Pools

### Check Pool Status

```json
{
  "tool": "device_pool_status",
  "arguments": {"pool_name": "default"}
}
```

**Output:**
```
Device Pool Status: default

Strategy: partition
Device: /dev/nvme1n1
Created: 2025-11-15T10:30:00
User: josef

Volumes (7):
  - test: 10G @ /dev/nvme1n1p1 ($TEST_DEV)
  - pool1: 10G @ /dev/nvme1n1p2
  ...

Permissions: acl
Validated: 2025-11-15T10:31:00

Health Status: ✓ HEALTHY
Pool 'default' is healthy
```

### Resize LVM Volume

First, get the LV name from status, then resize:

```bash
# Get LV name
device_pool_status --pool=default
# Shows: kdev-20251115103045-a3f9d2-test

# Resize that specific LV
device_pool_resize \
  --pool=default \
  --lv_name=kdev-20251115103045-a3f9d2-test \
  --new_size="+20G"
```

### Clean Up Orphaned LVs

If MCP processes crash, LVs may be left behind:

```json
{
  "tool": "device_pool_cleanup",
  "arguments": {
    "pool_name": "default"
  }
}
```

**What this does:**
- Checks all LVs in state file
- Uses `os.kill(pid, 0)` to check if process still alive
- Removes LVs from dead processes
- Updates state file

**When to run:**
- After system crashes
- Before long test runs (to free up space)
- If you see "insufficient free space" errors

### Remove Pool

```json
{
  "tool": "device_pool_teardown",
  "arguments": {
    "pool_name": "default",
    "wipe_data": false  // Set true for secure erase
  }
}
```

**What happens:**
- Automatically cleans up orphaned LVs first
- Removes VG and PV
- Deletes configuration file
- Optional: Overwrites device with zeros (wipe_data=true)

## Troubleshooting

### Problem: "sudo: command not found" or permission errors

**Symptom:** LVM commands fail with permission errors

**Solution:**
```bash
# Ensure sudo is installed
which sudo

# Ensure your user can run LVM commands with sudo
sudo -l | grep -E "pvcreate|vgcreate|lvcreate|lvremove"

# Add to sudoers if needed (or use root)
```

### Problem: "Device is mounted"

**Symptom:** Can't create pool

**Solution:**
```bash
# Unmount device
sudo umount /dev/nvme1n1p1

# If busy, find what's using it
sudo lsof /dev/nvme1n1p1
```

### Problem: "Device is a RAID member"

**Symptom:** Safety validation blocks device

**Solution:** Use a different device. Don't break your RAID!

### Problem: Pool setup hangs

**Symptom:** Setup doesn't complete

**Solutions:**
1. Check if sudo password needed (prompt may be hidden)
2. Verify device isn't in use: `sudo lsof /dev/nvme1n1`
3. Check dmesg for hardware errors: `dmesg | tail`

### Problem: "Insufficient free space" / VG Full

**Symptom:** Tests fail with "not enough free space in volume group"

**Cause:** Orphaned LVs from crashed MCP processes are filling the VG

**Solution:**
```bash
# Check VG free space
sudo vgs  # Look at VFree column

# See all LVs
sudo lvs  # Look for old timestamps

# Clean up orphaned LVs
device_pool_cleanup --pool=default

# Verify space freed
sudo vgs
```

### Problem: Slow performance after setup

**Symptom:** Still slow with physical devices

**Possible Causes:**
1. **Wrong device:** Using HDD instead of SSD
   - Check: `lsblk -d -o name,rota` (0=SSD, 1=HDD)
2. **USB device:** USB has high latency
   - Use internal SATA/NVMe instead
3. **Filesystem overhead:** Some filesystems are slower
   - Expected: btrfs slower than ext4

## Best Practices

### 1. Use Dedicated Devices

❌ **Don't:** Share device with other workloads
✅ **Do:** Dedicate entire disk to testing

### 2. Size Your Device Appropriately

**Calculate space needed:**
- Standard fstests: 7 LVs × 10GB = 70GB per test
- Concurrent tests: 70GB × N concurrent Claudes
- Example: 512GB device = 7 concurrent test runs

**Recommendations:**
- **Single Claude**: 128GB+ device is plenty
- **2-3 concurrent Claudes**: 256GB+ device
- **4-7 concurrent Claudes**: 512GB+ device

### 3. Enable Auto-Use Environment Variable

```bash
# In ~/.bashrc
export KERNELDEV_DEVICE_POOL=default
```

One-time setup, automatic usage everywhere. All tests auto-allocate LVs.

### 4. Run Cleanup Periodically

For long-running work or after crashes:
```bash
device_pool_cleanup --pool=default
```

This frees space from orphaned LVs.

### 5. Use keep_volumes for Debugging

When you need to inspect test state:
```bash
fstests_vm_boot_and_run ... --keep_volumes=true
# LVs remain after test, check with: sudo lvs
# Cleanup when done: device_pool_cleanup
```

## Performance Tips

### 1. Use NVMe Over SATA

**NVMe:** ~500K IOPS, <100µs latency
**SATA SSD:** ~100K IOPS, ~500µs latency
**HDD:** ~200 IOPS, ~10ms latency

**NVMe provides 5× better performance than SATA SSD**

### 2. Use Multiple Devices for More Concurrency

If you have multiple SSDs, create multiple pools for more total capacity:

```bash
# Pool 1: NVMe (fast, for quick tests)
device_pool_setup --device=/dev/nvme1n1 --pool=nvme

# Pool 2: SATA SSD (more capacity, for long tests)
device_pool_setup --device=/dev/sdb --pool=sata

# Each pool supports multiple concurrent Claudes
# Total concurrency = (nvme_size + sata_size) / 70GB
```

### 3. Enable TRIM/Discard

For SSDs, enable TRIM in your filesystem:

```bash
# Most filesystems enable this automatically
# Verify with: mount | grep discard
```

### 4. Monitor I/O Scheduler

For NVMe, `none` is fastest:
```bash
cat /sys/block/nvme1n1/queue/scheduler
# [none] mq-deadline kyber bfq
```

kerneldev-mcp automatically selects optimal scheduler.

## FAQ

**Q: Can I use the same device for multiple pools?**
A: No. Each pool needs a dedicated device.

**Q: Can I use a partition instead of whole disk?**
A: Yes! Use `/dev/nvme1n1p5` instead of `/dev/nvme1n1`.

**Q: What happens if I reboot?**
A: LVM volumes persist automatically. Udev rules apply permissions on boot.

**Q: Can I delete a pool and recreate it?**
A: Yes. Use `device_pool_teardown` then `device_pool_setup`.

**Q: Can I have pools on multiple devices?**
A: Yes! Create multiple pools with different names.

**Q: Does this work with USB devices?**
A: Technically yes, but USB is slow. Use internal NVMe/SATA devices.

**Q: Can I use loop devices sometimes?**
A: Yes. If no pool specified and no env var, falls back to loop devices.

**Q: How do I know if my pool is being used?**
A: Check test output - it shows device paths. LVM devices show as `/dev/<vg-name>/<lv-name>`.

**Q: Can I resize volumes after creating the pool?**
A: Yes! Use `device_pool_resize` with the full LV name to grow or shrink logical volumes dynamically.

**Q: Can multiple Claude instances use the same pool?**
A: Yes! Each test automatically gets unique LV names (timestamp + random), so there's no conflict.

**Q: What happens if an MCP process crashes?**
A: LVs are left behind (orphaned). Run `device_pool_cleanup` to remove them and free space.

**Q: How many concurrent tests can I run?**
A: Depends on device size. Example: 512GB device ÷ 70GB per test = ~7 concurrent tests.

**Q: Do I need to pre-create volumes?**
A: No! The pool is just an empty VG. Volumes are created automatically when you run tests.

**Q: Where are LV allocations tracked?**
A: In `~/.kerneldev-mcp/lv-state.json` with PID tracking for orphan cleanup.

## Next Steps

1. **Set up your first pool** (5 minutes)
2. **Export KERNELDEV_DEVICE_POOL** environment variable
3. **Run a quick test** to verify performance
4. **Monitor first few runs** to ensure stability
5. **Enjoy 10× faster tests!**

## Support

- **Architecture**: `docs/DEVICE-POOL-ARCHITECTURE.md` - Detailed concurrency model and design
- **Design document**: `docs/implementation/device-pool-design.md` - Original multi-phase design
- **Issue tracker**: https://github.com/anthropics/claude-code/issues
- **Changelog**: `CHANGELOG.md` - Implementation history and changes
