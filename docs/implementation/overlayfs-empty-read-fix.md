# Fix for Overlayfs Empty Read Bug

## Summary

Fixed a critical bug in the Linux kernel where all file reads through overlayfs returned empty content (0 bytes), breaking systems using overlayfs with virtiofs or other backing filesystems.

## The Bug

### Symptoms
- Files appear to exist with correct size via `stat()`
- Reading files returns empty content (0 bytes)
- All executables fail with ENOEXEC
- System becomes unusable when overlayfs is used for system directories

### Root Cause

The bug was introduced in commit `b9455f57e320` ("backing-file: use credential guards for reads") which refactored the credential handling in `fs/backing-file.c`.

The problematic code in `backing_file_read_iter()`:

```c
ssize_t ret;  // Declared but not assigned

// ...

scoped_with_creds(ctx->cred)
    do_backing_file_read_iter(file, iter, iocb, flags);  // Return value ignored!

// ...

return ret;  // Returns uninitialized value (likely 0)
```

The `do_backing_file_read_iter()` function returns the number of bytes read, but this return value was not being captured in the `ret` variable, causing the function to return an uninitialized value (typically 0).

## The Fix

The fix is simple - capture the return value:

```c
scoped_with_creds(ctx->cred)
    ret = do_backing_file_read_iter(file, iter, iocb, flags);
```

## Impact

This bug affected:
- All overlayfs mounts on affected kernels
- Systems using virtme-ng for kernel testing (which uses overlayfs by default)
- Container systems using overlayfs
- Any system mounting overlayfs over virtiofs, 9p, or other backing filesystems

## Testing

### Before Fix
```bash
# Mount overlayfs
mount -t overlay overlay -o lowerdir=/lib,upperdir=/tmp/upper,workdir=/tmp/work /mnt

# Try to read any file
cat /mnt/libc.so.6
# Returns nothing (empty)

# Check file exists and has size
stat /mnt/libc.so.6
# Shows correct size but reading returns empty
```

### After Fix
```bash
# Same mount, but now files read correctly
cat /mnt/libc.so.6 | head -c 20 | od -x
# Shows ELF header: 7f45 4c46 0201 0103 ...
```

## Detection

To check if your kernel is affected:

1. Check kernel version and if it includes commit `b9455f57e320`
2. Test overlayfs read behavior:

```bash
# Create test setup
mkdir -p /tmp/test/{lower,upper,work,merged}
echo "test content" > /tmp/test/lower/file.txt

# Mount overlay
mount -t overlay overlay \
  -o lowerdir=/tmp/test/lower,upperdir=/tmp/test/upper,workdir=/tmp/test/work \
  /tmp/test/merged

# Test read
if [ -z "$(cat /tmp/test/merged/file.txt)" ]; then
    echo "KERNEL BUG: overlayfs returns empty reads"
else
    echo "Kernel OK"
fi

# Cleanup
umount /tmp/test/merged
```

## Related Files

- `/home/josef/vfs/fs/backing-file.c` - Where the bug was located
- `/home/josef/vfs/fs/overlayfs/file.c` - Calls `backing_file_read_iter()`
- Commit: `383581bb06c6` in the `iput-work` branch

## Lessons Learned

1. **Credential guard macros need careful attention to return values** - The `scoped_with_creds()` macro executes code in a different credential context but doesn't automatically handle return values.

2. **Compiler warnings might have caught this** - An uninitialized variable warning could have detected this, though the variable was technically initialized (just never assigned the actual result).

3. **Testing matters** - This bug completely breaks overlayfs but might not be caught by unit tests that don't actually read file content through the overlay.

## Timeline

- Bug introduced: Commit `b9455f57e320` (dated November 3, 2025 - likely incorrect date)
- Bug discovered: November 12, 2025 during virtme-ng boot testing
- Bug fixed: November 12, 2025 in commit `383581bb06c6`