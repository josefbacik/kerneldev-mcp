# fstests Build Process Fixes

## Problem Summary

When testing the `boot_kernel_with_fstests` tool, we encountered a failure where fstests appeared to be installed but critical test binaries were missing, causing the error:

```
fsstress not found or executable
```

## Root Cause Analysis

Investigation revealed multiple issues in the build process:

1. **Configure Script Failure**: The configure script was being interrupted (signal 13 = SIGPIPE) and failing silently
2. **Missing Development Packages**: Required development packages were not installed:
   - `xfsprogs-devel` (XFS headers)
   - `libacl-devel` (ACL headers)
   - `libattr-devel` (extended attributes)
   - `liburing-devel` (io_uring support)
3. **Incomplete Build Validation**: The build() function didn't verify that:
   - Configure actually succeeded
   - Required files like `include/builddefs` were created
   - Critical binaries were actually compiled

## Implemented Fixes

### 1. Enhanced Dependency Checking

**File**: `src/kerneldev_mcp/fstests_manager.py` (lines 226-270)

**Changes**:
- Added header file checking using GCC test compilation
- Checks for `xfs/xfs.h`, `sys/acl.h`, and `attr/xattr.h`
- Provides package-specific installation instructions

**Before**:
```python
def check_build_dependencies(self) -> Tuple[bool, List[str]]:
    required_tools = ["make", "gcc", "git"]
    # Only checked for command-line tools
```

**After**:
```python
def check_build_dependencies(self) -> Tuple[bool, List[str]]:
    required_tools = ["make", "gcc", "git"]
    # Check tools...

    # Also check for development package headers
    required_headers = {
        "xfs/xfs.h": "xfsprogs-devel (Fedora/RHEL) or xfslibs-dev (Debian/Ubuntu)",
        "sys/acl.h": "libacl-devel (Fedora/RHEL) or libacl1-dev (Debian/Ubuntu)",
        "attr/xattr.h": "libattr-devel (Fedora/RHEL) or libattr1-dev (Debian/Ubuntu)",
    }

    # Test compile to verify headers exist
    for header, package_info in required_headers.items():
        test_program = f'#include <{header}>\nint main() {{ return 0; }}'
        result = subprocess.run(
            ["gcc", "-x", "c", "-c", "-o", "/dev/null", "-"],
            input=test_program, ...
        )
```

### 2. Improved Configure Validation

**File**: `src/kerneldev_mcp/fstests_manager.py` (lines 332-428)

**Changes**:
- Verify configure exits with returncode 0
- Check that `include/builddefs` is created
- Parse configure output for common error patterns
- Increased timeout from 60s to 120s
- Provide helpful error messages with dependency hints

**Before**:
```python
if configure_script.exists():
    result = subprocess.run(["./configure"], ...)
    if result.returncode != 0:
        pass  # Ignored errors!
```

**After**:
```python
if configure_script.exists():
    result = subprocess.run(["./configure"], timeout=120, ...)

    if result.returncode != 0:
        error_msg = "Configure failed.\n\n"
        output = result.stdout + result.stderr
        if "xfs/xfs.h" in output or "FATAL ERROR" in output:
            error_msg += "Missing required development packages.\n\n"
            error_msg += "Install dependencies:\n"
            error_msg += "  Fedora/RHEL: sudo dnf install -y xfsprogs-devel ..."
        return False, error_msg

    # Verify builddefs was created
    if not builddefs_file.exists():
        return False, "Configure completed but failed to create include/builddefs..."
```

### 3. Post-Build Binary Verification

**File**: `src/kerneldev_mcp/fstests_manager.py` (lines 414-426)

**Changes**:
- Verify critical binaries exist after build
- Check that binaries are executable
- Provide clear error messages when build is incomplete

**Before**:
```python
def build(self) -> Tuple[bool, str]:
    # ... run make ...
    if result.returncode != 0:
        return False, error_msg
    return True, "Build successful"  # Assumed binaries exist!
```

**After**:
```python
def build(self) -> Tuple[bool, str]:
    # ... run make ...

    # Verify that critical binaries were built
    critical_binaries = ["ltp/fsstress", "src/aio-dio-regress"]
    missing_binaries = []
    for binary in critical_binaries:
        binary_path = self.fstests_path / binary
        if not binary_path.exists() or not os.access(binary_path, os.X_OK):
            missing_binaries.append(binary)

    if missing_binaries:
        return False, (
            f"Build completed but critical binaries were not created: {', '.join(missing_binaries)}\n"
            "This usually means the build failed silently. Check that all dependencies are installed."
        )

    return True, "Build successful"
```

### 4. Boot-Time Validation

**File**: `src/kerneldev_mcp/boot_manager.py` (lines 685-706)

**Changes**:
- Check for critical binaries before booting VM
- Provide rebuild instructions if binaries are missing

**Before**:
```python
# Only checked if fstests directory exists
if not fstests_path.exists() or not (fstests_path / "check").exists():
    return error...
```

**After**:
```python
# Check fstests exists
if not fstests_path.exists() or not (fstests_path / "check").exists():
    return error...

# Verify that fstests is fully built
critical_binaries = [
    fstests_path / "ltp" / "fsstress",
    fstests_path / "src" / "aio-dio-regress",
]
missing_binaries = []
for binary in critical_binaries:
    if not binary.exists() or not os.access(binary, os.X_OK):
        missing_binaries.append(str(binary.relative_to(fstests_path)))

if missing_binaries:
    return BootResult(
        success=False,
        dmesg_output=(
            f"ERROR: fstests is not fully built. Missing binaries: {', '.join(missing_binaries)}\n"
            f"Run the install_fstests tool to rebuild fstests, or manually run:\n"
            f"  cd {fstests_path} && ./configure && make -j$(nproc)"
        ),
        exit_code=-1
    )
```

### 5. Fixed Duration Parsing

**File**: `src/kerneldev_mcp/fstests_manager.py` (line 528)

**Changes**:
- Fixed regex to properly parse "Ran: 4 tests in 15s" format

**Before**:
```python
duration_match = re.search(r'Ran:\s+\S+\s+in\s+(\d+)s', output)
# Failed to match "Ran: 4 tests in 15s" because \S+ doesn't match "4 tests"
```

**After**:
```python
# Matches "Ran: 4 tests in 15s" or "Ran: 4 in 15s"
duration_match = re.search(r'Ran:\s+.*?\s+in\s+(\d+)s', output)
```

## Test Updates

**File**: `tests/test_fstests_manager.py`

Added/Updated tests:
- `test_check_build_dependencies_success` - Updated to handle header checking
- `test_check_build_dependencies_missing_tools` - Tests missing command-line tools
- `test_check_build_dependencies_missing_headers` - Tests missing development packages (NEW)
- `test_build_configure_failure` - Tests configure failure with helpful errors (NEW)
- `test_build_configure_no_builddefs` - Tests missing builddefs detection (NEW)
- `test_build_missing_binaries` - Tests binary verification (NEW)
- `test_install_success` - Updated to mock build() method

All 50 tests in test_fstests_manager.py now pass.

## Impact

These fixes ensure that:

1. **Users get early warnings** about missing dependencies before build starts
2. **Configure failures are detected** and reported with helpful error messages
3. **Incomplete builds are caught** before attempting to run tests
4. **Clear instructions** are provided for fixing issues
5. **No silent failures** - every error is detected and reported

## Example Error Messages

### Before Fix
```
ERROR: fsstress not found or executable
```

### After Fix
```
ERROR: fstests is not fully built. Missing binaries: ltp/fsstress, src/aio-dio-regress

This usually means the build failed silently. Check that all dependencies are installed.

Run the install_fstests tool to rebuild fstests, or manually run:
  cd /home/user/.kerneldev-mcp/fstests && ./configure && make -j4
```

Or during dependency checking:
```
Missing required build tools: xfs/xfs.h (xfsprogs-devel (Fedora/RHEL) or xfslibs-dev (Debian/Ubuntu))

Install with:
  Fedora/RHEL: sudo dnf install -y xfsprogs-devel libacl-devel libattr-devel
  Ubuntu/Debian: sudo apt-get install -y xfslibs-dev libacl1-dev libattr1-dev
```

## Testing

The fixes were validated by:

1. Running all 50 unit tests in `test_fstests_manager.py` - all pass
2. Testing with missing development packages - proper error messages shown
3. Testing with incomplete builds - detected and reported
4. Testing boot_kernel_with_fstests - now validates binaries before running

## Files Modified

- `src/kerneldev_mcp/fstests_manager.py` - Core build logic improvements
- `src/kerneldev_mcp/boot_manager.py` - Pre-boot validation
- `tests/test_fstests_manager.py` - Updated and new tests
- `CHANGELOG.md` - Documented all changes
