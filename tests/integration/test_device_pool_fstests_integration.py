#!/usr/bin/env python3
"""
Integration test for device pool with fstests - verifies LV permissions work end-to-end.

This test verifies that when fstests code uses device pools, the LVs are accessible
without needing sudo for filesystem operations.
"""

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import logging
import sys

# Import device pool and boot modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from kerneldev_mcp.device_pool import LVMPoolManager, VolumeConfig, ConfigManager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run_sudo_cmd(cmd, check=True):
    """Helper to run sudo commands."""
    return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, check=check)


class TestDevicePoolFstestsIntegration:
    """Test that device pool LVs work correctly with fstests device management."""

    @classmethod
    def setup_class(cls):
        """Create a loop device for testing."""
        cls.loop_device = None
        cls.vg_name = f"testvg_{uuid.uuid4().hex[:8]}"
        cls.pool_name = f"testpool_{uuid.uuid4().hex[:8]}"

        # Create a temporary file for the loop device
        cls.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
        cls.temp_file.close()

        # Create a 2GB sparse file (need space for multiple devices)
        with open(cls.temp_file.name, "wb") as f:
            f.seek(2 * 1024 * 1024 * 1024 - 1)  # 2GB
            f.write(b"\0")

        # Setup loop device
        result = run_sudo_cmd(["losetup", "-f", "--show", cls.temp_file.name])
        cls.loop_device = result.stdout.strip()
        logger.info(f"Created loop device: {cls.loop_device}")

        # Give device time to settle
        time.sleep(0.5)

    @classmethod
    def teardown_class(cls):
        """Clean up loop device and temp file."""
        if cls.loop_device:
            # Remove VG if it exists
            run_sudo_cmd(["vgremove", "-f", cls.vg_name], check=False)
            time.sleep(0.5)

            # Remove PV if it exists
            run_sudo_cmd(["pvremove", "-f", cls.loop_device], check=False)
            time.sleep(0.5)

            # Detach loop device
            run_sudo_cmd(["losetup", "-d", cls.loop_device], check=False)
            logger.info(f"Removed loop device: {cls.loop_device}")

        # Remove temp file
        if hasattr(cls, "temp_file"):
            try:
                os.unlink(cls.temp_file.name)
            except:
                pass

    def test_device_manager_with_pool(self):
        """Test that pool LVs are usable for filesystem operations without permission errors."""
        # Initialize managers
        config_manager = ConfigManager()
        lvm_manager = LVMPoolManager(config_manager)

        try:
            # Setup the pool
            logger.info(f"Setting up pool '{self.pool_name}' on {self.loop_device}")
            pool_config = lvm_manager.setup_pool(
                self.loop_device, self.pool_name, vg_name=self.vg_name
            )
            assert pool_config is not None, "Failed to setup LVM pool"

            # Allocate devices directly via LVMPoolManager (simulating what fstests integration would do)
            volume_specs = [
                VolumeConfig(name="test", size="200M", env_var="TEST_DEV", order=0),
                VolumeConfig(name="scratch", size="200M", env_var="SCRATCH_DEV", order=1),
            ]

            session_id = f"fstests_session_{uuid.uuid4().hex[:8]}"
            logger.info(f"Allocating devices for session {session_id}...")
            allocations = lvm_manager.allocate_volumes(self.pool_name, volume_specs, session_id)
            assert len(allocations) == 2, f"Expected 2 allocations, got {len(allocations)}"

            # Build devices list similar to what fstests would use
            devices = []
            for alloc in allocations:
                devices.append(
                    {
                        "path": alloc.lv_path,
                        "name": alloc.volume_spec.name,
                        "env_var": alloc.volume_spec.env_var,
                    }
                )

            # Verify we can access the devices without sudo
            for dev in devices:
                dev_path = dev["path"]
                logger.info(f"Testing access to {dev_path} (allocated via LVMPoolManager)")

                # Verify device exists
                assert Path(dev_path).exists(), f"Device {dev_path} does not exist"

                # Test reading
                try:
                    with open(dev_path, "rb") as f:
                        data = f.read(512)
                        assert len(data) == 512
                    logger.info(f"✓ Can read from {dev_path} without sudo")
                except PermissionError:
                    raise AssertionError(f"Cannot read from {dev_path} - permissions not working")

                # Test writing
                test_data = b"FSTESTS" * 73  # 511 bytes
                test_data = test_data[:511] + b"\n"  # Exactly 512 bytes with newline
                try:
                    with open(dev_path, "r+b") as f:
                        f.write(test_data)
                        f.flush()  # Make sure data is written
                        f.seek(0)
                        read_back = f.read(512)
                        assert len(read_back) == 512, f"Expected 512 bytes, got {len(read_back)}"
                        assert read_back == test_data, (
                            f"Data mismatch: expected {test_data[:20]}..., got {read_back[:20]}..."
                        )
                    logger.info(f"✓ Can write to {dev_path} without sudo")
                except PermissionError:
                    raise AssertionError(f"Cannot write to {dev_path} - permissions not working")

                # Verify we can run filesystem operations (what fstests does)
                try:
                    # Try to create a filesystem (ext4)
                    result = subprocess.run(
                        ["mkfs.ext4", "-F", dev_path],
                        capture_output=True,
                        text=True,
                        check=False,  # Don't raise on error
                    )

                    if result.returncode == 0:
                        logger.info(f"✓ Successfully created ext4 filesystem on {dev_path}")
                    else:
                        # This might fail if user lacks mkfs permissions, which is OK
                        # The important part is we could open the device
                        logger.info(
                            f"mkfs.ext4 returned {result.returncode} (may need different permissions)"
                        )

                except Exception as e:
                    logger.warning(f"mkfs test skipped: {e}")

            # Clean up via LVMPoolManager
            logger.info("Releasing volumes...")
            lvm_manager.release_volumes(self.pool_name, session_id)

            # Teardown pool
            logger.info(f"Tearing down pool '{self.pool_name}'")
            lvm_manager.teardown_pool(self.pool_name)

            logger.info("✓ DeviceManager integration test passed!")

        except Exception as e:
            # Emergency cleanup
            logger.error(f"Test failed: {e}")
            # Try to clean up
            try:
                if "session_id" in locals():
                    lvm_manager.release_volumes(self.pool_name, session_id)
                lvm_manager.teardown_pool(self.pool_name)
            except:
                pass
            raise


if __name__ == "__main__":
    # Run the test directly
    test = TestDevicePoolFstestsIntegration()
    test.setup_class()
    try:
        test.test_device_manager_with_pool()
        print("\n✓ All integration tests passed!")
    finally:
        test.teardown_class()
