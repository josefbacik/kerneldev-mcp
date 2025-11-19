# null_blk Device Support Implementation

## Overview

The null_blk feature provides high-performance memory-backed block devices for VM testing, offering 10-100× performance improvement over traditional loop devices. This document describes the technical implementation, architecture decisions, and operational considerations.

## Performance Characteristics

### Benchmark Results

Performance comparison (4K random reads, direct I/O):

| Device Type | IOPS | Latency | Memory Usage | Availability |
|-------------|------|---------|--------------|--------------|
| **null_blk** | 7M+ | ~0.14 μs | High (uses RAM) | Linux 5.0+ with module |
| **tmpfs loop** | 200K+ | ~5 μs | Medium (uses RAM) | Universal |
| **disk loop** | 50K | ~20 μs | Low (disk cache) | Universal |
| **LVM on SSD** | 475K | ~2 μs | Low | Requires setup |

### When to Use Each Type

**null_blk** (Fastest):
- High-speed benchmarking and performance testing
- Latency-sensitive workloads
- Testing with minimal I/O overhead
- Systems with plenty of RAM (32GB+ recommended)
- Not suitable for: testing actual disk I/O behavior, systems with limited RAM

**tmpfs** (Fast, Universal):
- General-purpose testing with good performance
- Systems without null_blk support
- Balance of speed and compatibility
- Moderate memory requirements

**disk** (Universal, Low Memory):
- Testing on memory-constrained systems
- Long-running tests that need persistence
- Default for maximum compatibility
- Realistic I/O characteristics

## Architecture

### Component Structure

The null_blk implementation is integrated into the existing device management infrastructure:

```
device_utils.py (Shared Utilities)
├── DeviceBacking enum (DISK/TMPFS/NULL_BLK)
├── check_null_blk_support() - Availability checking
├── create_null_blk_device() - Device creation
├── cleanup_null_blk_device() - Device cleanup
├── cleanup_orphaned_null_blk_devices() - Orphan cleanup
└── _allocate_null_blk_index() - Atomic index allocation

boot_manager.py (VM Device Management)
├── VMDeviceManager
│   ├── null_blk_supported (detection flag)
│   ├── created_null_blk_devices (tracking)
│   └── cleanup() - Resource cleanup
└── DeviceSpec (with backing parameter)
```

### Design Decisions

#### 1. Why device_utils.py?

The null_blk functions were added to `device_utils.py` (shared utilities) rather than creating a separate module because:

- **Code Reuse**: Parallels existing loop device functions (`create_loop_device`, `cleanup_loop_device`)
- **Single Responsibility**: All device creation/cleanup logic in one place
- **Easy Migration**: Future callers (like fstests) can use the same functions
- **Consistent API**: Similar function signatures to loop device functions

#### 2. Why Enum-Based API?

The `DeviceBacking` enum provides:

- **Type Safety**: Prevents invalid backing type strings
- **Discoverability**: IDE autocomplete shows all options
- **Future-Proof**: Easy to add new backing types (e.g., `DeviceBacking.NBD`)
- **Clarity**: `backing=DeviceBacking.NULL_BLK` is clearer than `backing="null_blk"`

#### 3. Why Configfs Interface?

null_blk devices are created via configfs rather than module parameters because:

- **Reliability**: Atomic directory creation prevents race conditions
- **Kernel Standard**: Recommended approach since Linux 4.0+
- **Fine Control**: Can set per-device parameters (size, queue depth, etc.)
- **Clean Cleanup**: Easy to detect and remove orphaned devices

#### 4. Why Automatic Fallback?

The three-tier fallback chain (null_blk → tmpfs → disk) provides:

- **User Experience**: Tests "just work" without manual configuration
- **Graceful Degradation**: Falls back to slower but working options
- **Transparency**: Logs show which backing was actually used
- **Flexibility**: Users can force a specific backing type if needed

## How null_blk Works

### Kernel Module

null_blk is a kernel block device driver that simulates a block device entirely in memory. It was originally created for testing and benchmarking block layer code.

**Key Features**:
- Zero-latency I/O (no actual storage access)
- Configurable device parameters (size, queue depth, block size)
- Memory-backed or no-op mode
- Supports up to 1024 devices (indices 0-1023)

**Requirements**:
- Linux 5.0+ (earlier versions have limited configfs support)
- CONFIG_BLK_DEV_NULL_BLK=m or =y in kernel config
- configfs mounted at /sys/kernel/config

### Configfs Interface

Devices are created by manipulating sysfs directories:

```bash
# Load module (if not built-in)
modprobe null_blk

# Create device directory (atomically allocates index)
mkdir /sys/kernel/config/nullb/nullb0

# Configure device
echo 10240 > /sys/kernel/config/nullb/nullb0/size          # 10GB (in MB)
echo 1 > /sys/kernel/config/nullb/nullb0/memory_backed     # Use RAM
echo 4096 > /sys/kernel/config/nullb/nullb0/blocksize      # 4K blocks
echo 128 > /sys/kernel/config/nullb/nullb0/hw_queue_depth  # Queue size

# Activate device (creates /dev/nullb0)
echo 1 > /sys/kernel/config/nullb/nullb0/power

# Device is now ready at /dev/nullb0
```

### Performance Parameters

Our implementation uses optimized defaults:

```c
blocksize = 4096          // 4K blocks (modern standard)
hw_queue_depth = 128      // Large queue for throughput
irqmode = 0               // No IRQ overhead (polling mode)
completion_nsec = 0       // Zero latency
memory_backed = 1         // Use RAM (required for FS testing)
```

## Implementation Details

### Atomic Index Allocation

**Problem**: Multiple processes might try to create devices concurrently.

**Solution**: Use configfs directory creation atomicity:

```python
def _allocate_null_blk_index() -> Optional[int]:
    for idx in range(1024):
        device_dir = NULLB_CONFIGFS / f"nullb{idx}"
        try:
            subprocess.run(["sudo", "mkdir", str(device_dir)], check=True)
            return idx  # First mkdir to succeed wins
        except subprocess.CalledProcessError:
            continue  # Index taken, try next
    return None  # All indices in use
```

This is race-free because:
- Directory creation is atomic at the filesystem level
- Only one process can successfully create a given directory
- Losing processes get EEXIST error and try next index

### Staleness-Based Orphan Cleanup

**Problem**: Crashed processes leave behind null_blk devices.

**Solution**: Clean up devices that haven't been modified recently:

```python
def cleanup_orphaned_null_blk_devices(staleness_seconds: int = 60):
    current_time = time.time()
    for device_dir in NULLB_CONFIGFS.iterdir():
        mtime = device_dir.stat().st_mtime
        age_seconds = current_time - mtime

        if age_seconds >= staleness_seconds:
            cleanup_null_blk_device(device_path, idx)
```

**Why staleness checking?**:
- Prevents race conditions with concurrent device creation
- Avoids deleting devices being actively created
- 60-second threshold is safe (device creation takes <1 second)
- Multiple processes can run cleanup safely

### Fallback Chain Logic

The fallback chain is implemented in `VMDeviceManager._setup_devices()`:

```python
# Try null_blk first
if spec.backing == DeviceBacking.NULL_BLK:
    if self.null_blk_supported:
        null_blk_dev, idx = create_null_blk_device(spec.size, spec.name)
        if null_blk_dev:
            # Success - use null_blk
            self.created_null_blk_devices.append((null_blk_dev, idx))
            continue
        else:
            # Failed - log and fall back
            logger.warning(f"Failed to create null_blk device, falling back to tmpfs")

    # Mutate spec.backing to tmpfs for fallback
    # NOTE: This intentional mutation ensures device is created with tmpfs
    spec.backing = DeviceBacking.TMPFS

# Continue with tmpfs or disk loop device creation...
```

**Important**: The `spec.backing` field is mutated during fallback. This is intentional and documented - it ensures the DeviceSpec accurately reflects what was actually created.

### Memory Management

Memory limits prevent system OOM:

```python
# Configurable via environment variables
MAX_NULL_BLK_DEVICE_GB = int(os.getenv("KERNELDEV_NULL_BLK_MAX_SIZE", "32"))
MAX_NULL_BLK_TOTAL_GB = int(os.getenv("KERNELDEV_NULL_BLK_TOTAL", "70"))

# Validated in DeviceSpec.validate()
size_gb = parse_size_to_gb(self.size)
if size_gb > MAX_NULL_BLK_DEVICE_GB:
    return False, f"null_blk device size {self.size} exceeds maximum {MAX_NULL_BLK_DEVICE_GB}G"
```

**Why these defaults?**:
- 32GB/device: Reasonable for modern systems (64GB RAM)
- 70GB total: Allows 2-3 large devices safely
- Leaves room for: kernel (4-8GB), userspace (8-16GB), buffer cache
- User can override if they have more RAM

## Troubleshooting

### null_blk Module Not Available

**Symptoms**:
- `check_null_blk_support()` returns "module not available"
- Fallback to tmpfs or disk

**Diagnosis**:
```bash
# Check if module exists
modprobe -n null_blk

# Check kernel config
grep BLK_DEV_NULL_BLK /boot/config-$(uname -r)
```

**Solutions**:
1. If `CONFIG_BLK_DEV_NULL_BLK` not set: rebuild kernel with it enabled
2. If module blacklisted: remove from `/etc/modprobe.d/blacklist.conf`
3. If no module: use tmpfs backing instead

### configfs Not Mounted

**Symptoms**:
- `/sys/kernel/config` does not exist
- Error: "configfs not mounted"

**Diagnosis**:
```bash
mount | grep configfs
ls -l /sys/kernel/config
```

**Solutions**:
```bash
# Mount configfs
sudo mount -t configfs none /sys/kernel/config

# Make persistent (add to /etc/fstab)
echo "none /sys/kernel/config configfs defaults 0 0" | sudo tee -a /etc/fstab
```

### Permission Errors

**Symptoms**:
- "No permission to create null_blk devices"
- Fallback to tmpfs even with module loaded

**Diagnosis**:
```bash
# Check configfs permissions
ls -ld /sys/kernel/config/nullb/

# Try to create test device
sudo mkdir /sys/kernel/config/nullb/test
```

**Solutions**:
1. Ensure running with sudo (already handled by implementation)
2. Check SELinux/AppArmor policies
3. Verify configfs is writable: `mount -o remount,rw /sys/kernel/config`

### All Indices in Use

**Symptoms**:
- "All null_blk device indices (0-1023) are in use"
- Cannot create more devices

**Diagnosis**:
```bash
# List all active null_blk devices
ls -1 /sys/kernel/config/nullb/
ls -1 /dev/nullb*
```

**Solutions**:
```bash
# Clean up orphaned devices (if you have this tool)
python3 -c "
from src.kerneldev_mcp.device_utils import cleanup_orphaned_null_blk_devices
cleaned = cleanup_orphaned_null_blk_devices(staleness_seconds=10)
print(f'Cleaned {cleaned} devices')
"

# Manual cleanup (careful!)
for dir in /sys/kernel/config/nullb/nullb*; do
    echo 0 > $dir/power
    rmdir $dir
done
```

### Memory Limits Hit

**Symptoms**:
- "null_blk device size exceeds maximum"
- Validation error before creation

**Solutions**:
```bash
# Option 1: Use smaller devices
# Instead of 50GB, use 20GB

# Option 2: Increase limits (if you have RAM)
export KERNELDEV_NULL_BLK_MAX_SIZE=64     # 64GB per device
export KERNELDEV_NULL_BLK_TOTAL=128       # 128GB total

# Option 3: Use tmpfs or disk backing instead
# backing=DeviceBacking.TMPFS
```

### System Runs Out of Memory

**Symptoms**:
- OOM killer triggers
- System becomes unresponsive
- High memory usage in `top`

**Diagnosis**:
```bash
# Check null_blk memory usage
for dev in /dev/nullb*; do
    echo "$dev: $(blockdev --getsize64 $dev | numfmt --to=iec-i)B"
done

# Check total memory usage
free -h
```

**Solutions**:
1. Reduce device sizes
2. Use fewer devices
3. Lower memory limits
4. Switch to disk-backed loop devices
5. Add more RAM or swap

## Known Limitations

### 1. Linux Kernel Version

- **Minimum**: Linux 5.0 for full configfs support
- **Recommended**: Linux 5.4+ for stability
- **Earlier versions**: Some configfs parameters may not be available

### 2. Memory Requirements

- Each device uses RAM equal to its configured size
- Memory is allocated on first write, not at creation
- No disk persistence - data lost on cleanup
- Large devices (50GB+) require significant RAM

### 3. Concurrent Access

- Multiple VMs can use null_blk devices (different indices)
- Cannot share same null_blk device between VMs
- Index allocation is thread-safe but not multi-process safe across different MCP servers
- Use LVM device pools for concurrent multi-process testing

### 4. No Actual I/O

- null_blk does not perform real I/O
- Cannot test disk-specific behaviors (TRIM, NCQ, etc.)
- Does not test SATA/NVMe controller behavior
- Latencies are unrealistically low (good for testing, not for profiling real-world performance)

### 5. configfs Dependency

- Requires configfs mounted at `/sys/kernel/config`
- May not work in nested containers
- Some container runtimes restrict configfs access
- Fallback to tmpfs/disk handles this gracefully

### 6. Module Parameter Limitations

- Some parameters (e.g., `completion_nsec`) may not be available on all kernels
- Implementation gracefully handles missing parameters
- Logs warnings but continues with default values

## Best Practices

### When to Use null_blk

Use null_blk when:
- Performance testing and benchmarking
- High-speed filesystem testing
- Latency-sensitive workloads
- System has plenty of RAM (64GB+)
- Testing on modern kernels (5.0+)

Avoid null_blk when:
- Testing real disk I/O behavior
- Limited RAM available
- Need data persistence across reboots
- Testing on older kernels (<5.0)
- Running in restricted containers

### Memory Planning

For a system with 64GB RAM:
- OS + kernel: 8GB
- Userspace: 8GB
- Buffer cache: 8GB
- Available for null_blk: ~40GB
- Safe configuration: 3x 10GB devices (30GB)

For a system with 32GB RAM:
- Available for null_blk: ~16GB
- Safe configuration: 2x 8GB devices or 4x 4GB devices

### Debugging Tips

**Enable verbose logging**:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

**Check device creation**:
```bash
# Watch syslog for null_blk messages
sudo dmesg -w | grep null_blk

# List all null_blk devices
ls -l /dev/nullb*
ls -l /sys/kernel/config/nullb/
```

**Verify device parameters**:
```bash
# Check device size
blockdev --getsize64 /dev/nullb0

# Check block size
blockdev --getbsz /dev/nullb0

# Check queue parameters
cat /sys/block/nullb0/queue/nr_requests
```

## Performance Tuning

### Optimal Parameters

Current implementation uses:
```
blocksize = 4096           # 4K (standard for modern storage)
hw_queue_depth = 128       # Large queue for throughput
irqmode = 0                # Polling mode (no IRQ overhead)
completion_nsec = 0        # Zero latency
```

### Alternative Configurations

**For latency testing** (more realistic):
```
completion_nsec = 10000    # 10 microseconds
irqmode = 1                # IRQ mode (more realistic)
```

**For queue depth testing**:
```
hw_queue_depth = 1         # Test single outstanding I/O
hw_queue_depth = 32        # Test moderate queue
hw_queue_depth = 256       # Test deep queue
```

**For block size testing**:
```
blocksize = 512            # Legacy devices
blocksize = 4096           # Modern SSDs
blocksize = 8192           # High-performance
```

Note: Parameter changes require modifying `create_null_blk_device()` in `device_utils.py`.

## Future Enhancements

Potential improvements for future versions:

1. **Parameter Configuration**: Allow users to specify null_blk parameters
2. **Multi-Device Allocation**: Atomic allocation of multiple devices
3. **Device Pools**: null_blk-backed device pools (like LVM pools)
4. **Statistics**: Track null_blk I/O statistics and report
5. **Snapshots**: Implement copy-on-write snapshots (if supported by kernel)
6. **Network Block Devices**: Add NBD as another backing type
7. **Disk Simulation**: Add latency and error injection support

## References

### Kernel Documentation
- [null_blk Documentation](https://www.kernel.org/doc/html/latest/block/null_blk.html)
- [configfs Documentation](https://www.kernel.org/doc/html/latest/filesystems/configfs.html)
- [Block Layer Documentation](https://www.kernel.org/doc/html/latest/block/index.html)

### Related Code
- `src/kerneldev_mcp/device_utils.py` - Implementation
- `src/kerneldev_mcp/boot_manager.py` - Integration
- `tests/test_device_utils_null_blk.py` - Unit tests
- `tests/integration/test_null_blk_integration.py` - Integration tests

### Commit History
- `d70f57a` - Initial null_blk implementation with fallback
- `1a11440` - Unit tests for null_blk utilities
- `0e46711` - Integration tests for null_blk boot scenarios
- `cda25f2` - MCP tool schema documentation

## Changelog

### 2025-11-19: Initial Implementation

- Added DeviceBacking enum (DISK/TMPFS/NULL_BLK)
- Implemented check_null_blk_support() with comprehensive validation
- Implemented create_null_blk_device() with atomic index allocation
- Implemented cleanup_null_blk_device() with proper deactivation
- Implemented cleanup_orphaned_null_blk_devices() with staleness checking
- Added null_blk support to VMDeviceManager with automatic fallback
- Added memory limits with environment variable configuration
- Comprehensive test coverage (unit + integration tests)
- Documentation in MCP tool schemas
