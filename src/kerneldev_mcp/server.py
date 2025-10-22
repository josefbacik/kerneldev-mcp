"""
MCP server for kernel development configuration management.
"""
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
    FstestsManager, FstestsConfig, FstestsRunResult,
    format_fstests_result
)
from .baseline_manager import (
    BaselineManager, Baseline, ComparisonResult,
    format_comparison_result
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize server
app = Server("kerneldev-mcp")

# Initialize managers
template_manager = TemplateManager()
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
            name="check_fstests",
            description="Check if fstests is installed and get version info",
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
            name="install_fstests",
            description="Clone and build fstests from git",
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
            name="setup_fstests_devices",
            description="Setup test and scratch devices for fstests",
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
            name="configure_fstests",
            description="Create or update fstests local.config file",
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
                    }
                },
                "required": ["test_dev", "scratch_dev", "fstype"]
            }
        ),
        Tool(
            name="run_fstests",
            description="Run fstests and capture results",
            inputSchema={
                "type": "object",
                "properties": {
                    "fstests_path": {
                        "type": "string",
                        "description": "Path to fstests installation (optional)"
                    },
                    "tests": {
                        "type": "array",
                        "description": "Tests to run (e.g., ['generic/001'] or ['-g', 'quick'])",
                        "items": {"type": "string"}
                    },
                    "exclude_file": {
                        "type": "string",
                        "description": "Path to exclude file"
                    },
                    "randomize": {
                        "type": "boolean",
                        "description": "Randomize test order",
                        "default": False
                    },
                    "iterations": {
                        "type": "integer",
                        "description": "Number of times to run tests",
                        "default": 1,
                        "minimum": 1
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "minimum": 60
                    },
                    "save_baseline": {
                        "type": "boolean",
                        "description": "Save results as baseline",
                        "default": False
                    },
                    "baseline_name": {
                        "type": "string",
                        "description": "Name for saved baseline"
                    },
                    "kernel_version": {
                        "type": "string",
                        "description": "Kernel version for baseline metadata"
                    }
                }
            }
        ),
        Tool(
            name="boot_kernel_with_fstests",
            description="Boot kernel in VM with fstests configured and run tests",
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
                        "description": "Tests to run",
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
                    }
                },
                "required": ["kernel_path", "fstests_path"]
            }
        ),
        Tool(
            name="list_fstests_groups",
            description="List available fstests test groups",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_fstests_baseline",
            description="Get or create baseline for current kernel/config",
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
            name="compare_fstests_results",
            description="Compare test results against a baseline",
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline_name": {
                        "type": "string",
                        "description": "Name of baseline to compare against"
                    },
                    "current_results_file": {
                        "type": "string",
                        "description": "Path to current results JSON file (optional if just ran tests)"
                    }
                },
                "required": ["baseline_name"]
            }
        ),
        Tool(
            name="list_fstests_baselines",
            description="List all stored baselines",
            inputSchema={
                "type": "object",
                "properties": {}
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

            # For successful boots, show first 50 lines of dmesg
            # (failure output is already shown by format_boot_result)
            if result.boot_completed and result.dmesg_output:
                output += "\n\nDmesg Output (first 50 lines):"
                lines = result.dmesg_output.splitlines()[:50]
                output += "\n" + "\n".join(lines)
                if len(result.dmesg_output.splitlines()) > 50:
                    output += f"\n... ({len(result.dmesg_output.splitlines()) - 50} more lines)"

            return [TextContent(type="text", text=output)]

        elif name == "check_virtme_ng":
            # Check if virtme-ng is available
            boot_manager = BootManager(Path.cwd())
            available = boot_manager.check_virtme_ng()

            if available:
                # Try to get version
                try:
                    result = subprocess.run(
                        ["vng", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    version_info = result.stdout.strip()
                    output = f"✓ virtme-ng is installed and available\n\n{version_info}"
                except Exception as e:
                    output = f"✓ virtme-ng is installed and available\n\nVersion: Unable to determine ({e})"
            else:
                output = "✗ virtme-ng is not available\n\n"
                output += "Install virtme-ng with:\n"
                output += "  pip install virtme-ng\n\n"
                output += "Or on Fedora/Ubuntu:\n"
                output += "  sudo dnf install virtme-ng\n"
                output += "  sudo apt install virtme-ng"

            return [TextContent(type="text", text=output)]

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

        elif name == "check_fstests":
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

        elif name == "install_fstests":
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

        elif name == "setup_fstests_devices":
            mode = arguments.get("mode", "loop")
            fstype = arguments.get("fstype", "ext4")
            mount_options = arguments.get("mount_options")
            mkfs_options = arguments.get("mkfs_options")

            if mode == "loop":
                test_size = arguments.get("test_size", "10G")
                scratch_size = arguments.get("scratch_size", "10G")

                result = device_manager.setup_loop_devices(
                    test_size=test_size,
                    scratch_size=scratch_size,
                    fstype=fstype,
                    mount_options=mount_options,
                    mkfs_options=mkfs_options
                )
            else:  # existing
                test_dev = arguments.get("test_dev")
                scratch_dev = arguments.get("scratch_dev")

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
                    mkfs_options=mkfs_options
                )

            if result.success:
                output = f"✓ {result.message}\n\n"
                output += f"Test device: {result.test_device.device_path}\n"
                output += f"Test mount: {result.test_device.mount_point}\n"
                output += f"Scratch device: {result.scratch_device.device_path}\n"
                output += f"Scratch mount: {result.scratch_device.mount_point}\n"
                output += f"Filesystem: {fstype}\n"
                if result.cleanup_needed:
                    output += "\n⚠ Cleanup required when done (loop devices)"
            else:
                output = f"✗ {result.message}"

            return [TextContent(type="text", text=output)]

        elif name == "configure_fstests":
            fstests_path = arguments.get("fstests_path")
            test_dev = arguments["test_dev"]
            scratch_dev = arguments["scratch_dev"]
            fstype = arguments["fstype"]
            test_dir = Path(arguments.get("test_dir", "/mnt/test"))
            scratch_dir = Path(arguments.get("scratch_dir", "/mnt/scratch"))
            mount_options = arguments.get("mount_options")
            mkfs_options = arguments.get("mkfs_options")

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
                mkfs_options=mkfs_options
            )

            # Write config
            success = manager.write_config(config)

            if success:
                output = f"✓ Configuration written to {manager.fstests_path / 'local.config'}\n\n"
                output += config.to_config_text()
            else:
                output = "✗ Failed to write configuration"

            return [TextContent(type="text", text=output)]

        elif name == "run_fstests":
            fstests_path = arguments.get("fstests_path")
            tests = arguments.get("tests")
            exclude_file = arguments.get("exclude_file")
            randomize = arguments.get("randomize", False)
            iterations = arguments.get("iterations", 1)
            timeout = arguments.get("timeout")
            save_baseline = arguments.get("save_baseline", False)
            baseline_name = arguments.get("baseline_name")
            kernel_version = arguments.get("kernel_version")

            if fstests_path:
                manager = FstestsManager(Path(fstests_path))
            else:
                manager = fstests_manager

            if not manager.check_installed():
                return [TextContent(
                    type="text",
                    text=f"Error: fstests not installed at {manager.fstests_path}"
                )]

            # Run tests
            result = manager.run_tests(
                tests=tests,
                exclude_file=Path(exclude_file) if exclude_file else None,
                randomize=randomize,
                iterations=iterations,
                timeout=timeout
            )

            # Save baseline if requested
            if save_baseline and baseline_name:
                baseline_manager.save_baseline(
                    baseline_name=baseline_name,
                    results=result,
                    kernel_version=kernel_version,
                    test_selection=" ".join(tests) if tests else "-g quick"
                )

            # Format output
            output = format_fstests_result(result)

            if save_baseline and baseline_name:
                output += f"\n\n✓ Baseline saved as '{baseline_name}'"

            return [TextContent(type="text", text=output)]

        elif name == "list_fstests_groups":
            groups = fstests_manager.list_groups()

            output = "Available fstests groups:\n\n"
            for group, description in groups.items():
                output += f"  {group:15} - {description}\n"

            return [TextContent(type="text", text=output)]

        elif name == "get_fstests_baseline":
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

        elif name == "compare_fstests_results":
            baseline_name = arguments["baseline_name"]
            current_results_file = arguments.get("current_results_file")

            baseline = baseline_manager.load_baseline(baseline_name)

            if not baseline:
                return [TextContent(
                    type="text",
                    text=f"Error: Baseline '{baseline_name}' not found"
                )]

            # Load current results (would need to be stored from previous run_fstests)
            # For now, return error asking user to run tests first
            # TODO: Implement storing last run results or loading from file

            output = "Error: Current results comparison not yet implemented.\n"
            output += "Need to run tests first and store results, or provide results file."

            return [TextContent(type="text", text=output)]

        elif name == "list_fstests_baselines":
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

        elif name == "boot_kernel_with_fstests":
            kernel_path = Path(arguments["kernel_path"])
            fstests_path = Path(arguments["fstests_path"])
            tests = arguments.get("tests", ["-g", "quick"])
            timeout = arguments.get("timeout", 300)
            memory = arguments.get("memory", "4G")
            cpus = arguments.get("cpus", 4)

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
                timeout=timeout,
                memory=memory,
                cpus=cpus
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
            else:
                output += "✗ fstests did not complete (boot failed or timed out)\n"

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
