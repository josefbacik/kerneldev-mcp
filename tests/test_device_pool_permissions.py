#!/usr/bin/env python3
"""
Test device pool LV permissions - verifies user can access LVs without sudo.

This test:
1. Creates a temporary loop device to use as a PV
2. Sets up an LVM pool on it
3. Allocates LVs
4. Verifies the current user can read/write the LVs without sudo
5. Cleans up everything
"""

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import logging

# Import device pool modules
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from kerneldev_mcp.device_pool import LVMPoolManager, VolumeConfig, ConfigManager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run_sudo_cmd(cmd, check=True):
    """Helper to run sudo commands."""
    return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, check=check)


class TestLVPermissions:
    """Test that LVs are accessible to the user without sudo."""

    @classmethod
    def setup_class(cls):
        """Create a loop device for testing."""
        cls.loop_device = None
        cls.vg_name = f"testvg_{uuid.uuid4().hex[:8]}"
        cls.pool_name = f"testpool_{uuid.uuid4().hex[:8]}"

        # Create a temporary file for the loop device
        cls.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
        cls.temp_file.close()

        # Create a 1GB sparse file
        with open(cls.temp_file.name, "wb") as f:
            f.seek(1024 * 1024 * 1024 - 1)  # 1GB
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
            except Exception:
                pass

    def test_lv_user_permissions(self):
        """Test that allocated LVs are accessible to the user without sudo."""
        # Initialize manager (it creates its own state_manager internally)
        config_manager = ConfigManager()
        lvm_manager = LVMPoolManager(config_manager)

        try:
            # Setup the pool
            logger.info(f"Setting up pool '{self.pool_name}' on {self.loop_device}")
            pool_config = lvm_manager.setup_pool(
                self.loop_device, self.pool_name, vg_name=self.vg_name
            )
            assert pool_config is not None, "Failed to setup LVM pool"

            # Define volumes to create
            volume_specs = [
                VolumeConfig(name="test1", size="100M"),
                VolumeConfig(name="test2", size="100M"),
            ]

            # Allocate volumes
            session_id = f"test_session_{uuid.uuid4().hex[:8]}"
            logger.info(f"Allocating volumes for session {session_id}")
            allocations = lvm_manager.allocate_volumes(self.pool_name, volume_specs, session_id)

            assert len(allocations) == 2, f"Expected 2 allocations, got {len(allocations)}"

            # Test each allocated LV
            for alloc in allocations:
                lv_path = alloc.lv_path
                logger.info(f"Testing access to {lv_path}")

                # Verify device exists
                assert Path(lv_path).exists(), f"Device {lv_path} does not exist"

                # Test 1: Can we open the device for reading without sudo?
                try:
                    with open(lv_path, "rb") as f:
                        data = f.read(512)
                        assert len(data) == 512, f"Expected to read 512 bytes, got {len(data)}"
                    logger.info(f"✓ Can read from {lv_path} without sudo")
                except PermissionError:
                    raise AssertionError(
                        f"Cannot read from {lv_path} without sudo - permissions not set correctly"
                    )

                # Test 2: Can we write to the device without sudo?
                test_data = b"TEST" * 128  # 512 bytes
                try:
                    with open(lv_path, "r+b") as f:
                        f.write(test_data)
                        f.seek(0)
                        read_back = f.read(512)
                        assert read_back == test_data, "Written data doesn't match"
                    logger.info(f"✓ Can write to {lv_path} without sudo")
                except PermissionError:
                    raise AssertionError(
                        f"Cannot write to {lv_path} without sudo - permissions not set correctly"
                    )

                # Test 3: Check ownership
                import pwd

                stat_info = os.stat(lv_path)
                uid = stat_info.st_uid
                gid = stat_info.st_gid

                current_uid = os.getuid()
                current_user = pwd.getpwuid(current_uid).pw_name
                owner_user = pwd.getpwuid(uid).pw_name

                logger.info(
                    f"Device owner: {owner_user} (uid={uid}), current user: {current_user} (uid={current_uid})"
                )

                # We should either own the device OR have access through group
                if uid != current_uid:
                    # Check if we're in the same group
                    import grp

                    group_name = grp.getgrgid(gid).gr_name
                    user_groups = [g.gr_name for g in grp.getgrall() if current_user in g.gr_mem]

                    # Also check primary group
                    primary_group = grp.getgrgid(pwd.getpwuid(current_uid).pw_gid).gr_name
                    user_groups.append(primary_group)

                    logger.info(f"Device group: {group_name}, user groups: {user_groups}")
                    # We've already verified we can read/write, so permissions are working

            # Clean up volumes
            logger.info(f"Releasing volumes for session {session_id}")
            lvm_manager.release_volumes(self.pool_name, session_id)

            # Teardown pool
            logger.info(f"Tearing down pool '{self.pool_name}'")
            lvm_manager.teardown_pool(self.pool_name)

            logger.info("✓ All permission tests passed!")

        except Exception as e:
            # Emergency cleanup
            logger.error(f"Test failed: {e}")
            # Try to clean up
            try:
                lvm_manager.teardown_pool(self.pool_name)
            except Exception:
                pass
            raise


if __name__ == "__main__":
    # Run the test directly
    test = TestLVPermissions()
    test.setup_class()
    try:
        test.test_lv_user_permissions()
        print("\n✓ All tests passed!")
    finally:
        test.teardown_class()
