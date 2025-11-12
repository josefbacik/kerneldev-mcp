# Changelog

## [Unreleased] - 2025-01-XX

### Removed

#### Custom Rootfs Feature
Removed the custom rootfs feature as it was not working properly and is not needed at this time:
- Removed `rootfs_manager.py` module
- Removed MCP tools: `create_test_rootfs`, `check_test_rootfs`, `delete_test_rootfs`
- Removed `use_custom_rootfs` and `custom_rootfs_path` parameters from `fstests_vm_boot_and_run`
- Removed documentation: `docs/CUSTOM_ROOTFS.md`, `docs/implementation/CUSTOM_ROOTFS_IMPLEMENTATION.md`, `examples/custom_rootfs_usage.md`

**Migration:** Remove any usage of the `use_custom_rootfs` parameter from `fstests_vm_boot_and_run` calls. The tool now uses the host filesystem directly via virtme-ng's default behavior.

### BREAKING CHANGES

#### fstests Tool Reorganization
All fstests-related tools have been renamed with clear category prefixes to improve discoverability and workflow understanding. This is a **breaking change** - existing code using the old tool names will need to be updated.

**Migration Guide:**

Setup tools (run in sequence):
- `check_fstests` → `fstests_setup_check`
- `install_fstests` → `fstests_setup_install`
- `setup_fstests_devices` → `fstests_setup_devices`
- `configure_fstests` → `fstests_setup_configure`

Run tools:
- `run_fstests` → `fstests_run`
- `run_and_save_fstests` → `fstests_run_and_save`

Baseline tools:
- `get_fstests_baseline` → `fstests_baseline_get`
- `compare_fstests_results` → `fstests_baseline_compare`
- `list_fstests_baselines` → `fstests_baseline_list`

Git integration tools:
- `load_fstests_from_git` → `fstests_git_load`
- `list_git_fstests_results` → `fstests_git_list`
- `delete_git_fstests_results` → `fstests_git_delete`

Info tools:
- `list_fstests_groups` → `fstests_groups_list`

VM tools (all-in-one):
- `boot_kernel_with_fstests` → `fstests_vm_boot_and_run`

**Rationale:**
- Visual grouping by prefix makes tool relationships clear
- Sequential numbering implicit in setup tools (check → install → devices → configure)
- Easier for AI assistants to understand the workflow
- Reduces confusion about prerequisites

### Enhanced

#### Improved Tool Descriptions
All fstests tools now include workflow context in their descriptions:
- Setup tools explain their position in the sequence
- Run tools list prerequisites
- VM tool highlighted as the "easy path" for automated testing
- Baseline comparison tools emphasize regression detection

This helps AI assistants understand the correct workflow without trial and error.

## [Previous] - 2024-01-XX

### Added

#### fstests Integration (Complete Feature)
- **10 new MCP tools** for filesystem testing with fstests
  - `check_fstests` - Check installation status
  - `install_fstests` - Clone and build fstests from git
  - `setup_fstests_devices` - Setup test/scratch devices (loop or existing)
  - `configure_fstests` - Create local.config
  - `run_fstests` - Run tests and capture results
  - `list_fstests_groups` - List available test groups
  - `get_fstests_baseline` - Retrieve baseline info
  - `compare_fstests_results` - Compare against baseline for regression detection
  - `list_fstests_baselines` - List all stored baselines
  - `boot_kernel_with_fstests` - Boot kernel with fstests (TODO)

#### New Modules
- `device_manager.py` - Device setup and management (~450 lines)
  - Loop device creation and teardown
  - Filesystem creation and mounting
  - Support for both auto-created and existing devices

- `fstests_manager.py` - Core fstests functionality (~550 lines)
  - Automatic installation from git
  - Build dependency checking
  - Configure script detection and execution
  - Test execution with full parameter support
  - Output parsing (passed/failed/notrun)

- `baseline_manager.py` - Baseline tracking (~350 lines)
  - Baseline storage with metadata
  - Result comparison and regression detection
  - Multiple baseline management
  - Exclude list generation

#### Documentation
- `FSTESTS.md` - Comprehensive fstests guide (300+ lines)
  - Quick start guide
  - Complete tool reference
  - Baseline comparison workflow
  - Testing strategies
  - Best practices
  - Troubleshooting

- `SPARSE_FILES.md` - Sparse file implementation details (200+ lines)
  - What are sparse files and how they work
  - Benefits for fstests device provisioning
  - Resource usage comparison (dense vs sparse)
  - Implementation details and verification
  - Edge cases and limitations
  - Future enhancement possibilities

#### Tests
- `test_device_manager.py` - Device management tests (~500 lines, 40+ tests)
- `test_fstests_manager.py` - fstests manager tests (~750 lines, 58 tests)
  - Added 5 new tests for summary parsing with "Not run" lines
  - Added test for kernel messages interleaved with output
  - Added tests for mixed passed/notrun/failed statuses
  - Added test for parsing check.log with multiple historical runs
  - Added tests for parsing "Failures:" line (single and multiple)
- `test_baseline_manager.py` - Baseline manager tests (~450 lines, 30+ tests)

### Changed

#### boot_kernel_with_fstests - Sparse File Support for Large Devices
- **Switched to sparse files for loop device backing**
  - Device size increased from 256MB to 10GB per device
  - Uses sparse files (allocate space logically, consume disk as data is written)
  - Total logical allocation: 60GB (6 devices × 10GB)
  - Actual disk usage: Only what tests write (typically a few MB)
  - Enables tests requiring large devices (e.g., btrfs/282) to run successfully
  - No impact on VM /tmp capacity since sparse files don't pre-allocate space

### Fixed

#### boot_kernel_with_fstests - Result Parsing from check.log
- **Fixed false negative test reporting**
  - Changed to read results from `results/check.log` file instead of parsing console output
  - Console output had kernel dmesg messages interleaved, breaking line-by-line parsing
  - check.log file provides clean, structured output without kernel messages
  - Added fallback logic to parse summary lines when detailed results unavailable
  - Properly detects passed tests even when kernel logs split output lines

- **Fixed false positive for skipped tests**
  - Parser now checks "Not run:" lines in summary before assuming tests passed
  - Correctly reports tests as "notrun" when they are skipped (e.g., device too small)
  - Summary shows count of not-run tests with reasons (e.g., "1 not run")
  - Prevents reporting "all tests passed" when tests were actually skipped

- **Fixed parser reading entire check.log history**
  - check.log is append-only and contains multiple test runs
  - Parser now splits by empty lines and only parses the last (most recent) entry
  - Prevents results from previous test runs from being included in current results
  - Added test `test_parse_check_log_multiple_runs` to validate behavior

- **Fixed parser not detecting failed tests**
  - Parser was looking for "Failed:" but fstests outputs "Failures:" (plural)
  - Updated regex to match "Failures: test/name" format correctly
  - Tests that fail are now correctly reported as failed instead of passed
  - Added tests `test_parse_check_output_summary_single_failure` and `test_parse_check_output_summary_multiple_failures` to prevent regression

#### boot_kernel_with_fstests - Automatic Device Setup
- **Eliminated need for host-side root permissions**
  - All device setup now happens inside the VM (virtme-ng runs as root)
  - Loop devices created in `/tmp` inside VM
  - Mount points created in `/tmp/test` and `/tmp/scratch` (writable by everyone)
  - No more "Permission denied" errors for /mnt/test

- **Automatic filesystem detection**
  - Detects "btrfs" in test names and uses `mkfs.btrfs`
  - Defaults to `mkfs.ext4` for generic tests
  - Can be extended to support xfs, f2fs, etc.

- **Auto-generated fstests configuration**
  - Creates `local.config` inside VM with correct device paths
  - Uses environment variables from loop device setup
  - No manual configuration required

- **SCRATCH_DEV_POOL support for multi-device tests** (FIXED configuration conflict and space issues)
  - Creates 5 pool devices (256MB each, 1.5GB total) for RAID and multi-device tests
  - Removed separate SCRATCH_DEV (causes conflict with SCRATCH_DEV_POOL)
  - First device in pool serves as primary scratch device
  - Reduced device size from 512MB to 256MB to fit in VM /tmp (was 3GB, now 1.5GB)
  - Pool devices are NOT pre-formatted (tests format them as needed)
  - Enables tests like btrfs/003 that require multiple scratch devices
  - All pool devices automatically cleaned up after tests

- **Integrated cleanup**
  - Automatically unmounts filesystems after tests
  - Detaches all loop devices (test, scratch, and pool)
  - Removes all backing image files
  - VM exits cleanly

#### fstests Build Process (Critical Fixes)
- **Enhanced dependency checking to include development packages**
  - Now checks for required header files (xfs/xfs.h, sys/acl.h, attr/xattr.h)
  - Detects missing development packages before build starts
  - Uses GCC test compilation to verify headers are available
  - Provides package-specific installation instructions for Fedora/RHEL and Debian/Ubuntu

- **Improved configure validation and error handling**
  - Verifies that configure script actually succeeds (not just runs)
  - Checks that `include/builddefs` is created after configure
  - Detects configure failures due to missing dependencies
  - Prevents build from proceeding if configure didn't complete successfully
  - Increased configure timeout from 60s to 120s for slower systems

- **Added post-build binary verification**
  - Verifies that critical binaries (fsstress, aio-dio-regress) were created
  - Detects silent build failures where make succeeds but binaries are missing
  - Provides clear error messages when build is incomplete
  - Prevents boot_kernel_with_fstests from running with incomplete builds

- **Fixed boot_kernel_with_fstests validation**
  - Added check for critical binaries before booting VM
  - Provides helpful rebuild instructions when binaries are missing
  - Prevents "fsstress not found" errors during test execution

- **Fixed duration parsing in fstests output**
  - Corrected regex to properly parse "Ran: 4 tests in 15s" format
  - Changed from `\S+` to `.*?` to handle "tests" keyword in output

#### fstests Installation
- **Added configure script detection and execution**
  - Checks for `./configure` script existence
  - Runs configure before make if present
  - Handles configure failures gracefully

- **Improved build error reporting**
  - Shows both stdout and stderr from build
  - Includes first 1000 characters of error output
  - Provides specific error messages

- **Added dependency checking**
  - Checks for required build tools (make, gcc, git)
  - Provides helpful installation commands for Ubuntu/Debian and Fedora/RHEL
  - Lists all required fstests dependencies

- **Better error handling**
  - Proper timeout handling for configure and build
  - More informative error messages
  - Dependency installation hints on build failure

### Changed

- Updated `install_fstests` tool to check dependencies before installation
- Enhanced build process to handle configure step automatically
- Improved error messages with actionable suggestions

## Implementation Details

### Build Process Flow

**Before:**
```
1. git clone
2. make
```

**After:**
```
1. Check dependencies (make, gcc, git)
2. git clone
3. Check for configure script
4. Run ./configure (if exists)
5. Run make
6. Provide helpful error messages with dependency hints
```

### Error Messages

**Before:**
```
Build failed: [truncated stderr]
```

**After:**
```
Build failed.

Error output:
[first 1000 chars of stderr]

Build output:
[first 1000 chars of stdout]

If build fails due to missing libraries, install fstests dependencies:
  Ubuntu/Debian: sudo apt-get install -y xfslibs-dev uuid-dev libtool-bin ...
  Fedora/RHEL: sudo dnf install -y acl attr automake bc dbench ...
```

### Test Updates

- Added `test_check_build_dependencies_success` - Test dependency checking
- Added `test_check_build_dependencies_missing` - Test missing dependencies
- Added `test_build_with_configure` - Test configure script execution
- Updated `test_install_*` tests to mock dependency checking
- Updated `test_build_failure` to verify error message content

## Testing

All tests pass:
```bash
pytest tests/test_device_manager.py -v     # 40+ tests
pytest tests/test_fstests_manager.py -v    # 37+ tests
pytest tests/test_baseline_manager.py -v   # 30+ tests
```

## Breaking Changes

None - this is a new feature addition.

## Migration Guide

No migration needed. New functionality is opt-in via the new MCP tools.

## Known Issues

- `boot_kernel_with_fstests` tool is marked as TODO and not yet fully implemented
- Requires virtme-ng integration for VM-based testing
