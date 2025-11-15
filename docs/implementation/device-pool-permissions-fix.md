# Device Pool LV Permissions Fix

## Problem

When the device pool manager creates logical volumes (LVs) for testing, they are created with root:disk ownership and 660 permissions by default (via udev rules). This means non-root users cannot access these devices for filesystem operations, which breaks fstests and other testing workflows that expect to run without sudo.

## Background

The kerneldev-mcp project uses LVM to manage device pools for kernel testing. The workflow is:

1. User sets up a physical device as an LVM pool (PV + VG)
2. When tests run, LVs are created on-demand with unique names
3. Tests use these LVs as block devices for filesystem testing
4. After tests complete, LVs are automatically deleted

The issue is that step 3 fails without proper permissions on the LV devices.

## Solution

We implemented a simple ownership-based approach that grants the current user access to each LV immediately after creation. This is appropriate because:

1. **LVs are ephemeral** - Created per-session and deleted after use
2. **No persistence needed** - We don't need access to survive reboots
3. **Aligns with project philosophy** - Uses sudo like everything else

### Implementation

Added a `_grant_user_lv_access()` helper function that:

1. Waits for the device to appear (udev processing)
2. Changes ownership to `{username}:disk` using sudo
3. Verifies access by actually opening the device

This function is called in `LVMPoolManager.allocate_volumes()` right after each LV is created.

### Code Changes

**File: `src/kerneldev_mcp/device_pool.py`**

```python
def _grant_user_lv_access(lv_path: str) -> bool:
    """Grant user read/write access to LV device by changing ownership.

    This is sufficient for ephemeral LVs that are deleted after each run.
    For persistent access, user should be added to 'disk' group.
    """
    # Get username safely (avoid os.getlogin() issues)
    username = os.environ.get('USER') or pwd.getpwuid(os.getuid()).pw_name

    # Wait for device to appear and settle (udev processing)
    device_path = Path(lv_path)
    for attempt in range(20):  # Wait up to 2 seconds
        if device_path.exists():
            # Device exists, wait a bit more for udev to finish
            time.sleep(0.1)
            break
        time.sleep(0.1)
    else:
        logger.error(f"Device {lv_path} did not appear after creation")
        return False

    # Change ownership to user (disk group for compatibility)
    try:
        subprocess.run(
            ["sudo", "chown", f"{username}:disk", lv_path],
            capture_output=True,
            check=True,
            timeout=5
        )
        logger.debug(f"Changed ownership of {lv_path} to {username}:disk")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to change ownership of {lv_path}: {e.stderr.decode()}")
        return False

    # Verify we can actually access the device (critical!)
    try:
        with open(lv_path, 'rb') as f:
            f.read(512)  # Try reading first sector
        logger.info(f"✓ Granted {username} access to {lv_path}")
        return True
    except PermissionError:
        # Provide helpful instructions if this fails
        logger.error(
            f"Cannot access {lv_path} even after ownership change.\n"
            f"You may need to add yourself to the 'disk' group:\n"
            f"  sudo usermod -a -G disk {username}\n"
            f"Then logout and login again.\n"
            f"WARNING: This grants access to ALL block devices on the system."
        )
        return False
    except Exception as e:
        logger.error(f"Failed to verify access to {lv_path}: {e}")
        return False
```

And in `allocate_volumes()`:

```python
subprocess.run(
    ["sudo", "lvcreate", "-L", vol_spec.size, "-n", lv_name, vg_name],
    capture_output=True,
    check=True,
    timeout=30
)

# Grant user access to LV device
success = _grant_user_lv_access(lv_path)
if not success:
    raise RuntimeError(f"Failed to grant access to {lv_path}")
```

## Testing

Two tests were created to verify the fix:

### 1. Unit Test: `tests/test_device_pool_permissions.py`

Tests that:
- Allocated LVs are accessible without sudo
- User can read from the devices
- User can write to the devices
- Ownership is correctly set

### 2. Integration Test: `tests/integration/test_device_pool_fstests_integration.py`

Tests that:
- LVs work with the full fstests workflow
- Filesystem operations (mkfs.ext4) work on the devices
- The complete allocate->use->release cycle works

Both tests pass successfully, confirming that users can now access pool LVs without needing sudo.

## User Impact

### Before Fix
```bash
# LVs created but not accessible
$ ./your-test-script
Error: Permission denied accessing /dev/vg/lv-test
```

### After Fix
```bash
# LVs created and immediately accessible
$ ./your-test-script
✓ Granted josef access to /dev/vg/lv-test
✓ Test completed successfully
```

## Alternative Approaches Considered

1. **ACLs** - More complex, has race conditions with udev
2. **Udev Rules** - Would require system configuration changes
3. **Disk Group Membership** - Grants too broad permissions (all block devices)
4. **Persistent Permissions** - Not needed for ephemeral LVs

The ownership change approach was chosen because it's simple, reliable, and aligns with the project's sudo-based philosophy.

## Security Considerations

- Changes only affect ephemeral LVs created by this tool
- LVs are deleted after each test run
- No system-wide permission changes required
- Users still need sudo to create/delete LVs (as before)

## Compatibility

- Works on all Linux distributions with standard LVM
- No special kernel features required
- Compatible with all filesystem types
- No changes needed to existing test scripts

## Future Improvements

Could optionally add a check during pool setup to warn users if they're not in the 'disk' group, suggesting they add themselves for better performance (avoiding the chown on each LV creation).