"""
MCP server for kernel development configuration management.
"""
import json
import logging
import os
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize server
app = Server("kerneldev-mcp")

# Initialize managers
template_manager = TemplateManager()
config_manager = ConfigManager()


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
                cross_compile=cross_compile
            )

            result = "✓ Configuration applied successfully" if success else "⚠ Configuration applied with warnings"
            result += f"\n\nLocation: {kernel_path / '.config'}"
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
            cross_compile = _parse_cross_compile_args(arguments)

            if not kernel_path.exists():
                return [TextContent(type="text", text=f"Error: Kernel path does not exist: {kernel_path}")]

            builder = KernelBuilder(kernel_path)

            # Check if configured
            if not builder.check_config():
                return [TextContent(type="text", text="Error: Kernel not configured. Run 'make defconfig' or apply a configuration first.")]

            # Clean if requested
            if clean_first:
                logger.info("Cleaning build artifacts...")
                builder.clean(
                    build_dir=Path(build_dir) if build_dir else None,
                    cross_compile=cross_compile
                )

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
                cross_compile=cross_compile
            )

            # Format results
            output = format_build_errors(result, max_errors=20)

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
