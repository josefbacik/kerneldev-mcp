"""
MCP server for kernel development configuration management.
"""
import json
import logging
import os
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

from .config_manager import ConfigManager, KernelConfig
from .templates import TemplateManager

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
    ]


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
                merge_with_existing=merge_with_existing
            )

            result = "✓ Configuration applied successfully" if success else "⚠ Configuration applied with warnings"
            result += f"\n\nLocation: {kernel_path / '.config'}"
            result += "\n\nNext steps:"
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
