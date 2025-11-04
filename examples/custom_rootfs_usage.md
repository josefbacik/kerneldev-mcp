# Custom Rootfs Usage Examples

## Quick Start

### 1. One-time Setup: Create the Rootfs

```python
# Create rootfs with default settings (Ubuntu 22.04, ~/.kerneldev-mcp/test-rootfs)
create_test_rootfs()

# Output:
# Creating test rootfs at /home/user/.kerneldev-mcp/test-rootfs
# Creating Ubuntu jammy rootfs (this may take 5-10 minutes)...
#   Downloading base system packages...
#   Base rootfs created successfully
#   Configuring test users...
# ✓ Rootfs created and configured successfully
#   Location: /home/user/.kerneldev-mcp/test-rootfs
#   Size: 450M
#
# You can now use use_custom_rootfs=true in fstests_vm_boot_and_run
```

### 2. Verify Rootfs is Ready

```python
# Check rootfs status
check_test_rootfs()

# Output:
# ✓ Rootfs is configured and ready at /home/user/.kerneldev-mcp/test-rootfs
#
# Size: 450M
# Ubuntu: jammy
# Test users: fsqa, fsgqa2
```

### 3. Run Tests with Custom Rootfs

```python
# Run fstests using the custom rootfs
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux",
    fstests_path="/home/user/.kerneldev-mcp/fstests",
    tests=["-g", "quick"],
    fstype="ext4",
    use_custom_rootfs=True  # This is the key parameter!
)

# Output:
# ============================================================
# Starting kernel boot with fstests: /home/user/linux
# Config: fstype=ext4, memory=4G, cpus=4, timeout=300s
# Tests: -g quick
# IO scheduler: mq-deadline
# Using custom test rootfs (isolated from host)
# ✓ Using custom rootfs: /home/user/.kerneldev-mcp/test-rootfs
#   Size: 450M
#   Test users: fsqa, fsgqa2
# ...
# Tests now run with fsqa user available!
```

## Advanced Usage

### Custom Rootfs Location

```python
# Create rootfs in a custom location
create_test_rootfs(rootfs_path="/mnt/storage/my-test-rootfs")

# Use it when running tests
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux",
    fstests_path="/home/user/.kerneldev-mcp/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True,
    custom_rootfs_path="/mnt/storage/my-test-rootfs"
)
```

### Different Ubuntu Release

```python
# Use Ubuntu 24.04 (Noble) instead of 22.04 (Jammy)
create_test_rootfs(ubuntu_release="noble")

# Use Ubuntu 20.04 (Focal)
create_test_rootfs(ubuntu_release="focal")
```

### Recreate Rootfs

```python
# Force recreation (useful if rootfs got corrupted or needs updating)
create_test_rootfs(force=True)

# Or delete and recreate
delete_test_rootfs()
create_test_rootfs()
```

## Workflow Examples

### First-time Setup Workflow

```python
# Step 1: Check if rootfs exists
check_test_rootfs()
# Output: ✗ Rootfs does not exist at ...

# Step 2: Create rootfs
create_test_rootfs()
# Wait 5-10 minutes...

# Step 3: Verify it worked
check_test_rootfs()
# Output: ✓ Rootfs is configured and ready

# Step 4: Run tests
fstests_vm_boot_and_run(
    kernel_path="/path/to/kernel",
    fstests_path="/path/to/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True
)
```

### Testing Multiple Kernels

```python
# Create rootfs once
create_test_rootfs()

# Test kernel version A
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux-6.8",
    fstests_path="/home/user/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True
)

# Test kernel version B (reuses same rootfs)
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux-6.9",
    fstests_path="/home/user/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True
)
```

### Cleanup Workflow

```python
# When done testing, free up disk space
delete_test_rootfs()
# Output: ✓ Successfully deleted rootfs at /home/user/.kerneldev-mcp/test-rootfs

# Recreate when needed again
create_test_rootfs()
```

## Comparison: With vs Without Custom Rootfs

### Without Custom Rootfs (Default)

```python
# Run tests using host filesystem
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux",
    fstests_path="/home/user/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=False  # Or omit this parameter
)

# Potential issues:
# - Some tests may fail with "fsqa: No such user"
# - Tests see all host files and users
# - Less isolated environment
```

### With Custom Rootfs

```python
# Run tests using isolated rootfs
fstests_vm_boot_and_run(
    kernel_path="/home/user/linux",
    fstests_path="/home/user/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True
)

# Benefits:
# ✓ fsqa user is always available
# ✓ Isolated from host system
# ✓ Reproducible environment
# ✓ No host system pollution
```

## Troubleshooting Examples

### Error: "Custom rootfs not found"

```python
# Check status
check_test_rootfs()
# Output: ✗ Rootfs does not exist at ...

# Solution: Create the rootfs
create_test_rootfs()
```

### Error: "Custom rootfs not properly configured"

```python
# Check status
check_test_rootfs()
# Output: ⚠ Rootfs exists but is not properly configured
#         Missing configuration: users=fsqa

# Solution: Recreate the rootfs
create_test_rootfs(force=True)
```

### Error: "debootstrap not found"

```bash
# Install debootstrap first
# Fedora/RHEL:
sudo dnf install debootstrap

# Ubuntu/Debian:
sudo apt install debootstrap

# Then create rootfs
```

```python
create_test_rootfs()
```

## Performance Notes

### Creation Time
- First time: 5-10 minutes (downloads packages)
- Subsequent recreations: 3-5 minutes (may use cached packages)

### Storage Usage
- Download: ~200-300 MB
- Installed: ~400-500 MB
- Total: ~500-800 MB

### Runtime Performance
- Boot time: Same as host filesystem
- Test execution: No measurable difference
- I/O performance: Identical to host filesystem

## Best Practices

1. **Create rootfs once**: Reuse it for multiple test runs
2. **Use default location**: Unless you have specific storage requirements
3. **Use LTS releases**: jammy (22.04) is stable and well-supported
4. **Verify before testing**: Always run `check_test_rootfs()` first
5. **Clean up when done**: Delete rootfs if not actively testing to save space

## Integration with Existing Workflows

### With Baseline Comparisons

```python
# Create rootfs
create_test_rootfs()

# Run tests and save baseline
run_and_save_fstests(
    kernel_path="/path/to/kernel",
    fstests_path="/path/to/fstests",
    tests=["-g", "auto"],
    use_custom_rootfs=True  # NOTE: Not yet implemented, but planned
)
```

### With Cross-Compilation

```python
# Custom rootfs works with cross-compilation too
fstests_vm_boot_and_run(
    kernel_path="/path/to/kernel",
    fstests_path="/path/to/fstests",
    tests=["-g", "quick"],
    use_custom_rootfs=True,
    # Cross-compilation parameters (if needed)
)
```

## Future Enhancements

Planned features:

1. **Pre-built rootfs downloads**: Skip the 5-10 minute build time
2. **Rootfs profiles**: Different configurations for different test suites
3. **Automatic package installation**: Detect and install missing dependencies
4. **Rootfs versioning**: Track and auto-update rootfs versions
5. **Integration with all VM tools**: Support custom rootfs in all boot tools
