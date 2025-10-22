#!/usr/bin/env python3
"""
Integration test for kernel boot validation with virtme-ng.

This test validates that the boot_manager can successfully boot a kernel
using virtme-ng and capture/analyze the dmesg output.
"""
import sys
from pathlib import Path
from kerneldev_mcp.boot_manager import BootManager, format_boot_result


def test_kernel_boot():
    """Test booting the kernel at ~/linux."""
    kernel_path = Path.home() / "linux"

    if not kernel_path.exists():
        print(f"ERROR: Kernel path does not exist: {kernel_path}")
        return False

    print(f"Testing kernel boot with virtme-ng")
    print(f"Kernel path: {kernel_path}")
    print("")

    # Create boot manager
    boot_manager = BootManager(kernel_path)

    # Check virtme-ng is available
    if not boot_manager.check_virtme_ng():
        print("ERROR: virtme-ng is not available")
        print("Install with: pip install virtme-ng")
        return False

    print("✓ virtme-ng is available")
    print("")

    # Check if kernel is built
    vmlinux_path = kernel_path / "vmlinux"
    use_host = not vmlinux_path.exists()

    if use_host:
        print("WARNING: Kernel is not built (vmlinux not found)")
        print("Using host kernel instead...")
        print("")
    else:
        print("✓ Kernel is built")
        print("")

    # Run boot test
    print("Running boot test...")
    print(f"  Using: {'Host kernel' if use_host else 'Local kernel'}")
    print("  Timeout: 90 seconds")
    print("  Memory: 2G")
    print("  CPUs: 2")
    print("")

    result = boot_manager.boot_test(
        timeout=90,
        memory="2G",
        cpus=2,
        use_host_kernel=use_host
    )

    # Display results
    print("=" * 80)
    print("BOOT TEST RESULTS")
    print("=" * 80)
    print("")
    print(format_boot_result(result, max_errors=15))
    print("")

    # Detailed analysis
    if result.boot_completed:
        print("=" * 80)
        print("BOOT ANALYSIS")
        print("=" * 80)
        print(f"  Boot duration: {result.duration:.2f} seconds")
        print(f"  Kernel version: {result.kernel_version or 'Unknown'}")
        print(f"  Exit code: {result.exit_code}")
        print("")
        print(f"  Errors found: {result.error_count}")
        print(f"  Warnings found: {result.warning_count}")
        print(f"  Panics found: {result.panic_count}")
        print(f"  Oops found: {result.oops_count}")
        print("")

        if result.has_critical_issues:
            print("  ⚠ CRITICAL ISSUES DETECTED!")
        elif result.error_count > 0:
            print("  ⚠ Boot completed with errors")
        elif result.warning_count > 0:
            print("  ✓ Boot completed with warnings")
        else:
            print("  ✓ Clean boot - no issues detected")
        print("")

        # Show sample dmesg
        if result.dmesg_output:
            print("=" * 80)
            print("DMESG OUTPUT (first 30 lines)")
            print("=" * 80)
            lines = result.dmesg_output.splitlines()[:30]
            for line in lines:
                print(line)
            if len(result.dmesg_output.splitlines()) > 30:
                remaining = len(result.dmesg_output.splitlines()) - 30
                print(f"... ({remaining} more lines)")
            print("")

    # Show dmesg output even on failure for debugging
    if not result.boot_completed:
        print("=" * 80)
        print("DEBUG: FAILURE OUTPUT")
        print("=" * 80)
        print(result.dmesg_output)
        print("")

    # Determine success
    success = result.boot_completed and not result.has_critical_issues

    if success:
        print("=" * 80)
        print("✓✓✓ BOOT TEST SUCCESSFUL ✓✓✓")
        print("=" * 80)
        print("")
        print("The kernel booted successfully and no critical issues were detected.")
        print(f"Boot completed in {result.duration:.2f} seconds.")
        if result.error_count > 0 or result.warning_count > 0:
            print(f"Note: Found {result.error_count} errors and {result.warning_count} warnings.")
            print("These may be normal depending on your configuration.")
    else:
        print("=" * 80)
        print("✗✗✗ BOOT TEST FAILED ✗✗✗")
        print("=" * 80)
        print("")
        if not result.boot_completed:
            print("The kernel failed to boot or timed out.")
        elif result.has_critical_issues:
            print("The kernel booted but encountered critical issues (panics or oops).")

    return success


if __name__ == "__main__":
    success = test_kernel_boot()
    sys.exit(0 if success else 1)
