# Custom Test Rootfs for Kernel Testing

## Overview

The kerneldev-mcp now supports creating and using custom root filesystems for running kernel tests in an isolated VM environment. This solves the problem of tests requiring specific users (like `fsqa` and `fsgqa` for fstests) that may not exist on the host system.

## Problem Solved

When running fstests using virtme-ng with the default configuration, the VM shares the host system's root filesystem. This causes issues:

1. **Missing test users**: fstests requires users like `fsqa` and `fsgqa` that don't exist on most systems
2. **Host pollution**: Creating these users on the host system is undesirable and potentially risky
3. **Test isolation**: Tests should run in an isolated environment separate from the host

## Solution

The custom rootfs feature creates a minimal Ubuntu-based root filesystem with:
- Pre-configured test users (`fsqa`, `fsgqa2`)
- Pre-configured test groups (`fsgqa`)
- Essential packages for running fstests
- Complete isolation from the host system

## Architecture

### Components

1. **RootfsManager** (`rootfs_manager.py`):
   - Creates Ubuntu rootfs using virtme-ng's `--root-release` feature
   - Configures test users and groups
   - Validates rootfs is properly set up

2. **Integration with BootManager** (`boot_manager.py`):
   - Added `use_custom_rootfs` parameter to `boot_with_fstests()`
   - Automatically checks rootfs exists and is configured
   - Uses virtme-ng's `--root` option to boot with custom rootfs

3. **MCP Tools** (server.py):
   - `create_test_rootfs`: Create/rebuild the rootfs
   - `check_test_rootfs`: Check status and configuration
   - `delete_test_rootfs`: Remove the rootfs
   - `fstests_vm_boot_and_run`: Now supports `use_custom_rootfs` parameter

### How It Works

1. **Rootfs Creation**:
   ```
   virtme-ng --root-release jammy --root ~/.kerneldev-mcp/test-rootfs --  true
   ```
   This creates a minimal Ubuntu 22.04 (Jammy) rootfs.

2. **User Configuration**:
   After creation, the rootfs is configured via `chroot` to add:
   - User `fsqa` (uid:1000, gid:1000)
   - User `fsgqa2` (uid:1001, gid:1001)
   - Group `fsgqa` (gid:1002)
   - Essential packages for testing

3. **VM Booting**:
   ```
   vng --root ~/.kerneldev-mcp/test-rootfs --rwdir /path/to/fstests ...
   ```
   The VM boots with the custom rootfs, while fstests directory is mounted read-write.

## Usage

### 1. Create the Custom Rootfs

First, create the rootfs (one-time setup):

```python
# Via MCP tool
create_test_rootfs()

# Or with custom options
create_test_rootfs(
    rootfs_path="/custom/path/test-rootfs",
    ubuntu_release="jammy",  # Ubuntu 22.04 LTS
    force=False  # Set to true to recreate
)
```

This takes 5-10 minutes and downloads ~300-500MB of packages.

**Requirements**:
- `virtme-ng` installed (`pip install virtme-ng`)
- `debootstrap` installed (`sudo dnf install debootstrap` or `sudo apt install debootstrap`)
- Sufficient disk space (~500MB)
- sudo access for chroot operations

### 2. Verify the Rootfs

Check that rootfs was created successfully:

```python
check_test_rootfs()
```

Output example:
```
✓ Rootfs is configured and ready at /home/user/.kerneldev-mcp/test-rootfs

Size: 450M
Ubuntu: jammy
Test users: fsqa, fsgqa2
```

### 3. Run Tests with Custom Rootfs

Use the rootfs when running fstests:

```python
fstests_vm_boot_and_run(
    kernel_path="/path/to/kernel",
    fstests_path="/path/to/fstests",
    tests=["-g", "quick"],
    fstype="ext4",
    use_custom_rootfs=True  # Enable custom rootfs
)
```

### 4. Managing the Rootfs

**Recreate the rootfs**:
```python
create_test_rootfs(force=True)
```

**Delete the rootfs**:
```python
delete_test_rootfs()
```

**Use custom location**:
```python
create_test_rootfs(rootfs_path="/custom/path")
fstests_vm_boot_and_run(..., use_custom_rootfs=True, custom_rootfs_path="/custom/path")
```

## Default Configuration

### Default Rootfs Location
```
~/.kerneldev-mcp/test-rootfs/
```

### Default Ubuntu Release
- **jammy** (Ubuntu 22.04 LTS)
- Stable, well-supported, long-term support

### Configured Users

| Username | UID  | GID  | Groups | Purpose |
|----------|------|------|--------|---------|
| fsqa     | 1000 | 1000 | fsgqa  | Primary fstests user |
| fsgqa2   | 1001 | 1001 | -      | Secondary fstests user |

### Configured Groups

| Group  | GID  | Purpose |
|--------|------|---------|
| fsgqa  | 1002 | fstests group |

### Installed Packages

The rootfs includes:
- bash, coreutils, util-linux, procps
- sudo, acl, attr, quota
- xfsprogs, e2fsprogs, btrfs-progs
- Other essential utilities

## Technical Details

### virtme-ng Integration

The implementation uses virtme-ng's built-in features:

- `--root-release <release>`: Creates Ubuntu chroot with debootstrap
- `--root <path>`: Uses specified directory as VM root filesystem
- `--rwdir <path>`: Mounts directory read-write inside VM (for fstests)

### Rootfs Structure

```
~/.kerneldev-mcp/test-rootfs/
├── bin/           # Binaries
├── etc/           # Configuration files
│   ├── passwd     # Users including fsqa
│   ├── group      # Groups including fsgqa
│   └── shadow     # Password hashes
├── usr/           # User programs
├── var/           # Variable data
├── tmp/           # Temporary files
└── home/
    └── fsqa/      # fsqa user home directory
```

### Storage Requirements

- Initial download: ~200-300MB
- Extracted size: ~400-500MB
- Total: ~500-800MB depending on packages

### Performance Impact

- **Creation time**: 5-10 minutes (one-time)
- **Boot time**: No significant difference vs. host filesystem
- **Runtime**: No performance impact
- **Storage**: Minimal (~500MB)

## Troubleshooting

### Rootfs creation fails with "debootstrap not found"

**Solution**: Install debootstrap
```bash
# Fedora/RHEL
sudo dnf install debootstrap

# Ubuntu/Debian
sudo apt install debootstrap
```

### Rootfs creation fails with permission errors

**Solution**: Ensure you have sudo access for chroot operations

### Tests still fail with "fsqa user not found"

**Causes**:
1. Rootfs not created: Run `create_test_rootfs()`
2. Rootfs not configured: Run `create_test_rootfs(force=True)`
3. `use_custom_rootfs=True` not set in `fstests_vm_boot_and_run`

**Verification**:
```python
check_test_rootfs()  # Should show users: fsqa, fsgqa2
```

### Want to use a different Ubuntu release

```python
create_test_rootfs(ubuntu_release="noble")  # Ubuntu 24.04
```

Supported releases:
- `noble` (24.04)
- `jammy` (22.04 LTS) - recommended
- `focal` (20.04 LTS)
- `bionic` (18.04 LTS)

### Rootfs takes too much space

The rootfs can be deleted when not in use:
```python
delete_test_rootfs()
```

Recreate it before running tests:
```python
create_test_rootfs()
```

## Comparison with Alternatives

### Option 1: Custom Rootfs (Implemented)
✅ Isolated from host system
✅ Pre-configured users
✅ Easy to manage
✅ Uses virtme-ng built-in features
❌ Requires initial setup time
❌ Uses disk space

### Option 2: Host Filesystem (Default)
✅ No setup required
✅ No extra storage
❌ May lack test users
❌ Not isolated
❌ Could affect host

### Option 3: Manual User Creation on Host
✅ Works with host filesystem
❌ Pollutes host system
❌ Risky (uid conflicts, cleanup issues)
❌ Manual process

## Future Enhancements

Potential improvements:

1. **Multiple rootfs profiles**: Different configurations for different test suites
2. **Rootfs caching**: Share rootfs between different kernel testing environments
3. **Automated package installation**: Detect and install missing test dependencies
4. **Rootfs versioning**: Track rootfs version and auto-upgrade
5. **Pre-built rootfs**: Download pre-built rootfs images instead of building

## References

- virtme-ng documentation: https://github.com/arighi/virtme-ng
- fstests documentation: https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git/
- Ubuntu debootstrap: https://wiki.debian.org/Debootstrap
