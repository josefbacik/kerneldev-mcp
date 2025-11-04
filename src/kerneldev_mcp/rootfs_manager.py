"""
Manage custom root filesystems for kernel testing.

This module creates and manages minimal Ubuntu-based root filesystems
with pre-configured test users (fsqa, fsgqa) for running fstests and
other kernel tests in an isolated environment.
"""
import logging
import os
import subprocess
import shutil
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class RootfsManager:
    """Manages custom root filesystems for kernel testing."""

    # Default location for test rootfs
    DEFAULT_ROOTFS_DIR = Path.home() / ".kerneldev-mcp" / "test-rootfs"

    # Ubuntu release to use (LTS releases recommended)
    DEFAULT_UBUNTU_RELEASE = "jammy"  # Ubuntu 22.04 LTS

    # Required test users
    TEST_USERS = {
        "fsqa": {"uid": 1000, "gid": 1000, "groups": ["fsgqa"]},
        "fsgqa2": {"uid": 1001, "gid": 1001, "groups": []},
    }

    TEST_GROUPS = {
        "fsgqa": {"gid": 1002},
    }

    def __init__(self, rootfs_path: Optional[Path] = None):
        """Initialize rootfs manager.

        Args:
            rootfs_path: Path to rootfs directory (default: ~/.kerneldev-mcp/test-rootfs)
        """
        self.rootfs_path = Path(rootfs_path) if rootfs_path else self.DEFAULT_ROOTFS_DIR

    def check_exists(self) -> bool:
        """Check if rootfs exists and appears valid.

        Returns:
            True if rootfs exists and has basic structure
        """
        if not self.rootfs_path.exists():
            return False

        # Check for essential directories
        essential_dirs = ["bin", "etc", "usr", "var"]
        for dir_name in essential_dirs:
            if not (self.rootfs_path / dir_name).exists():
                return False

        # Check for passwd file
        passwd_file = self.rootfs_path / "etc" / "passwd"
        if not passwd_file.exists():
            return False

        return True

    def check_configured(self) -> Tuple[bool, str]:
        """Check if rootfs has required test users configured.

        Returns:
            Tuple of (is_configured, status_message)
        """
        if not self.check_exists():
            return False, "Rootfs does not exist"

        passwd_file = self.rootfs_path / "etc" / "passwd"
        group_file = self.rootfs_path / "etc" / "group"

        try:
            passwd_content = passwd_file.read_text()
            group_content = group_file.read_text()

            # Check for required users
            missing_users = []
            for username in self.TEST_USERS.keys():
                if f"{username}:" not in passwd_content:
                    missing_users.append(username)

            # Check for required groups
            missing_groups = []
            for groupname in self.TEST_GROUPS.keys():
                if f"{groupname}:" not in group_content:
                    missing_groups.append(groupname)

            if missing_users or missing_groups:
                msg = "Missing configuration:"
                if missing_users:
                    msg += f" users={','.join(missing_users)}"
                if missing_groups:
                    msg += f" groups={','.join(missing_groups)}"
                return False, msg

            return True, "Rootfs configured with test users"

        except OSError as e:
            return False, f"Error reading configuration: {e}"

    def check_virtme_ng(self) -> bool:
        """Check if virtme-ng is installed.

        Returns:
            True if virtme-ng is available
        """
        try:
            result = subprocess.run(
                ["vng", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def create_rootfs(
        self,
        ubuntu_release: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """Create a new rootfs using virtme-ng.

        Args:
            ubuntu_release: Ubuntu release name (default: jammy)
            force: Force recreation if rootfs already exists

        Returns:
            Tuple of (success, message)
        """
        logger.info("=" * 60)
        logger.info(f"Creating test rootfs at {self.rootfs_path}")

        # Check if virtme-ng is available
        if not self.check_virtme_ng():
            return False, "virtme-ng (vng) not found. Install with: pip install virtme-ng"

        # Check if rootfs already exists
        if self.rootfs_path.exists():
            if not force:
                return False, f"Rootfs already exists at {self.rootfs_path}. Use force=True to recreate."

            logger.info(f"Removing existing rootfs at {self.rootfs_path}")
            try:
                shutil.rmtree(self.rootfs_path)
            except OSError as e:
                return False, f"Failed to remove existing rootfs: {e}"

        # Create parent directory
        self.rootfs_path.parent.mkdir(parents=True, exist_ok=True)

        # Use virtme-ng to create Ubuntu chroot
        release = ubuntu_release or self.DEFAULT_UBUNTU_RELEASE
        logger.info(f"Creating Ubuntu {release} rootfs (this may take 5-10 minutes)...")
        logger.info("  Downloading base system packages...")

        # Create a simple script to run inside the new rootfs
        # This will be used to set up users after the rootfs is created
        setup_script = """#!/bin/bash
set -e

echo "Setting up test users in rootfs..."

# Create groups first
groupadd -g 1002 fsgqa || true

# Create users
useradd -u 1000 -g 1000 -m -s /bin/bash fsqa || true
useradd -u 1001 -g 1001 -m -s /bin/bash fsgqa2 || true

# Add fsqa to fsgqa group
usermod -a -G fsgqa fsqa || true

# Set simple passwords (for debugging, not security)
echo "fsqa:fsqa" | chpasswd
echo "fsgqa2:fsgqa2" | chpasswd

# Install essential packages
apt-get update -qq
apt-get install -y --no-install-recommends \
    bash coreutils util-linux procps \
    sudo acl attr quota xfsprogs e2fsprogs btrfs-progs \
    2>&1 | grep -v "^Get:" | grep -v "^Fetched" || true

echo "Test users configured successfully"
"""

        # Save setup script to temp file
        temp_script = Path("/tmp/rootfs-setup.sh")
        temp_script.write_text(setup_script)
        temp_script.chmod(0o755)

        try:
            # Use vng to create and configure the rootfs
            # We'll use --root-release to create the base Ubuntu chroot
            # Then run our setup script inside it

            # First, create the base rootfs by running a simple command
            logger.info("  Creating base Ubuntu rootfs...")
            result = subprocess.run(
                [
                    "vng",
                    "--root-release", release,
                    "--root", str(self.rootfs_path),
                    "--verbose",
                    "--", "true"  # Just run 'true' to initialize the rootfs
                ],
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes
            )

            if result.returncode != 0:
                error_msg = f"Failed to create base rootfs:\n{result.stderr}"
                # Check for common errors
                if "debootstrap" in result.stderr.lower():
                    error_msg += "\n\nMake sure debootstrap is installed:\n"
                    error_msg += "  Ubuntu/Debian: sudo apt-get install debootstrap\n"
                    error_msg += "  Fedora/RHEL: sudo dnf install debootstrap"
                return False, error_msg

            logger.info("  Base rootfs created successfully")
            logger.info("  Configuring test users...")

            # Now configure the rootfs by running our setup script
            # We use chroot directly here instead of vng to avoid kernel boot
            result = subprocess.run(
                [
                    "sudo", "chroot", str(self.rootfs_path),
                    "/bin/bash", "-c",
                    setup_script
                ],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes
            )

            if result.returncode != 0:
                logger.warning(f"Setup script had some errors, but continuing...")
                logger.warning(f"Output: {result.stdout}")
                logger.warning(f"Errors: {result.stderr}")

            # Verify users were created
            is_configured, msg = self.check_configured()
            if not is_configured:
                return False, f"Rootfs created but user configuration failed: {msg}"

            logger.info("✓ Rootfs created and configured successfully")
            logger.info(f"  Location: {self.rootfs_path}")
            logger.info(f"  Size: {self._get_directory_size(self.rootfs_path)}")
            logger.info("=" * 60)

            return True, f"Successfully created rootfs at {self.rootfs_path}"

        except subprocess.TimeoutExpired:
            return False, "Rootfs creation timed out"
        except Exception as e:
            return False, f"Error creating rootfs: {e}"
        finally:
            # Cleanup temp script
            if temp_script.exists():
                try:
                    temp_script.unlink()
                except OSError:
                    pass

    def _get_directory_size(self, path: Path) -> str:
        """Get human-readable size of a directory.

        Args:
            path: Directory path

        Returns:
            Size string (e.g., "450M")
        """
        try:
            result = subprocess.run(
                ["du", "-sh", str(path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                # Output format: "450M\t/path/to/dir"
                size = result.stdout.split()[0]
                return size
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
            pass

        return "unknown"

    def delete_rootfs(self) -> Tuple[bool, str]:
        """Delete the rootfs.

        Returns:
            Tuple of (success, message)
        """
        if not self.rootfs_path.exists():
            return False, f"Rootfs does not exist at {self.rootfs_path}"

        try:
            logger.info(f"Deleting rootfs at {self.rootfs_path}")
            shutil.rmtree(self.rootfs_path)
            return True, f"Successfully deleted rootfs at {self.rootfs_path}"
        except OSError as e:
            return False, f"Failed to delete rootfs: {e}"

    def get_info(self) -> dict:
        """Get information about the rootfs.

        Returns:
            Dictionary with rootfs information
        """
        info = {
            "path": str(self.rootfs_path),
            "exists": self.check_exists(),
            "configured": False,
            "size": None,
            "ubuntu_release": None,
            "users": [],
        }

        if not info["exists"]:
            return info

        is_configured, msg = self.check_configured()
        info["configured"] = is_configured
        info["size"] = self._get_directory_size(self.rootfs_path)

        # Try to determine Ubuntu release
        lsb_release = self.rootfs_path / "etc" / "lsb-release"
        if lsb_release.exists():
            try:
                content = lsb_release.read_text()
                for line in content.splitlines():
                    if line.startswith("DISTRIB_CODENAME="):
                        info["ubuntu_release"] = line.split("=")[1].strip()
                        break
            except OSError:
                pass

        # Get list of test users
        passwd_file = self.rootfs_path / "etc" / "passwd"
        if passwd_file.exists():
            try:
                content = passwd_file.read_text()
                for username in self.TEST_USERS.keys():
                    if f"{username}:" in content:
                        info["users"].append(username)
            except OSError:
                pass

        return info
