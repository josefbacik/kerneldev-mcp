"""
Tests for SafetyValidator in device_pool module.

These tests verify the comprehensive 10-point safety checklist.
"""

import pytest
from unittest.mock import Mock, patch, mock_open

from kerneldev_mcp.device_pool import SafetyValidator, ValidationLevel, ValidationResult


@pytest.fixture
def validator():
    """Create SafetyValidator instance."""
    return SafetyValidator()


class TestSafetyValidator:
    """Test SafetyValidator comprehensive checks."""

    def test_check_exists_and_is_block_device_nonexistent(self, validator):
        """Test check fails for nonexistent device."""
        result = validator._check_exists_and_is_block_device("/dev/nonexistent_device_xyz")
        assert result.level == ValidationLevel.ERROR
        assert "does not exist" in result.message

    @patch("os.path.exists")
    @patch("os.stat")
    def test_check_exists_and_is_block_device_not_block(self, mock_stat, mock_exists, validator):
        """Test check fails for non-block device."""
        mock_exists.return_value = True

        # Create a mock stat result for a regular file
        import stat

        mock_st = Mock()
        mock_st.st_mode = stat.S_IFREG | 0o644  # Regular file
        mock_stat.return_value = mock_st

        result = validator._check_exists_and_is_block_device("/dev/fake")
        assert result.level == ValidationLevel.ERROR
        assert "not a block device" in result.message

    @patch("os.path.exists")
    @patch("os.stat")
    def test_check_exists_and_is_block_device_success(self, mock_stat, mock_exists, validator):
        """Test check passes for valid block device."""
        mock_exists.return_value = True

        # Create a mock stat result for a block device
        import stat

        mock_st = Mock()
        mock_st.st_mode = stat.S_IFBLK | 0o660  # Block device
        mock_stat.return_value = mock_st

        result = validator._check_exists_and_is_block_device("/dev/fake")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_not_mounted_is_mounted(self, mock_run, validator):
        """Test check fails when device is mounted."""
        # Mock findmnt output showing device is mounted
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "/mnt/test\n"
        mock_run.return_value = mock_result

        result = validator._check_not_mounted("/dev/sdb")
        assert result.level == ValidationLevel.ERROR
        assert "mounted" in result.message.lower()

    @patch("subprocess.run")
    def test_check_not_mounted_not_mounted(self, mock_run, validator):
        """Test check passes when device is not mounted."""
        # Mock findmnt output showing device is not mounted
        mock_result_1 = Mock()
        mock_result_1.returncode = 1  # Not found
        mock_result_1.stdout = ""

        mock_result_2 = Mock()
        mock_result_2.returncode = 0
        mock_result_2.stdout = "/dev/sda1 /\n"  # Different device

        mock_run.side_effect = [mock_result_1, mock_result_2]

        result = validator._check_not_mounted("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("builtins.open", new_callable=mock_open, read_data="/dev/sda1 / ext4 defaults 0 1\n")
    def test_check_not_in_fstab_not_present(self, mock_file, validator):
        """Test check passes when device not in fstab."""
        result = validator._check_not_in_fstab("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("builtins.open", new_callable=mock_open, read_data="/dev/sdb1 /data ext4 defaults 0 1\n")
    def test_check_not_in_fstab_is_present(self, mock_file, validator):
        """Test check fails when device in fstab."""
        result = validator._check_not_in_fstab("/dev/sdb1")
        assert result.level == ValidationLevel.ERROR
        assert "fstab" in result.message.lower()

    @patch("subprocess.run")
    def test_check_not_system_disk_is_system(self, mock_run, validator):
        """Test check fails when device contains system partition."""
        # Mock findmnt showing device contains root partition
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "/dev/sda2\n"

        mock_run.return_value = mock_result

        result = validator._check_not_system_disk("/dev/sda")
        assert result.level == ValidationLevel.ERROR
        assert "system partition" in result.message.lower()

    @patch("subprocess.run")
    def test_check_not_system_disk_not_system(self, mock_run, validator):
        """Test check passes when device is not system disk."""
        # Mock findmnt showing different device for system mounts
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "/dev/nvme0n1p2\n"

        mock_run.return_value = mock_result

        result = validator._check_not_system_disk("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_not_raid_member_is_raid(self, mock_run, validator):
        """Test check fails when device is RAID member."""
        mock_result = Mock()
        mock_result.returncode = 0  # mdadm --examine succeeded
        mock_run.return_value = mock_result

        result = validator._check_not_raid_member("/dev/sdb")
        assert result.level == ValidationLevel.ERROR
        assert "RAID" in result.message

    @patch("subprocess.run")
    def test_check_not_raid_member_not_raid(self, mock_run, validator):
        """Test check passes when device is not RAID member."""
        mock_result = Mock()
        mock_result.returncode = 1  # mdadm --examine failed (not a RAID member)
        mock_run.return_value = mock_result

        result = validator._check_not_raid_member("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_not_lvm_pv_is_pv(self, mock_run, validator):
        """Test check fails when device is LVM PV."""
        mock_result = Mock()
        mock_result.returncode = 0  # pvdisplay succeeded
        mock_run.return_value = mock_result

        result = validator._check_not_lvm_pv("/dev/sdb")
        assert result.level == ValidationLevel.ERROR
        assert "LVM" in result.message

    @patch("subprocess.run")
    def test_check_not_lvm_pv_not_pv(self, mock_run, validator):
        """Test check passes when device is not LVM PV."""
        mock_result = Mock()
        mock_result.returncode = 5  # pvdisplay failed (not a PV)
        mock_run.return_value = mock_result

        result = validator._check_not_lvm_pv("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_not_encrypted_is_luks(self, mock_run, validator):
        """Test check fails when device is LUKS encrypted."""
        mock_result = Mock()
        mock_result.returncode = 0  # cryptsetup isLuks succeeded
        mock_run.return_value = mock_result

        result = validator._check_not_encrypted("/dev/sdb")
        assert result.level == ValidationLevel.ERROR
        assert "encrypted" in result.message.lower()

    @patch("subprocess.run")
    def test_check_not_encrypted_not_encrypted(self, mock_run, validator):
        """Test check passes when device is not encrypted."""
        mock_result = Mock()
        mock_result.returncode = 1  # cryptsetup isLuks failed (not LUKS)
        mock_run.return_value = mock_result

        result = validator._check_not_encrypted("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_no_open_handles_has_handles(self, mock_run, validator):
        """Test check fails when device has open handles."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "qemu-system 12345 user  3u  BLK  8,16 /dev/sdb\n"
        mock_run.return_value = mock_result

        result = validator._check_no_open_handles("/dev/sdb")
        assert result.level == ValidationLevel.ERROR
        assert "open file handles" in result.message.lower()

    @patch("subprocess.run")
    def test_check_no_open_handles_no_handles(self, mock_run, validator):
        """Test check passes when device has no open handles."""
        mock_result = Mock()
        mock_result.returncode = 1  # lsof returns 1 when no matches
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = validator._check_no_open_handles("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_filesystem_signatures_has_fs(self, mock_run, validator):
        """Test check warns when device has filesystem."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = '/dev/sdb: TYPE="ext4" UUID="abc-123"\n'
        mock_run.return_value = mock_result

        result = validator._check_filesystem_signatures("/dev/sdb")
        assert result.level == ValidationLevel.WARNING
        assert "signatures" in result.message.lower()

    @patch("subprocess.run")
    def test_check_filesystem_signatures_no_fs(self, mock_run, validator):
        """Test check passes when device has no filesystem."""
        mock_result = Mock()
        mock_result.returncode = 2  # blkid returns 2 when no signatures found
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = validator._check_filesystem_signatures("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    def test_check_partition_table_has_table(self, mock_run, validator):
        """Test check warns when device has partition table."""
        mock_result = Mock()
        mock_result.returncode = 0  # sgdisk succeeded
        mock_result.stdout = "Partition table of /dev/sdb...\n"
        mock_run.return_value = mock_result

        result = validator._check_partition_table("/dev/sdb")
        assert result.level == ValidationLevel.WARNING
        assert "partition table" in result.message.lower()

    @patch("subprocess.run")
    def test_check_partition_table_no_table(self, mock_run, validator):
        """Test check passes when device has no partition table."""
        mock_result = Mock()
        mock_result.returncode = 2  # sgdisk failed (no partition table)
        mock_run.return_value = mock_result

        result = validator._check_partition_table("/dev/sdb")
        assert result.level == ValidationLevel.OK

    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.stat")
    def test_validate_device_comprehensive(self, mock_stat, mock_exists, mock_run, validator):
        """Test comprehensive validation with all checks."""
        # Setup mocks for successful validation
        mock_exists.return_value = True

        import stat

        mock_st = Mock()
        mock_st.st_mode = stat.S_IFBLK | 0o660
        mock_stat.return_value = mock_st

        # Mock all subprocess calls to return success
        mock_result = Mock()
        mock_result.returncode = 1  # Most checks expect non-zero for "safe"
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = validator.validate_device("/dev/sdb")

        # Should get OK or WARNING (not ERROR)
        assert result.is_safe
        assert "/dev/sdb" in result.message

    @patch("os.path.exists")
    def test_validate_device_nonexistent_fails(self, mock_exists, validator):
        """Test validation fails for nonexistent device."""
        mock_exists.return_value = False

        result = validator.validate_device("/dev/nonexistent")

        assert result.level == ValidationLevel.ERROR
        assert not result.is_safe
        assert "does not exist" in result.message


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_validation_result_ok_is_safe(self):
        """Test OK result is safe."""
        result = ValidationResult(ValidationLevel.OK, "All good")
        assert result.is_safe
        assert not result.is_error

    def test_validation_result_warning_is_safe(self):
        """Test WARNING result is safe."""
        result = ValidationResult(ValidationLevel.WARNING, "Be careful")
        assert result.is_safe
        assert not result.is_error

    def test_validation_result_error_not_safe(self):
        """Test ERROR result is not safe."""
        result = ValidationResult(ValidationLevel.ERROR, "Failed")
        assert not result.is_safe
        assert result.is_error

    def test_validation_result_with_details(self):
        """Test ValidationResult with details."""
        details = {"device": "/dev/sdb", "issue": "mounted"}
        result = ValidationResult(ValidationLevel.ERROR, "Device is mounted", details=details)
        assert result.details == details
        assert result.details["device"] == "/dev/sdb"
