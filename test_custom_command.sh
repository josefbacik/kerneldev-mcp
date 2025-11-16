#!/bin/bash
# Simple test script for fstests_vm_boot_custom feature
# This script demonstrates how to use the custom command functionality

echo "=== Test Script for fstests_vm_boot_custom ==="
echo ""
echo "This script has access to fstests device environment:"
echo "  TEST_DEV: $TEST_DEV"
echo "  SCRATCH_DEV_POOL: $SCRATCH_DEV_POOL"
echo "  LOGWRITES_DEV: $LOGWRITES_DEV"
echo "  FSTYP: $FSTYP"
echo "  RESULT_BASE: $RESULT_BASE"
echo ""

# Verify devices exist
echo "Verifying block devices..."
for dev in $TEST_DEV $LOGWRITES_DEV; do
    if [ -b "$dev" ]; then
        echo "  ✓ $dev exists"
        # Show device info
        blockdev --getsize64 "$dev" | awk '{printf "    Size: %.2f GB\n", $1/1024/1024/1024}'
    else
        echo "  ✗ $dev NOT FOUND"
        exit 1
    fi
done

# Verify pool devices
echo ""
echo "Pool devices:"
for dev in $SCRATCH_DEV_POOL; do
    if [ -b "$dev" ]; then
        echo "  ✓ $dev exists"
    else
        echo "  ✗ $dev NOT FOUND"
        exit 1
    fi
done

# Test filesystem on TEST_DEV
echo ""
echo "Testing filesystem on $TEST_DEV..."
mount "$TEST_DEV" "$TEST_DIR"
if [ $? -eq 0 ]; then
    echo "  ✓ Successfully mounted $TEST_DEV at $TEST_DIR"

    # Write a test file
    echo "Hello from custom command!" > "$TEST_DIR/test.txt"
    if [ $? -eq 0 ]; then
        echo "  ✓ Successfully wrote test file"
        cat "$TEST_DIR/test.txt"
    else
        echo "  ✗ Failed to write test file"
    fi

    # Cleanup
    umount "$TEST_DIR"
    echo "  ✓ Unmounted $TEST_DIR"
else
    echo "  ✗ Failed to mount $TEST_DEV"
    exit 1
fi

# Save results
echo ""
echo "Saving results to $RESULT_BASE..."
mkdir -p "$RESULT_BASE"
cat > "$RESULT_BASE/test-results.txt" <<EOF
Test execution completed successfully
Date: $(date)
Kernel: $(uname -r)
Filesystem: $FSTYP
Devices tested: $TEST_DEV
EOF

echo "  ✓ Results saved"

echo ""
echo "=== Test Completed Successfully ==="
exit 0
