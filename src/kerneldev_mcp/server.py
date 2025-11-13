"""
MCP server for kernel development configuration management.
"""
import atexit
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server import Server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)

from .config_manager import ConfigManager, KernelConfig, CrossCompileConfig
from .templates import TemplateManager
from .build_manager import KernelBuilder, BuildResult, format_build_errors
from .boot_manager import BootManager, BootResult, format_boot_result
from .device_manager import DeviceManager, DeviceConfig, DeviceSetupResult
from .fstests_manager import (
    FstestsManager, FstestsConfig, FstestsRunResult, TestResult,
    format_fstests_result
)
from .baseline_manager import (
    BaselineManager, Baseline, ComparisonResult,
    format_comparison_result
)
from .git_manager import GitManager, GitNoteMetadata

# Configure logging - log to both file and stderr
# File logging allows tailing progress: tail -f /tmp/kerneldev-mcp.log
# Stderr logging may be captured by MCP client (Claude Code)
log_file = Path("/tmp/kerneldev-mcp.log")
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a'),  # Append mode
        logging.StreamHandler()  # stderr - may show in MCP client
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info(f"kerneldev-mcp server starting")
logger.info(f"Log file: {log_file}")
logger.info(f"Tip: Monitor progress with: tail -f {log_file}")
logger.info("=" * 80)

# Initialize server
app = Server("kerneldev-mcp")

# Initialize managers
template_manager = TemplateManager()

# Register cleanup handler to remove tracking file on exit
def _cleanup_on_exit():
    """Clean up VM tracking file when server exits."""
    from .boot_manager import VM_PID_TRACKING_FILE
    try:
        if VM_PID_TRACKING_FILE.exists():
            VM_PID_TRACKING_FILE.unlink()
    except Exception:
        pass  # Ignore cleanup errors

atexit.register(_cleanup_on_exit)
config_manager = ConfigManager()
fstests_manager = FstestsManager()
device_manager = DeviceManager()
baseline_manager = BaselineManager()


@app.list_resources()
async def list_resources() -> list[Resource]:
    """List available configuration resources."""
    resources = []

    # Add preset resource
    resources.append(
        Resource(
            uri="config://presets",
            name="Configuration Presets",
            mimeType="application/json",
            description="List of all available configuration presets"
        )
    )

    # Add template resources
    for preset in template_manager.list_presets():
        category = preset["category"]
        name = preset["name"]
        uri = f"config://templates/{category}/{name}"

        resources.append(
            Resource(
                uri=uri,
                name=f"{category.capitalize()}: {name}",
                mimeType="text/plain",
                description=preset["description"]
            )
        )

    return resources


@app.read_resource()
async def read_resource(uri: str) -> str:
    """Read a configuration resource."""
    if uri == "config://presets":
        # Return JSON list of all presets
        presets = template_manager.list_presets()
        return json.dumps(presets, indent=2)

    # Parse template URI: config://templates/{category}/{name}
    if uri.startswith("config://templates/"):
        parts = uri.replace("config://templates/", "").split("/")
        if len(parts) == 2:
            category, name = parts
            template = template_manager.get_template(category, name)
            if template:
                return template.load()

    raise ValueError(f"Unknown resource: {uri}")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="list_config_presets",
            description="List all available kernel configuration presets",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["target", "debug", "fragment"],
                        "description": "Optional category filter"
                    }
                }
            }
        ),
        Tool(
            name="get_config_template",
            description="Generate a complete kernel configuration from templates",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target use case",
                        "enum": template_manager.get_targets()
                    },
                    "debug_level": {
                        "type": "string",
                        "description": "Debug level",
                        "enum": template_manager.get_debug_levels(),
                        "default": "basic"
                    },
                    "architecture": {
                        "type": "string",
                        "description": "Target architecture",
                        "enum": ["x86_64", "arm64", "arm", "riscv"],
                        "default": "x86_64"
                    },
                    "additional_options": {
                        "type": "object",
                        "description": "Additional CONFIG options to set",
                        "additionalProperties": {"type": ["string", "null"]}
                    },
                    "fragments": {
                        "type": "array",
                        "description": "Additional fragments to merge",
                        "items": {
                            "type": "string",
                            "enum": template_manager.get_fragments()
                        }
                    }
                },
                "required": ["target"]
            }
        ),
        Tool(
            name="create_config_fragment",
            description="Create a custom configuration fragment",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Fragment name"
                    },
                    "options": {
                        "type": "object",
                        "description": "CONFIG options and their values",
                        "additionalProperties": {"type": ["string", "null"]}
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description"
                    }
                },
                "required": ["name", "options"]
            }
        ),
        Tool(
            name="merge_configs",
            description="Merge multiple configuration fragments",
            inputSchema={
                "type": "object",
                "properties": {
                    "base": {
                        "type": "string",
                        "description": "Base configuration (template name like 'target/networking' or file path)"
                    },
                    "fragments": {
                        "type": "array",
                        "description": "List of fragment names or file paths to merge",
                        "items": {"type": "string"}
                    },
                    "output": {
                        "type": "string",
                        "description": "Output file path (optional)"
                    }
                },
                "required": ["base", "fragments"]
            }
        ),
        Tool(
            name="apply_config",
            description="Apply configuration to kernel source tree",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "config_source": {
                        "type": "string",
                        "description": "Configuration source (template name, file path, or 'inline')"
                    },
                    "config_content": {
                        "type": "string",
                        "description": "Inline configuration content (if config_source is 'inline')"
                    },
                    "merge_with_existing": {
                        "type": "boolean",
                        "description": "Merge with existing .config",
                        "default": False
                    },
                    "cross_compile_arch": {
                        "type": "string",
                        "description": "Target architecture for cross-compilation (arm64, arm, riscv, etc.)",
                        "enum": ["x86_64", "x86", "arm64", "arm", "riscv", "powerpc", "mips"]
                    },
                    "cross_compile_prefix": {
                        "type": "string",
                        "description": "Cross-compiler prefix (e.g., 'aarch64-linux-gnu-'). Auto-detected if not specified."
                    },
                    "use_llvm": {
                        "type": "boolean",
                        "description": "Use LLVM toolchain for cross-compilation",
                        "default": False
                    },
                    "enable_virtme": {
                        "type": "boolean",
                        "description": "Add virtme-ng requirements via 'vng --kconfig' (recommended for configs that will be tested with boot_kernel_test)",
                        "default": True
                    }
                },
                "required": ["kernel_path", "config_source"]
            }
        ),
        Tool(
            name="validate_config",
            description="Validate a kernel configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_path": {
                        "type": "string",
                        "description": "Path to .config file"
                    },
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source (for Kconfig validation)"
                    }
                },
                "required": ["config_path"]
            }
        ),
        Tool(
            name="search_config_options",
            description="Search for kernel configuration options",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term (e.g., 'KASAN', 'filesystem', 'debug')"
                    },
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source"
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter",
                        "enum": ["debugging", "networking", "filesystems", "drivers"]
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="generate_build_config",
            description="Generate optimized build configuration and commands",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Testing target"
                    },
                    "optimization": {
                        "type": "string",
                        "description": "Build optimization goal",
                        "enum": ["speed", "debug", "size"],
                        "default": "speed"
                    },
                    "ccache": {
                        "type": "boolean",
                        "description": "Use ccache",
                        "default": True
                    },
                    "out_of_tree": {
                        "type": "boolean",
                        "description": "Use out-of-tree build",
                        "default": True
                    },
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source"
                    }
                },
                "required": ["target"]
            }
        ),
        Tool(
            name="build_kernel",
            description="Build the Linux kernel and validate the build",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "jobs": {
                        "type": "integer",
                        "description": "Number of parallel jobs (default: CPU count)",
                        "minimum": 1
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Show detailed build output",
                        "default": False
                    },
                    "keep_going": {
                        "type": "boolean",
                        "description": "Continue building despite errors",
                        "default": False
                    },
                    "target": {
                        "type": "string",
                        "description": "Make target to build",
                        "default": "all",
                        "enum": ["all", "vmlinux", "modules", "bzImage", "Image", "dtbs"]
                    },
                    "build_dir": {
                        "type": "string",
                        "description": "Output directory for out-of-tree build"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Build timeout in seconds",
                        "minimum": 60
                    },
                    "clean_first": {
                        "type": "boolean",
                        "description": "Clean before building",
                        "default": False
                    },
                    "clean_type": {
                        "type": "string",
                        "description": "Type of clean operation (only used if clean_first=true)",
                        "enum": ["clean", "mrproper", "distclean"],
                        "default": "clean"
                    },
                    "cross_compile_arch": {
                        "type": "string",
                        "description": "Target architecture for cross-compilation (arm64, arm, riscv, etc.)",
                        "enum": ["x86_64", "x86", "arm64", "arm", "riscv", "powerpc", "mips"]
                    },
                    "cross_compile_prefix": {
                        "type": "string",
                        "description": "Cross-compiler prefix (e.g., 'aarch64-linux-gnu-'). Auto-detected if not specified."
                    },
                    "use_llvm": {
                        "type": "boolean",
                        "description": "Use LLVM toolchain for cross-compilation",
                        "default": False
                    },
                    "extra_host_cflags": {
                        "type": "string",
                        "description": "Additional CFLAGS for host tools (e.g., '-Wno-error' to disable all warnings in objtool). Only affects build tools, not kernel code."
                    },
                    "extra_kernel_cflags": {
                        "type": "string",
                        "description": "Additional CFLAGS for kernel code compilation (e.g., '-Wno-error=stringop-overflow' for specific kernel warnings). Use sparingly - prefer fixing issues when possible."
                    },
                    "c_std": {
                        "type": "string",
                        "description": "C standard to use for compilation (e.g., 'gnu11'). Required for old kernels with GCC 15+ due to C23 bool/false/true keywords. Applies to ALL code: kernel, realmode, EFI stub, etc.",
                        "enum": ["c89", "c99", "c11", "c17", "c23", "gnu89", "gnu99", "gnu11", "gnu17", "gnu23"]
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="check_build_requirements",
            description="Check if kernel source is ready to build",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="clean_kernel_build",
            description="Clean kernel build artifacts",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "clean_type": {
                        "type": "string",
                        "description": "Type of clean operation",
                        "enum": ["clean", "mrproper", "distclean"],
                        "default": "clean"
                    },
                    "build_dir": {
                        "type": "string",
                        "description": "Build directory for out-of-tree builds"
                    },
                    "cross_compile_arch": {
                        "type": "string",
                        "description": "Target architecture for cross-compilation (arm64, arm, riscv, etc.)",
                        "enum": ["x86_64", "x86", "arm64", "arm", "riscv", "powerpc", "mips"]
                    },
                    "cross_compile_prefix": {
                        "type": "string",
                        "description": "Cross-compiler prefix (e.g., 'aarch64-linux-gnu-'). Auto-detected if not specified."
                    },
                    "use_llvm": {
                        "type": "boolean",
                        "description": "Use LLVM toolchain for cross-compilation",
                        "default": False
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="boot_kernel_test",
            description="Boot kernel with virtme-ng and validate it works correctly",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Boot timeout in seconds",
                        "default": 60,
                        "minimum": 10,
                        "maximum": 300
                    },
                    "memory": {
                        "type": "string",
                        "description": "Memory size for VM (e.g., '2G', '4G')",
                        "default": "2G"
                    },
                    "cpus": {
                        "type": "integer",
                        "description": "Number of CPUs for VM",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 32
                    },
                    "cross_compile_arch": {
                        "type": "string",
                        "description": "Target architecture for cross-compilation",
                        "enum": ["x86_64", "x86", "arm64", "arm", "riscv", "powerpc", "mips"]
                    },
                    "cross_compile_prefix": {
                        "type": "string",
                        "description": "Cross-compiler prefix. Auto-detected if not specified."
                    },
                    "use_llvm": {
                        "type": "boolean",
                        "description": "Use LLVM toolchain for cross-compilation",
                        "default": False
                    },
                    "extra_args": {
                        "type": "array",
                        "description": "Additional arguments to pass to vng",
                        "items": {"type": "string"}
                    },
                    "use_host_kernel": {
                        "type": "boolean",
                        "description": "Use host kernel instead of building from kernel_path",
                        "default": False
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="check_virtme_ng",
            description="Check if virtme-ng is installed and available",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="kill_hanging_vms",
            description="""Kill hanging VM processes launched by THIS kerneldev-mcp session.

Use this when:
  • VM has hung and not responding
  • Want to stop long-running tests before timeout
  • Need to clean up after interrupted/failed VM runs

This tool will:
  • Find and kill VMs launched by THIS session (tracked processes only)
  • Kill the entire process group (includes QEMU child processes)
  • Check for orphaned loop devices and provide cleanup commands

IMPORTANT: This only kills VMs from this specific Claude/MCP session.
- Will NOT kill VMs from other Claude sessions
- Will NOT kill other QEMU VMs on your system
- Each MCP server instance tracks its own VMs independently

Each tracked VM shows: PID, description, and running time.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Use SIGKILL (-9) instead of SIGTERM for immediate termination",
                        "default": False
                    }
                }
            }
        ),
        Tool(
            name="modify_kernel_config",
            description="Enable, disable, or modify specific kernel configuration options in an existing .config file",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "options": {
                        "type": "object",
                        "description": "CONFIG options to modify. Keys can be 'CONFIG_NAME' or just 'NAME'. Values: 'y' (enable), 'n' (disable), 'm' (module), string value, or null (unset)",
                        "additionalProperties": {"type": ["string", "null"]}
                    },
                    "cross_compile_arch": {
                        "type": "string",
                        "description": "Target architecture for cross-compilation",
                        "enum": ["x86_64", "x86", "arm64", "arm", "riscv", "powerpc", "mips"]
                    },
                    "cross_compile_prefix": {
                        "type": "string",
                        "description": "Cross-compiler prefix. Auto-detected if not specified."
                    },
                    "use_llvm": {
                        "type": "boolean",
                        "description": "Use LLVM toolchain for cross-compilation",
                        "default": False
                    }
                },
                "required": ["kernel_path", "options"]
            }
        ),
        Tool(
            name="fstests_setup_check",
            description="""Check if fstests is installed and get version info.

WORKFLOW: This is step 1 of the setup process.
Next steps: fstests_setup_install (if not installed), then fstests_setup_devices, then fstests_setup_configure

For automatic VM-based testing without manual setup, use fstests_vm_boot_and_run instead.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "fstests_path": {
                        "type": "string",
                        "description": "Path to fstests installation (optional, default: ~/.kerneldev-mcp/fstests)"
                    }
                }
            }
        ),
        Tool(
            name="fstests_setup_install",
            description="""Clone and build fstests from git.

WORKFLOW: This is step 2 of the setup process (run only if fstests_setup_check shows not installed).
Next steps: fstests_setup_devices, then fstests_setup_configure""",
            inputSchema={
                "type": "object",
                "properties": {
                    "install_path": {
                        "type": "string",
                        "description": "Where to install fstests (optional, default: ~/.kerneldev-mcp/fstests)"
                    },
                    "git_url": {
                        "type": "string",
                        "description": "Git repository URL (optional, default: kernel.org)"
                    }
                }
            }
        ),
        Tool(
            name="fstests_setup_devices",
            description="""Setup test and scratch devices for fstests, including SCRATCH_DEV_POOL for multi-device tests.

WORKFLOW: This is step 3 of the setup process.
Prerequisites: fstests_setup_install must succeed first
Next step: fstests_setup_configure

Creates TEST_DEV, SCRATCH_DEV, and optionally SCRATCH_DEV_POOL (for RAID and multi-device tests).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Device setup mode",
                        "enum": ["loop", "existing"],
                        "default": "loop"
                    },
                    "test_dev": {
                        "type": "string",
                        "description": "Test device path (for 'existing' mode)"
                    },
                    "scratch_dev": {
                        "type": "string",
                        "description": "Scratch device path (for 'existing' mode)"
                    },
                    "pool_devs": {
                        "type": "array",
                        "description": "List of pool device paths for SCRATCH_DEV_POOL (for 'existing' mode)",
                        "items": {"type": "string"}
                    },
                    "test_size": {
                        "type": "string",
                        "description": "Test device size for loop mode (e.g., '10G')",
                        "default": "10G"
                    },
                    "scratch_size": {
                        "type": "string",
                        "description": "Scratch device size for loop mode (e.g., '10G')",
                        "default": "10G"
                    },
                    "pool_count": {
                        "type": "integer",
                        "description": "Number of SCRATCH_DEV_POOL devices to create (default: 4, set to 0 to disable). Required for RAID and multi-device filesystem tests.",
                        "default": 4,
                        "minimum": 0,
                        "maximum": 10
                    },
                    "pool_size": {
                        "type": "string",
                        "description": "Size of each pool device (e.g., '10G'). Only used if pool_count > 0.",
                        "default": "10G"
                    },
                    "fstype": {
                        "type": "string",
                        "description": "Filesystem type",
                        "enum": ["ext4", "btrfs", "xfs", "f2fs"],
                        "default": "ext4"
                    },
                    "mount_options": {
                        "type": "string",
                        "description": "Mount options (e.g., '-o noatime')"
                    },
                    "mkfs_options": {
                        "type": "string",
                        "description": "mkfs options (e.g., '-b 4096')"
                    }
                }
            }
        ),
        Tool(
            name="fstests_setup_configure",
            description="""Create or update fstests local.config file.

WORKFLOW: This is step 4 (final step) of the setup process.
Prerequisites: fstests_setup_devices must succeed first
Next step: fstests_run to actually run tests""",
            inputSchema={
                "type": "object",
                "properties": {
                    "fstests_path": {
                        "type": "string",
                        "description": "Path to fstests installation"
                    },
                    "test_dev": {
                        "type": "string",
                        "description": "Test device path"
                    },
                    "test_dir": {
                        "type": "string",
                        "description": "Test mount point",
                        "default": "/mnt/test"
                    },
                    "scratch_dev": {
                        "type": "string",
                        "description": "Scratch device path"
                    },
                    "scratch_dir": {
                        "type": "string",
                        "description": "Scratch mount point",
                        "default": "/mnt/scratch"
                    },
                    "fstype": {
                        "type": "string",
                        "description": "Filesystem type"
                    },
                    "mount_options": {
                        "type": "string",
                        "description": "Mount options"
                    },
                    "mkfs_options": {
                        "type": "string",
                        "description": "mkfs options"
                    },
                    "pool_devices": {
                        "type": "array",
                        "description": "List of pool device paths for SCRATCH_DEV_POOL (e.g., from fstests_setup_devices)",
                        "items": {"type": "string"}
                    }
                },
                "required": ["test_dev", "scratch_dev", "fstype"]
            }
        ),
        Tool(
            name="fstests_vm_boot_and_run",
            description="""Boot kernel in VM with fstests and run tests - ALL-IN-ONE tool with automatic setup.

This is the EASIEST way to run fstests. It automatically:
  - Boots your kernel in a VM
  - Creates loop devices inside the VM
  - Sets up fstests configuration
  - Runs the specified tests (defaults to "-g quick" if not specified)
  - Reports results

Use this instead of the manual fstests_setup_* workflow when testing in a VM.

Quick examples:
  • Default (quick tests): tests parameter omitted or []
  • Auto group: tests=["-g", "auto"]
  • Specific test: tests=["generic/001"]
  • Multiple groups: tests=["-g", "quick", "-g", "auto"]""",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory"
                    },
                    "fstests_path": {
                        "type": "string",
                        "description": "Path to fstests installation"
                    },
                    "tests": {
                        "type": "array",
                        "description": """Tests to run (optional, defaults to ["-g", "quick"]).

IMPORTANT: Each argument must be a separate array element.

Valid patterns:
  • Test groups: ["-g", "quick"] or ["-g", "auto"]
  • Multiple groups: ["-g", "quick", "-g", "auto"]
  • Individual tests: ["generic/001"] or ["btrfs/010", "xfs/100"]
  • Exclude tests: ["-g", "quick", "-x", "generic/475"]
  • Mixed: ["-g", "auto", "btrfs/010", "-x", "generic/500"]

Common mistake to avoid:
  ✗ WRONG: ["-g quick"] (single string - won't work)
  ✓ RIGHT: ["-g", "quick"] (two separate elements)

Use fstests_groups_list tool to see available test groups.""",
                        "items": {"type": "string"}
                    },
                    "fstype": {
                        "type": "string",
                        "description": "Filesystem type to test",
                        "default": "ext4"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Boot and test timeout in seconds",
                        "default": 300,
                        "minimum": 60
                    },
                    "memory": {
                        "type": "string",
                        "description": "VM memory size",
                        "default": "4G"
                    },
                    "cpus": {
                        "type": "integer",
                        "description": "Number of CPUs",
                        "default": 4,
                        "minimum": 1
                    },
                    "force_9p": {
                        "type": "boolean",
                        "description": "Force use of 9p filesystem instead of virtio-fs (required for old kernels < 5.14 that lack virtio-fs support)",
                        "default": False
                    },
                    "io_scheduler": {
                        "type": "string",
                        "description": "IO scheduler to use for block devices (default: mq-deadline). Valid values: mq-deadline, none, bfq, kyber",
                        "default": "mq-deadline",
                        "enum": ["mq-deadline", "none", "bfq", "kyber"]
                    }
                },
                "required": ["kernel_path", "fstests_path"]
            }
        ),
        Tool(
            name="fstests_groups_list",
            description="""List available fstests test groups with descriptions.

Groups are predefined sets of tests. Common groups:
  • quick - Fast tests for basic validation
  • auto - Automated tests suitable for CI
  • dangerous - Tests that may cause system issues
  • log - Tests for filesystem logging
  • metadata - Tests for metadata operations

Use group names with the "-g" flag in test specifications:
  Example: tests=["-g", "quick"] to run all quick tests""",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="fstests_baseline_get",
            description="Get information about a stored baseline (results from a previous test run)",
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline_name": {
                        "type": "string",
                        "description": "Name of baseline to retrieve"
                    }
                },
                "required": ["baseline_name"]
            }
        ),
        Tool(
            name="fstests_baseline_compare",
            description="""Compare test results against a baseline to detect regressions.

This is the key tool for kernel development - it identifies NEW failures (regressions) vs pre-existing failures.
Current results can be loaded from git notes or a JSON file.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline_name": {
                        "type": "string",
                        "description": "Name of baseline to compare against"
                    },
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory (git repository) to load results from git notes"
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name to load current results from (defaults to current branch)"
                    },
                    "commit_sha": {
                        "type": "string",
                        "description": "Commit SHA to load current results from (overrides branch_name)"
                    },
                    "current_results_file": {
                        "type": "string",
                        "description": "Path to current results JSON file (alternative to git notes)"
                    }
                },
                "required": ["baseline_name"]
            }
        ),
        Tool(
            name="fstests_baseline_list",
            description="List all stored baselines with their metadata",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="fstests_git_load",
            description="Load fstests results from git notes",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory (git repository)"
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name to load from (defaults to current branch)"
                    },
                    "commit_sha": {
                        "type": "string",
                        "description": "Commit SHA to load from (overrides branch_name)"
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="fstests_git_list",
            description="List commits with stored fstests results (saved via fstests_run_and_save)",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory (git repository)"
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100
                    }
                },
                "required": ["kernel_path"]
            }
        ),
        Tool(
            name="fstests_git_delete",
            description="Delete fstests results from git notes",
            inputSchema={
                "type": "object",
                "properties": {
                    "kernel_path": {
                        "type": "string",
                        "description": "Path to kernel source directory (git repository)"
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name to delete from"
                    },
                    "commit_sha": {
                        "type": "string",
                        "description": "Commit SHA to delete from"
                    }
                },
                "required": ["kernel_path"]
            }
        ),
    ]


def _parse_cross_compile_args(arguments: Dict[str, Any]) -> Optional[CrossCompileConfig]:
    """Parse cross-compilation arguments from tool call.

    Args:
        arguments: Tool call arguments

    Returns:
        CrossCompileConfig if cross-compilation args present, None otherwise
    """
    arch = arguments.get("cross_compile_arch")
    if not arch:
        return None

    return CrossCompileConfig(
        arch=arch,
        cross_compile_prefix=arguments.get("cross_compile_prefix"),
        use_llvm=arguments.get("use_llvm", False)
    )


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    # Log IMMEDIATELY at entry to catch any hangs before tool logic
    logger.info(f"=" * 80)
    logger.info(f"TOOL CALL: {name}")
    logger.info(f"Arguments: {arguments}")
    logger.info(f"=" * 80)
    # Force flush immediately
    for handler in logger.handlers:
        handler.flush()

    try:
        if name == "list_config_presets":
            category = arguments.get("category")
            presets = template_manager.list_presets(category)
            result = json.dumps(presets, indent=2)
            return [TextContent(type="text", text=result)]

        elif name == "get_config_template":
            target = arguments["target"]
            debug_level = arguments.get("debug_level", "basic")
            architecture = arguments.get("architecture", "x86_64")
            additional_options = arguments.get("additional_options", {})
            fragments = arguments.get("fragments", [])

            config = config_manager.generate_config(
                target=target,
                debug_level=debug_level,
                architecture=architecture,
                additional_options=additional_options,
                fragments=fragments
            )

            return [TextContent(type="text", text=config.to_config_text())]

        elif name == "create_config_fragment":
            name = arguments["name"]
            options = arguments["options"]
            description = arguments.get("description", f"Custom fragment: {name}")

            # Create fragment config
            config = KernelConfig()
            config.header_comments = [description]
            for opt_name, opt_value in options.items():
                config.set_option(opt_name, opt_value)

            fragment_text = config.to_config_text()
            save_path = Path.cwd() / f"{name}.conf"

            return [TextContent(
                type="text",
                text=f"Created fragment:\n\n{fragment_text}\n\nSave to: {save_path}"
            )]

        elif name == "merge_configs":
            base = arguments["base"]
            fragments = arguments["fragments"]
            output = arguments.get("output")

            merged = config_manager.merge_configs(
                base=base,
                fragments=fragments,
                output=Path(output) if output else None
            )

            result_text = merged.to_config_text()
            if output:
                result_text = f"Configuration merged and saved to {output}\n\n{result_text[:500]}..."
            return [TextContent(type="text", text=result_text)]

        elif name == "apply_config":
            kernel_path = Path(arguments["kernel_path"])
            config_source = arguments["config_source"]
            merge_with_existing = arguments.get("merge_with_existing", False)
            enable_virtme = arguments.get("enable_virtme", True)  # Default to True
            cross_compile = _parse_cross_compile_args(arguments)

            # Handle inline config
            if config_source == "inline":
                config_content = arguments.get("config_content")
                if not config_content:
                    raise ValueError("config_content required when config_source is 'inline'")
                config = KernelConfig.from_config_text(config_content)
            else:
                # Try to load from file or template
                path = Path(config_source)
                if path.exists():
                    config = KernelConfig.from_file(path)
                else:
                    # Parse as template reference
                    parts = config_source.split("/")
                    if len(parts) == 2:
                        config = config_manager.generate_config(
                            target=parts[1] if parts[0] == "target" else "virtualization",
                            debug_level=parts[1] if parts[0] == "debug" else "basic"
                        )
                    else:
                        raise ValueError(f"Invalid config_source: {config_source}")

            success = config_manager.apply_config(
                config=config,
                kernel_path=kernel_path,
                merge_with_existing=merge_with_existing,
                cross_compile=cross_compile,
                enable_virtme=enable_virtme
            )

            result = "✓ Configuration applied successfully" if success else "⚠ Configuration applied with warnings"
            result += f"\n\nLocation: {kernel_path / '.config'}"
            if enable_virtme:
                result += "\n✓ virtme-ng requirements added (vng --kconfig)"
            if cross_compile:
                result += f"\n\nCross-compilation configured for {cross_compile.arch}"
                if cross_compile.use_llvm:
                    result += " (using LLVM)"
                elif cross_compile.cross_compile_prefix:
                    result += f" (using {cross_compile.cross_compile_prefix})"
            result += "\n\nNext steps:"
            if cross_compile:
                build_cmd = f"make ARCH={cross_compile.arch}"
                if cross_compile.use_llvm:
                    build_cmd += " LLVM=1"
                elif cross_compile.cross_compile_prefix:
                    build_cmd += f" CROSS_COMPILE={cross_compile.cross_compile_prefix}"
                build_cmd += " -j$(nproc)"
                result += f"\n1. Review the configuration: {build_cmd.replace('-j$(nproc)', 'menuconfig')}"
                result += f"\n2. Build the kernel: {build_cmd}"
            else:
                result += "\n1. Review the configuration: make menuconfig"
                result += "\n2. Build the kernel: make -j$(nproc)"

            return [TextContent(type="text", text=result)]

        elif name == "validate_config":
            config_path = Path(arguments["config_path"])
            kernel_path = arguments.get("kernel_path")

            if not config_path.exists():
                return [TextContent(type="text", text=f"Error: Config file not found: {config_path}")]

            # Load and parse config
            config = KernelConfig.from_file(config_path)

            validation_results = []
            validation_results.append(f"Configuration: {config_path}")
            validation_results.append(f"Total options: {len(config.options)}")

            # Count option types
            enabled = sum(1 for opt in config.options.values() if opt.value == "y")
            modules = sum(1 for opt in config.options.values() if opt.value == "m")
            disabled = sum(1 for opt in config.options.values() if opt.value is None)

            validation_results.append(f"  Built-in (y): {enabled}")
            validation_results.append(f"  Modules (m): {modules}")
            validation_results.append(f"  Disabled: {disabled}")

            return [TextContent(type="text", text="\n".join(validation_results))]

        elif name == "search_config_options":
            query = arguments["query"]
            kernel_path = arguments.get("kernel_path")

            if kernel_path:
                results = config_manager.search_config_options(
                    query=query,
                    kernel_path=Path(kernel_path)
                )

                if results:
                    result_text = f"Found {len(results)} config options matching '{query}':\n\n"
                    for i, result in enumerate(results[:10], 1):  # Show top 10
                        result_text += f"{i}. {result['name']}\n"
                        result_text += f"   {result['description']}\n"
                        result_text += f"   Source: {result['file']}\n\n"
                else:
                    result_text = f"No config options found matching '{query}'"
            else:
                result_text = "Kernel path required for searching config options"

            return [TextContent(type="text", text=result_text)]

        elif name == "generate_build_config":
            target = arguments["target"]
            optimization = arguments.get("optimization", "speed")
            ccache = arguments.get("ccache", True)
            out_of_tree = arguments.get("out_of_tree", True)
            kernel_path = arguments.get("kernel_path", "~/linux")

            build_commands = []
            build_commands.append("# Kernel Build Configuration")
            build_commands.append(f"# Target: {target}")
            build_commands.append(f"# Optimization: {optimization}")
            build_commands.append("")

            if out_of_tree:
                build_commands.append("# Out-of-tree build setup")
                build_commands.append("BUILD_DIR=build")
                build_commands.append("mkdir -p $BUILD_DIR")
                build_commands.append("")

            if ccache:
                build_commands.append("# Enable ccache for faster rebuilds")
                build_commands.append("export CCACHE_DIR=$HOME/.ccache")
                build_commands.append("export KBUILD_BUILD_TIMESTAMP=''")
                build_commands.append("")

            build_commands.append("# Build commands")
            if out_of_tree:
                build_commands.append(f"cd {kernel_path}")
                if ccache:
                    build_commands.append('make O=$BUILD_DIR CC="ccache gcc" -j$(nproc)')
                else:
                    build_commands.append("make O=$BUILD_DIR -j$(nproc)")
            else:
                if ccache:
                    build_commands.append(f'make -C {kernel_path} CC="ccache gcc" -j$(nproc)')
                else:
                    build_commands.append(f"make -C {kernel_path} -j$(nproc)")

            return [TextContent(type="text", text="\n".join(build_commands))]

        elif name == "build_kernel":
            kernel_path = Path(arguments["kernel_path"])
            jobs = arguments.get("jobs")
            verbose = arguments.get("verbose", False)
            keep_going = arguments.get("keep_going", False)
            target = arguments.get("target", "all")
            build_dir = arguments.get("build_dir")
            timeout = arguments.get("timeout")
            clean_first = arguments.get("clean_first", False)
            clean_type = arguments.get("clean_type", "clean")
            extra_host_cflags = arguments.get("extra_host_cflags")
            extra_kernel_cflags = arguments.get("extra_kernel_cflags")
            c_std = arguments.get("c_std")
            cross_compile = _parse_cross_compile_args(arguments)

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            builder = KernelBuilder(kernel_path)

            # Check if configured
            if not builder.check_config():
                return [TextContent(type="text", text="Error: Kernel not configured. Run 'make defconfig' or apply a configuration first.")]

            # Clean if requested
            if clean_first:
                logger.info(f"Cleaning build artifacts with '{clean_type}'...")

                # For mrproper/distclean, save config first if it exists
                config_backup = None
                if clean_type in ("mrproper", "distclean") and builder.check_config():
                    config_path = kernel_path / ".config"
                    config_backup = kernel_path / ".config.backup"
                    logger.info("Backing up .config before mrproper...")
                    shutil.copy(config_path, config_backup)

                # Do the clean
                builder.clean(
                    target=clean_type,
                    build_dir=Path(build_dir) if build_dir else None,
                    cross_compile=cross_compile
                )

                # Restore and reconfigure if we did mrproper/distclean
                if config_backup and config_backup.exists():
                    logger.info("Restoring .config and running olddefconfig...")
                    shutil.copy(config_backup, kernel_path / ".config")
                    config_backup.unlink()  # Remove backup

                    # Run olddefconfig to update config for this kernel version
                    subprocess.run(
                        ["make", "olddefconfig"],
                        cwd=kernel_path,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        check=True
                    )
                    logger.info("Configuration updated with olddefconfig")

            # Detect GCC version and warn about potential issues
            warnings = []
            try:
                gcc_result = subprocess.run(
                    ["gcc", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                gcc_version_line = gcc_result.stdout.split('\n')[0]
                # Extract major version (e.g., "gcc (GCC) 15.2.1" -> 15)
                import re
                version_match = re.search(r'gcc.*?(\d+)\.\d+', gcc_version_line, re.IGNORECASE)
                if version_match:
                    gcc_major = int(version_match.group(1))
                    if gcc_major >= 12 and not (extra_host_cflags or extra_kernel_cflags or c_std):
                        warnings.append(f"⚠ Detected GCC {gcc_major} - older kernels may fail to build due to new warnings/C23 changes")
                        warnings.append("  Suggestions if build fails:")
                        warnings.append("    • extra_host_cflags=\"-Wno-error\" - Disable errors in build tools (objtool, etc.)")
                        warnings.append("    • extra_kernel_cflags=\"-Wno-error=<warning>\" - Disable specific kernel code warnings")
                        if gcc_major >= 15:
                            warnings.append("    • c_std=\"gnu11\" - Force C11 (REQUIRED for kernels < 5.14 with GCC 15+)")
                    else:
                        if c_std:
                            logger.info(f"Using C standard: {c_std}")
                            logger.info("Note: This applies to ALL compilation via CC override")
                        if extra_host_cflags:
                            logger.info(f"Applying extra host CFLAGS: {extra_host_cflags}")
                            logger.info("Note: This only affects build tools (like objtool), not kernel code")
                        if extra_kernel_cflags:
                            logger.info(f"Applying extra kernel CFLAGS: {extra_kernel_cflags}")
                            logger.info("Note: This affects kernel code compilation")
            except Exception as e:
                logger.debug(f"Could not detect GCC version: {e}")

            # Build
            logger.info(f"Building kernel at {kernel_path}...")
            if cross_compile:
                logger.info(f"Cross-compiling for {cross_compile.arch}")

            result = builder.build(
                jobs=jobs,
                verbose=verbose,
                keep_going=keep_going,
                target=target,
                build_dir=Path(build_dir) if build_dir else None,
                timeout=timeout,
                cross_compile=cross_compile,
                extra_host_cflags=extra_host_cflags,
                extra_kernel_cflags=extra_kernel_cflags,
                c_std=c_std
            )

            # Format results
            output = format_build_errors(result, max_errors=20)

            # Prepend warnings if any
            if warnings:
                output = "\n".join(warnings) + "\n\n" + output

            if cross_compile:
                output += f"\n\nCross-compilation: {cross_compile.arch}"
                if cross_compile.use_llvm:
                    output += " (LLVM)"
                elif cross_compile.cross_compile_prefix:
                    output += f" ({cross_compile.cross_compile_prefix})"

            if result.success:
                output += "\n\nBuild artifacts:"
                if build_dir:
                    output += f"\n  Build directory: {build_dir}"
                else:
                    if cross_compile and cross_compile.arch == "arm64":
                        output += f"\n  Image: {kernel_path / 'arch/arm64/boot/Image'}"
                    elif cross_compile and cross_compile.arch == "arm":
                        output += f"\n  zImage: {kernel_path / 'arch/arm/boot/zImage'}"
                    else:
                        output += f"\n  vmlinux: {kernel_path / 'vmlinux'}"
                    output += f"\n  System.map: {kernel_path / 'System.map'}"

            return [TextContent(type="text", text=output)]

        elif name == "check_build_requirements":
            kernel_path = Path(arguments["kernel_path"])

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            builder = KernelBuilder(kernel_path)

            checks = []
            checks.append(f"Kernel path: {kernel_path}")

            # Check if it's a kernel tree
            if not (kernel_path / "Makefile").exists():
                checks.append("✗ Not a valid kernel source tree (no Makefile)")
                return [TextContent(type="text", text="\n".join(checks))]

            checks.append("✓ Valid kernel source tree")

            # Get version
            version = builder.get_kernel_version()
            if version:
                checks.append(f"✓ Kernel version: {version}")
            else:
                checks.append("✗ Could not determine kernel version")

            # Check if configured
            if builder.check_config():
                checks.append("✓ Kernel is configured (.config exists)")
            else:
                checks.append("✗ Kernel not configured (no .config)")
                checks.append("  Run: make defconfig")

            # Check for required tools
            required_tools = ["make", "gcc", "ld"]
            for tool in required_tools:
                try:
                    subprocess.run([tool, "--version"], capture_output=True, check=True)
                    checks.append(f"✓ {tool} available")
                except (subprocess.CalledProcessError, FileNotFoundError):
                    checks.append(f"✗ {tool} not found")

            return [TextContent(type="text", text="\n".join(checks))]

        elif name == "clean_kernel_build":
            kernel_path = Path(arguments["kernel_path"])
            clean_type = arguments.get("clean_type", "clean")
            build_dir = arguments.get("build_dir")
            cross_compile = _parse_cross_compile_args(arguments)

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            builder = KernelBuilder(kernel_path)

            success = builder.clean(
                target=clean_type,
                build_dir=Path(build_dir) if build_dir else None,
                cross_compile=cross_compile
            )

            if success:
                result_text = f"✓ Successfully ran 'make {clean_type}'"
                if cross_compile:
                    result_text += f" for {cross_compile.arch}"
                if build_dir:
                    result_text += f" in {build_dir}"
            else:
                result_text = f"✗ Failed to run 'make {clean_type}'"

            return [TextContent(type="text", text=result_text)]

        elif name == "boot_kernel_test":
            kernel_path = Path(arguments["kernel_path"])
            timeout = arguments.get("timeout", 60)
            memory = arguments.get("memory", "2G")
            cpus = arguments.get("cpus", 2)
            extra_args = arguments.get("extra_args", [])
            use_host_kernel = arguments.get("use_host_kernel", False)
            cross_compile = _parse_cross_compile_args(arguments)

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            boot_manager = BootManager(kernel_path)

            logger.info(f"Boot testing kernel at {kernel_path}...")
            if cross_compile:
                logger.info(f"Cross-compilation architecture: {cross_compile.arch}")
            if use_host_kernel:
                logger.info("Using host kernel instead of building")

            # Run boot test
            result = boot_manager.boot_test(
                timeout=timeout,
                memory=memory,
                cpus=cpus,
                cross_compile=cross_compile,
                extra_args=extra_args,
                use_host_kernel=use_host_kernel
            )

            # Format output
            output = format_boot_result(result, max_errors=20)

            # Add configuration info
            output += "\n\nBoot Configuration:"
            output += f"\n  Timeout: {timeout}s"
            output += f"\n  Memory: {memory}"
            output += f"\n  CPUs: {cpus}"
            if cross_compile:
                output += f"\n  Architecture: {cross_compile.arch}"

            # For successful boots, show first 100 and last 200 lines to give context
            # (failure output is already shown by format_boot_result)
            if result.boot_completed and result.dmesg_output:
                dmesg_lines = result.dmesg_output.splitlines()
                total_lines = len(dmesg_lines)

                output += f"\n\nDmesg Output (showing {min(300, total_lines)} of {total_lines} lines):"
                output += f"\nFull log: {result.log_file_path}\n"

                if total_lines <= 300:
                    # Show everything for short logs
                    output += "\n" + "\n".join(dmesg_lines)
                else:
                    # Show first 100 lines (boot start)
                    output += "\n\n=== First 100 lines (boot initialization) ===\n"
                    for i, line in enumerate(dmesg_lines[:100], 1):
                        output += f"{i:5d} | {line}\n"

                    output += f"\n... ({total_lines - 300} lines omitted) ...\n"

                    # Show last 200 lines (boot completion and results)
                    output += "\n=== Last 200 lines (boot completion) ===\n"
                    start_line = total_lines - 200
                    for i, line in enumerate(dmesg_lines[-200:], start_line + 1):
                        output += f"{i:5d} | {line}\n"

            return [TextContent(type="text", text=output)]

        elif name == "check_virtme_ng":
            # Check if virtme-ng and QEMU are available
            boot_manager = BootManager(Path.cwd())
            vng_available = boot_manager.check_virtme_ng()
            qemu_available, qemu_info = boot_manager.check_qemu()

            output_lines = []
            output_lines.append("Kernel Boot Prerequisites Check")
            output_lines.append("=" * 60)
            output_lines.append("")

            # Check virtme-ng
            if vng_available:
                try:
                    result = subprocess.run(
                        ["vng", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    version_info = result.stdout.strip()
                    output_lines.append(f"✓ virtme-ng: {version_info}")
                except Exception as e:
                    output_lines.append(f"✓ virtme-ng: Installed (version check failed: {e})")
            else:
                output_lines.append("✗ virtme-ng: Not found")
                output_lines.append("")
                output_lines.append("  Install with:")
                output_lines.append("    pip install virtme-ng")
                output_lines.append("  Or:")
                output_lines.append("    sudo dnf install virtme-ng  # Fedora/RHEL")
                output_lines.append("    sudo apt install virtme-ng  # Ubuntu/Debian")

            output_lines.append("")

            # Check QEMU
            if qemu_available:
                output_lines.append(f"✓ QEMU: {qemu_info}")
            else:
                output_lines.append(f"✗ QEMU: {qemu_info}")
                output_lines.append("")
                output_lines.append("  Install with:")
                output_lines.append("    sudo dnf install qemu-system-x86  # Fedora/RHEL")
                output_lines.append("    sudo apt install qemu-system-x86  # Ubuntu/Debian")
                output_lines.append("    sudo pacman -S qemu-system-x86    # Arch Linux")

            output_lines.append("")
            output_lines.append("=" * 60)

            # Summary
            both_available = vng_available and qemu_available
            if both_available:
                output_lines.append("✓ All prerequisites are available - ready to boot kernels!")
            else:
                output_lines.append("✗ Missing prerequisites - install the missing components above")

            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "kill_hanging_vms":
            from .boot_manager import _get_tracked_vm_processes, _cleanup_dead_tracked_processes
            import datetime

            force = arguments.get("force", False)
            signal_type = "SIGKILL (-9)" if force else "SIGTERM"

            logger.info("=" * 80)
            logger.info(f"kill_hanging_vms: Starting (force={force})")
            logger.info(f"MCP Server PID: {os.getpid()}")

            output_lines = []
            output_lines.append("Killing Tracked VM Processes (This Session Only)")
            output_lines.append("=" * 60)
            output_lines.append(f"MCP Server PID: {os.getpid()}")
            output_lines.append(f"Signal: {signal_type}")
            output_lines.append("")

            # Clean up dead processes from tracking first
            logger.info("Cleaning up dead tracked processes...")
            _cleanup_dead_tracked_processes()
            logger.info("Cleanup complete")

            # Get tracked processes
            logger.info("Getting tracked processes...")
            tracked = _get_tracked_vm_processes()
            logger.info(f"Found {len(tracked)} tracked processes: {list(tracked.keys())}")

            if not tracked:
                output_lines.append("✓ No tracked VM processes found in this session")
                output_lines.append("")
                output_lines.append("Note: This tool only kills VMs launched by THIS MCP session.")
                output_lines.append("Each Claude session has independent VM tracking.")
                output_lines.append("Use 'ps aux | grep qemu' to see all QEMU processes on the system.")
            else:
                output_lines.append(f"Found {len(tracked)} tracked VM process(es):")
                output_lines.append("")

                killed_count = 0
                errors = []

                for pid, info in tracked.items():
                    logger.info(f"Processing PID {pid}")
                    pgid = info.get("pgid", pid)
                    description = info.get("description", "Unknown")
                    started_at = info.get("started_at", 0)

                    # Calculate running time
                    if started_at:
                        running_time = datetime.datetime.now().timestamp() - started_at
                        running_str = f"{int(running_time)}s"
                    else:
                        running_str = "unknown"

                    logger.info(f"  PID={pid}, PGID={pgid}, Description={description}, Running={running_str}")

                    output_lines.append(f"  • PID {pid} (PGID {pgid})")
                    output_lines.append(f"    Description: {description}")
                    output_lines.append(f"    Running for: {running_str}")

                    # Kill the process tree (vng parent + QEMU children)
                    # Use subprocess with timeout to prevent hanging
                    try:
                        sig_num = "9" if force else "15"
                        logger.info(f"  Signal to use: -{sig_num}")

                        # Step 1: Find all child processes (including QEMU)
                        logger.info(f"  Step 1: Finding children of PID {pid}")
                        # pgrep -P <pid> finds children of the parent
                        try:
                            logger.info(f"  Running: pgrep -P {pid}")
                            child_result = subprocess.run(
                                ["pgrep", "-P", str(pid)],
                                capture_output=True,
                                timeout=1,
                                text=True
                            )
                            logger.info(f"  pgrep completed: rc={child_result.returncode}")
                            child_pids = []
                            if child_result.returncode == 0 and child_result.stdout.strip():
                                child_pids = child_result.stdout.strip().split('\n')
                                logger.info(f"  Found {len(child_pids)} children: {child_pids}")

                            # Log what we found
                            if child_pids:
                                output_lines.append(f"    Children: {', '.join(child_pids)}")
                        except subprocess.TimeoutExpired:
                            logger.warning(f"  pgrep timed out for PID {pid}")
                            child_pids = []

                        # Step 2: Kill all children first (QEMU processes)
                        logger.info(f"  Step 2: Killing {len(child_pids)} children")
                        for child_pid in child_pids:
                            try:
                                logger.info(f"  Running: kill -{sig_num} {child_pid}")
                                subprocess.run(
                                    ["kill", f"-{sig_num}", child_pid],
                                    capture_output=True,
                                    timeout=1,
                                    text=True
                                )
                                logger.info(f"  Successfully killed child {child_pid}")
                            except subprocess.TimeoutExpired:
                                logger.warning(f"  Timeout killing child {child_pid}")
                            except subprocess.CalledProcessError as e:
                                logger.warning(f"  Error killing child {child_pid}: {e}")

                        # Step 3: Kill the parent (vng) process
                        logger.info(f"  Step 3: Killing parent PID {pid}")
                        logger.info(f"  Running: kill -{sig_num} {pid}")
                        subprocess.run(
                            ["kill", f"-{sig_num}", str(pid)],
                            capture_output=True,
                            timeout=1,
                            text=True
                        )
                        logger.info(f"  Successfully killed parent {pid}")

                        # Step 4: Also try to kill the entire process group as backup
                        logger.info(f"  Step 4: Killing process group {pgid}")
                        # Syntax: kill -15 -- -<pgid> to kill process group
                        logger.info(f"  Running: kill -{sig_num} -- -{pgid}")
                        subprocess.run(
                            ["kill", f"-{sig_num}", "--", f"-{pgid}"],
                            capture_output=True,
                            timeout=1,
                            text=True
                        )
                        logger.info(f"  Successfully killed process group {pgid}")

                        killed_count += 1
                        logger.info(f"  Completed killing PID {pid}")
                        output_lines.append(f"    Status: ✓ Killed (parent + {len(child_pids)} child processes)")
                    except subprocess.TimeoutExpired as e:
                        logger.error(f"  TIMEOUT killing PID {pid}: {e}")
                        errors.append(f"Timeout killing PID {pid} (process may be stuck)")
                        output_lines.append(f"    Status: ✗ Timeout (stuck)")
                    except (ProcessLookupError, OSError, subprocess.CalledProcessError) as e:
                        logger.error(f"  ERROR killing PID {pid}: {e}")
                        errors.append(f"Failed to kill PID {pid}: {e}")
                        output_lines.append(f"    Status: ✗ Failed ({e})")

                    output_lines.append("")
                    logger.info(f"Finished processing PID {pid}")

                # Clean up tracking file after killing
                logger.info("Cleaning up tracking file after killing...")
                _cleanup_dead_tracked_processes()
                logger.info("Cleanup complete")

                output_lines.append("=" * 60)
                if killed_count > 0:
                    logger.info(f"Successfully killed {killed_count} process(es)")
                    output_lines.append(f"✓ Successfully killed {killed_count} process(es)")
                else:
                    logger.warning("Failed to kill any processes")
                    output_lines.append("✗ Failed to kill any processes")

                if errors:
                    logger.error(f"Encountered {len(errors)} errors:")
                    for error in errors:
                        logger.error(f"  {error}")
                    output_lines.append("")
                    output_lines.append("Errors:")
                    for error in errors:
                        output_lines.append(f"  • {error}")

            # Check for orphaned loop devices
            logger.info("Checking for orphaned loop devices...")
            output_lines.append("")
            output_lines.append("Checking for orphaned loop devices...")
            try:
                result = subprocess.run(
                    ["losetup", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    loop_devices = []
                    for line in result.stdout.strip().splitlines():
                        # Look for our loop devices (created in /var/tmp/kerneldev-loop-devices)
                        if "kerneldev-loop-devices" in line or "test.img" in line or "pool" in line:
                            # Extract loop device name (e.g., /dev/loop0)
                            parts = line.split(":", 1)
                            if parts:
                                loop_devices.append(parts[0].strip())

                    if loop_devices:
                        output_lines.append(f"Found {len(loop_devices)} orphaned loop device(s):")
                        for dev in loop_devices:
                            output_lines.append(f"  • {dev}")
                        output_lines.append("")
                        output_lines.append("To clean up loop devices, run:")
                        for dev in loop_devices:
                            output_lines.append(f"  sudo losetup -d {dev}")
                    else:
                        output_lines.append("✓ No orphaned loop devices found")
                else:
                    output_lines.append("✓ No loop devices active")
            except Exception as e:
                logger.error(f"Error checking loop devices: {e}")
                output_lines.append(f"⚠ Error checking loop devices: {e}")

            logger.info("kill_hanging_vms: Complete")
            logger.info("=" * 80)
            # Flush logs to ensure everything is written
            for handler in logger.handlers:
                handler.flush()

            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "modify_kernel_config":
            kernel_path = Path(arguments["kernel_path"])
            options = arguments["options"]
            cross_compile = _parse_cross_compile_args(arguments)

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            # Modify config
            result = config_manager.modify_kernel_config(
                kernel_path=kernel_path,
                options=options,
                cross_compile=cross_compile
            )

            # Format output
            output_lines = []

            if result["success"]:
                output_lines.append("✓ Configuration modified successfully")
            else:
                output_lines.append("⚠ Configuration modified with warnings/errors")

            output_lines.append("")

            # Show changes
            if result["changes"]:
                output_lines.append(f"Modified {len(result['changes'])} option(s):")
                for option_name, old_value, new_value in result["changes"]:
                    output_lines.append(f"  • {option_name}: {old_value} → {new_value}")
            else:
                output_lines.append("No changes made (options already had requested values)")

            # Show errors/warnings
            if result["errors"]:
                output_lines.append("")
                output_lines.append("Messages:")
                for error in result["errors"]:
                    output_lines.append(f"  {error}")

            output_lines.append("")
            output_lines.append(f"Updated config: {kernel_path / '.config'}")

            if cross_compile:
                output_lines.append(f"Architecture: {cross_compile.arch}")

            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "fstests_setup_check":
            fstests_path = arguments.get("fstests_path")
            if fstests_path:
                manager = FstestsManager(Path(fstests_path))
            else:
                manager = fstests_manager

            installed = manager.check_installed()
            version = manager.get_version() if installed else None

            if installed:
                output = f"✓ fstests is installed at {manager.fstests_path}\n"
                if version:
                    output += f"Version: {version}\n"
            else:
                output = f"✗ fstests is not installed at {manager.fstests_path}\n"
                output += "\nInstall with the install_fstests tool"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_setup_install":
            install_path = arguments.get("install_path")
            git_url = arguments.get("git_url")

            if install_path:
                manager = FstestsManager(Path(install_path))
            else:
                manager = fstests_manager

            # Check if already installed
            if manager.check_installed():
                return [TextContent(
                    type="text",
                    text=f"✓ fstests is already installed at {manager.fstests_path}"
                )]

            # Install
            success, message = manager.install(git_url=git_url)

            if success:
                output = f"✓ {message}\n"
                version = manager.get_version()
                if version:
                    output += f"Version: {version}"
            else:
                output = f"✗ Installation failed: {message}"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_setup_devices":
            mode = arguments.get("mode", "loop")
            fstype = arguments.get("fstype", "ext4")
            mount_options = arguments.get("mount_options")
            mkfs_options = arguments.get("mkfs_options")
            pool_count = arguments.get("pool_count", 4)
            pool_size = arguments.get("pool_size", "10G")

            if mode == "loop":
                test_size = arguments.get("test_size", "10G")
                scratch_size = arguments.get("scratch_size", "10G")

                result = device_manager.setup_loop_devices(
                    test_size=test_size,
                    scratch_size=scratch_size,
                    fstype=fstype,
                    mount_options=mount_options,
                    mkfs_options=mkfs_options,
                    pool_count=pool_count,
                    pool_size=pool_size
                )
            else:  # existing
                test_dev = arguments.get("test_dev")
                scratch_dev = arguments.get("scratch_dev")
                pool_devs = arguments.get("pool_devs")  # Optional list of pool device paths

                if not test_dev or not scratch_dev:
                    return [TextContent(
                        type="text",
                        text="Error: test_dev and scratch_dev required for 'existing' mode"
                    )]

                result = device_manager.setup_existing_devices(
                    test_dev=test_dev,
                    scratch_dev=scratch_dev,
                    fstype=fstype,
                    mount_options=mount_options,
                    mkfs_options=mkfs_options,
                    pool_devs=pool_devs
                )

            if result.success:
                output = f"✓ {result.message}\n\n"
                output += f"Test device: {result.test_device.device_path}\n"
                output += f"Test mount: {result.test_device.mount_point}\n"
                output += f"Scratch device: {result.scratch_device.device_path}\n"
                output += f"Scratch mount: {result.scratch_device.mount_point}\n"
                if result.pool_devices:
                    pool_paths = [pd.device_path for pd in result.pool_devices]
                    output += f"Pool devices: {', '.join(pool_paths)}\n"
                output += f"Filesystem: {fstype}\n"
                if result.cleanup_needed:
                    output += "\n⚠ Cleanup required when done (loop devices)"
                if result.pool_devices:
                    output += f"\n\n💡 Remember to pass pool_devices to fstests_setup_configure"
            else:
                output = f"✗ {result.message}"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_setup_configure":
            fstests_path = arguments.get("fstests_path")
            test_dev = arguments["test_dev"]
            scratch_dev = arguments["scratch_dev"]
            fstype = arguments["fstype"]
            test_dir = Path(arguments.get("test_dir", "/mnt/test"))
            scratch_dir = Path(arguments.get("scratch_dir", "/mnt/scratch"))
            mount_options = arguments.get("mount_options")
            mkfs_options = arguments.get("mkfs_options")
            pool_devices = arguments.get("pool_devices")

            if fstests_path:
                manager = FstestsManager(Path(fstests_path))
            else:
                manager = fstests_manager

            if not manager.check_installed():
                return [TextContent(
                    type="text",
                    text=f"Error: fstests not installed at {manager.fstests_path}"
                )]

            # Create config
            config = FstestsConfig(
                fstests_path=manager.fstests_path,
                test_dev=test_dev,
                test_dir=test_dir,
                scratch_dev=scratch_dev,
                scratch_dir=scratch_dir,
                fstype=fstype,
                mount_options=mount_options,
                mkfs_options=mkfs_options,
                scratch_dev_pool=pool_devices
            )

            # Write config
            success = manager.write_config(config)

            if success:
                output = f"✓ Configuration written to {manager.fstests_path / 'local.config'}\n\n"
                output += config.to_config_text()
            else:
                output = "✗ Failed to write configuration"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_groups_list":
            groups = fstests_manager.list_groups()

            output = "Available fstests groups:\n\n"
            for group, description in groups.items():
                output += f"  {group:15} - {description}\n"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_baseline_get":
            baseline_name = arguments["baseline_name"]

            baseline = baseline_manager.load_baseline(baseline_name)

            if baseline:
                output = f"Baseline: {baseline_name}\n"
                output += f"Created: {baseline.metadata.created_at}\n"
                if baseline.metadata.kernel_version:
                    output += f"Kernel: {baseline.metadata.kernel_version}\n"
                output += f"Filesystem: {baseline.metadata.fstype}\n"
                if baseline.metadata.test_selection:
                    output += f"Tests: {baseline.metadata.test_selection}\n"
                output += "\n"
                output += baseline.results.summary()
            else:
                output = f"✗ Baseline '{baseline_name}' not found"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_baseline_compare":
            baseline_name = arguments["baseline_name"]
            current_results_file = arguments.get("current_results_file")
            kernel_path = arguments.get("kernel_path")
            branch_name = arguments.get("branch_name")
            commit_sha = arguments.get("commit_sha")

            # Load baseline
            baseline = baseline_manager.load_baseline(baseline_name)

            if not baseline:
                return [TextContent(
                    type="text",
                    text=f"Error: Baseline '{baseline_name}' not found"
                )]

            # Load current results
            current_results = None

            # Try loading from git notes if kernel_path provided
            if kernel_path:
                try:
                    git_mgr = GitManager(Path(kernel_path))
                    current_results = git_mgr.load_fstests_run_result(
                        branch_name=branch_name,
                        commit_sha=commit_sha
                    )
                    if current_results:
                        logger.info("Loaded current results from git notes")
                except ValueError as e:
                    return [TextContent(
                        type="text",
                        text=f"Error: {str(e)}"
                    )]

            # Try loading from file if provided
            if not current_results and current_results_file:
                # Load from JSON file
                try:
                    with open(current_results_file) as f:
                        data = json.load(f)

                    # Parse into FstestsRunResult
                    test_results = [
                        TestResult(
                            test_name=t["test_name"],
                            status=t["status"],
                            duration=t["duration"],
                            failure_reason=t.get("failure_reason")
                        )
                        for t in data["test_results"]
                    ]

                    current_results = FstestsRunResult(
                        success=data["success"],
                        total_tests=data["total_tests"],
                        passed=data["passed"],
                        failed=data["failed"],
                        notrun=data["notrun"],
                        test_results=test_results,
                        duration=data["duration"]
                    )
                    logger.info("Loaded current results from file")
                except (OSError, json.JSONDecodeError, KeyError) as e:
                    return [TextContent(
                        type="text",
                        text=f"Error loading results file: {str(e)}"
                    )]

            if not current_results:
                output = "Error: No current results to compare.\n\n"
                output += "Please provide one of:\n"
                output += "  • kernel_path - to load results from git notes\n"
                output += "  • current_results_file - path to JSON results file\n\n"
                output += "Or run tests first with run_and_save_fstests tool."
                return [TextContent(type="text", text=output)]

            # Perform comparison
            comparison = baseline_manager.compare_results(current_results, baseline)

            # Format output
            output = format_comparison_result(comparison, baseline_name)

            return [TextContent(type="text", text=output)]

        elif name == "fstests_baseline_list":
            baselines = baseline_manager.list_baselines()

            if not baselines:
                output = "No baselines found"
            else:
                output = f"Available baselines ({len(baselines)}):\n\n"
                for baseline in baselines:
                    output += f"  • {baseline.name}\n"
                    output += f"    Created: {baseline.created_at}\n"
                    if baseline.kernel_version:
                        output += f"    Kernel: {baseline.kernel_version}\n"
                    output += f"    Filesystem: {baseline.fstype}\n"
                    if baseline.test_selection:
                        output += f"    Tests: {baseline.test_selection}\n"
                    output += "\n"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_vm_boot_and_run":
            kernel_path = Path(arguments["kernel_path"])
            fstests_path = Path(arguments["fstests_path"])
            tests = arguments.get("tests", ["-g", "quick"])
            fstype = arguments.get("fstype", "ext4")
            timeout = arguments.get("timeout", 300)
            memory = arguments.get("memory", "4G")
            cpus = arguments.get("cpus", 4)
            force_9p = arguments.get("force_9p", False)
            io_scheduler = arguments.get("io_scheduler", "mq-deadline")

            # Check kernel path exists
            if not kernel_path.exists():
                return [TextContent(
                    type="text",
                    text=f"Error: Kernel path does not exist: {kernel_path}"
                )]

            # Check fstests path exists
            if not fstests_path.exists():
                return [TextContent(
                    type="text",
                    text=f"Error: fstests path does not exist: {fstests_path}"
                )]

            # Create boot manager
            try:
                boot_mgr = BootManager(kernel_path)
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error creating BootManager: {str(e)}"
                )]

            # Boot with fstests
            boot_result, fstests_result = boot_mgr.boot_with_fstests(
                fstests_path=fstests_path,
                tests=tests,
                fstype=fstype,
                timeout=timeout,
                memory=memory,
                cpus=cpus,
                force_9p=force_9p,
                io_scheduler=io_scheduler
            )

            # Format output
            output = "=== Kernel Boot with fstests ===\n\n"

            # Boot status
            output += format_boot_result(boot_result)
            output += "\n\n"

            # fstests results
            if fstests_result:
                output += "=== fstests Results ===\n\n"
                output += format_fstests_result(fstests_result)

                # Check if tests actually succeeded
                if not fstests_result.success:
                    output += "\n⚠ WARNING: fstests completed but some tests FAILED\n"
                    output += f"Failed: {fstests_result.failed}, Passed: {fstests_result.passed}\n"
            else:
                output += "✗ fstests did not complete (boot failed or timed out)\n"

            # For successful boots, include console output to show what happened
            # This is valuable for debugging and verification
            if boot_result.boot_completed and boot_result.dmesg_output:
                console_lines = boot_result.dmesg_output.splitlines()
                total_lines = len(console_lines)

                # Show last 300 lines which typically includes:
                # - Kernel boot completion
                # - fstests setup
                # - All test output
                # - Test summary
                output += "\n\n=== Console Output (last 300 lines) ===\n"
                output += f"Full log saved to: {boot_result.log_file_path}\n\n"

                last_lines = console_lines[-300:] if total_lines > 300 else console_lines
                start_line_num = max(1, total_lines - len(last_lines) + 1)

                for i, line in enumerate(last_lines, start=start_line_num):
                    output += f"{i:5d} | {line}\n"

                if total_lines > 300:
                    output += f"\n... showing last 300 of {total_lines} total lines\n"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_git_load":
            kernel_path = Path(arguments["kernel_path"])
            branch_name = arguments.get("branch_name")
            commit_sha = arguments.get("commit_sha")

            try:
                git_mgr = GitManager(kernel_path)
            except ValueError as e:
                return [TextContent(
                    type="text",
                    text=f"Error: {str(e)}"
                )]

            # Load results
            data = git_mgr.load_fstests_results(
                branch_name=branch_name,
                commit_sha=commit_sha
            )

            if not data:
                location = commit_sha or branch_name or "current commit"
                return [TextContent(
                    type="text",
                    text=f"✗ No fstests results found for {location}"
                )]

            # Reconstruct and format results
            result = git_mgr.load_fstests_run_result(
                branch_name=branch_name,
                commit_sha=commit_sha
            )

            metadata = data["metadata"]
            output = "=== Fstests Results from Git Notes ===\n\n"
            output += f"Commit: {metadata['commit_sha'][:8]}\n"
            if metadata.get("branch_name"):
                output += f"Branch: {metadata['branch_name']}\n"
            if metadata.get("kernel_version"):
                output += f"Kernel: {metadata['kernel_version']}\n"
            output += f"Filesystem: {metadata['fstype']}\n"
            output += f"Tests: {metadata['test_selection']}\n"
            output += f"Created: {metadata['created_at']}\n\n"

            if result:
                output += result.summary()

            return [TextContent(type="text", text=output)]

        elif name == "fstests_git_list":
            kernel_path = Path(arguments["kernel_path"])
            max_count = arguments.get("max_count", 20)

            try:
                git_mgr = GitManager(kernel_path)
            except ValueError as e:
                return [TextContent(
                    type="text",
                    text=f"Error: {str(e)}"
                )]

            results = git_mgr.list_commits_with_results(max_count=max_count)

            if not results:
                output = "No fstests results found in git notes"
            else:
                output = f"Found {len(results)} commit(s) with fstests results:\n\n"
                for i, meta in enumerate(results, 1):
                    output += f"{i}. {meta.commit_sha[:8]}"
                    if meta.branch_name:
                        output += f" ({meta.branch_name})"
                    output += "\n"
                    if meta.kernel_version:
                        output += f"   Kernel: {meta.kernel_version}\n"
                    output += f"   Filesystem: {meta.fstype}\n"
                    output += f"   Tests: {meta.test_selection}\n"
                    output += f"   Created: {meta.created_at}\n\n"

            return [TextContent(type="text", text=output)]

        elif name == "fstests_git_delete":
            kernel_path = Path(arguments["kernel_path"])
            branch_name = arguments.get("branch_name")
            commit_sha = arguments.get("commit_sha")

            try:
                git_mgr = GitManager(kernel_path)
            except ValueError as e:
                return [TextContent(
                    type="text",
                    text=f"Error: {str(e)}"
                )]

            success = git_mgr.delete_fstests_results(
                branch_name=branch_name,
                commit_sha=commit_sha
            )

            if success:
                location = commit_sha or branch_name or "current commit"
                output = f"✓ Deleted fstests results for {location}"
            else:
                location = commit_sha or branch_name or "current commit"
                output = f"✗ Failed to delete results for {location}"

            return [TextContent(type="text", text=output)]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Error in tool {name}: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {str(e)}")]


def main():
    """Main entry point for the MCP server."""
    import asyncio
    import mcp.server.stdio

    async def run():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
