#!/bin/bash
# Test overlayfs behavior with virtiofs

set -x

# Create test directories
TEST_DIR="/tmp/overlay_test_$$"
mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

# Create lower directory with a test file
mkdir -p lower
echo "Test content from lower layer" > lower/testfile.txt
chmod 644 lower/testfile.txt

# Create upper and work directories
mkdir -p upper work merged

# Mount overlay
mount -t overlay overlay \
  -o lowerdir=lower,upperdir=upper,workdir=work \
  merged

# Test 1: Check if file exists in merged
echo "=== Test 1: File exists? ==="
ls -la merged/testfile.txt

# Test 2: Check file content
echo "=== Test 2: File content ==="
cat merged/testfile.txt

# Test 3: Check with hexdump
echo "=== Test 3: Hexdump ==="
hexdump -C merged/testfile.txt | head -2

# Test 4: Check with dd
echo "=== Test 4: DD read ==="
dd if=merged/testfile.txt bs=1 count=10 2>/dev/null | od -c

# Test 5: Check upper directory
echo "=== Test 5: Upper directory ==="
ls -la upper/

# Test 6: Try reading with different methods
echo "=== Test 6: Python read ==="
python3 -c "
import os
path = 'merged/testfile.txt'
print(f'stat: {os.stat(path)}')
with open(path, 'rb') as f:
    data = f.read()
    print(f'Read {len(data)} bytes')
    print(f'First 10 bytes: {data[:10]}')
"

# Cleanup
umount merged
rm -rf "$TEST_DIR"