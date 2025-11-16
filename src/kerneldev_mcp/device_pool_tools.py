"""
MCP tools for device pool management.

Provides MCP tool definitions and handlers for device pool operations.
"""

import logging
import subprocess
from typing import Any, Dict, List

from mcp.types import Tool, TextContent

from .device_pool import ConfigManager, LVMPoolManager, ValidationLevel


logger = logging.getLogger(__name__)


# Tool definitions for MCP server
def get_device_pool_tools() -> List[Tool]:
    """Get list of device pool MCP tools."""
    return [
        Tool(
            name="device_pool_setup",
            description="Create a new LVM-based device pool (PV + VG). Volumes are created on-demand when tests run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_path": {
                        "type": "string",
                        "description": "Physical device path (e.g., '/dev/nvme1n1')",
                    },
                    "pool_name": {
                        "type": "string",
                        "description": "Pool identifier",
                        "default": "default",
                    },
                    "vg_name": {
                        "type": "string",
                        "description": "Volume group name (optional, auto-generated if not specified)",
                    },
                    "lv_prefix": {
                        "type": "string",
                        "description": "Logical volume prefix for on-demand LVs (default: 'kdev')",
                    },
                },
                "required": ["device_path"],
            },
        ),
        Tool(
            name="device_pool_status",
            description="Display current pool status and validate health",
            inputSchema={
                "type": "object",
                "properties": {
                    "pool_name": {
                        "type": "string",
                        "description": "Pool to check",
                        "default": "default",
                    }
                },
            },
        ),
        Tool(
            name="device_pool_teardown",
            description="Remove device pool and clean up resources",
            inputSchema={
                "type": "object",
                "properties": {
                    "pool_name": {
                        "type": "string",
                        "description": "Pool to remove",
                        "default": "default",
                    },
                    "wipe_data": {
                        "type": "boolean",
                        "description": "Overwrite with zeros (slow but secure)",
                        "default": False,
                    },
                },
                "required": ["pool_name"],
            },
        ),
        Tool(
            name="device_pool_resize",
            description="Resize a logical volume by its full LV name",
            inputSchema={
                "type": "object",
                "properties": {
                    "pool_name": {
                        "type": "string",
                        "description": "Pool containing volume",
                        "default": "default",
                    },
                    "lv_name": {
                        "type": "string",
                        "description": "Full LV name (e.g., kdev-20251115103045-a3f9d2-test)",
                    },
                    "new_size": {
                        "type": "string",
                        "description": "New size (e.g., '+20G' or '50G')",
                    },
                },
                "required": ["lv_name", "new_size"],
            },
        ),
        Tool(
            name="device_pool_snapshot",
            description="LVM snapshot management (create/delete)",
            inputSchema={
                "type": "object",
                "properties": {
                    "pool_name": {
                        "type": "string",
                        "description": "Pool containing volume",
                        "default": "default",
                    },
                    "lv_name": {
                        "type": "string",
                        "description": "Source LV name (e.g., kdev-20251115103045-a3f9d2-test)",
                    },
                    "snapshot_name": {"type": "string", "description": "Snapshot identifier"},
                    "action": {
                        "type": "string",
                        "description": "Action to perform",
                        "enum": ["create", "delete"],
                    },
                    "snapshot_size": {
                        "type": "string",
                        "description": "Snapshot size (for create action, default: '1G')",
                        "default": "1G",
                    },
                },
                "required": ["lv_name", "snapshot_name", "action"],
            },
        ),
        Tool(
            name="device_pool_list",
            description="List all configured device pools",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="device_pool_cleanup",
            description="Clean up orphaned LVs from dead MCP processes",
            inputSchema={
                "type": "object",
                "properties": {
                    "pool_name": {
                        "type": "string",
                        "description": "Pool to clean up",
                        "default": "default",
                    }
                },
            },
        ),
    ]


# Tool handlers
async def handle_device_pool_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """
    Handle device pool tool calls.

    Args:
        name: Tool name
        arguments: Tool arguments

    Returns:
        List of TextContent responses
    """
    try:
        if name == "device_pool_setup":
            return await _handle_device_pool_setup(arguments)
        elif name == "device_pool_status":
            return await _handle_device_pool_status(arguments)
        elif name == "device_pool_teardown":
            return await _handle_device_pool_teardown(arguments)
        elif name == "device_pool_resize":
            return await _handle_device_pool_resize(arguments)
        elif name == "device_pool_snapshot":
            return await _handle_device_pool_snapshot(arguments)
        elif name == "device_pool_list":
            return await _handle_device_pool_list(arguments)
        elif name == "device_pool_cleanup":
            return await _handle_device_pool_cleanup(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown device pool tool: {name}")]
    except Exception as e:
        logger.error(f"Error handling device pool tool '{name}': {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def _handle_device_pool_setup(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_setup tool."""
    device_path = arguments["device_path"]
    pool_name = arguments.get("pool_name", "default")

    logger.info(f"Setting up LVM device pool '{pool_name}' on {device_path}")

    # Create LVM manager
    config_manager = ConfigManager()
    manager = LVMPoolManager(config_manager)

    # Prepare LVM options
    options = {}
    if "vg_name" in arguments:
        options["vg_name"] = arguments["vg_name"]
    if "lv_prefix" in arguments:
        options["lv_prefix"] = arguments["lv_prefix"]

    # Setup pool (just PV + VG, no LVs)
    pool_config = manager.setup_pool(device=device_path, pool_name=pool_name, **options)

    # Get VG size info
    vg_name = pool_config.lvm_config.vg_name if pool_config.lvm_config else "N/A"

    # Get VG size
    try:
        result = subprocess.run(
            ["sudo", "vgs", "--noheadings", "-o", "vg_size", "--units", "g", vg_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        vg_size = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        vg_size = "unknown"

    # Format response
    response_text = f"""LVM Device Pool Created Successfully!

Pool Name: {pool_name}
Device: {device_path}
Volume Group: {vg_name}
VG Size: {vg_size}
Created: {pool_config.created_at}
User: {pool_config.created_by}

Note: This pool contains NO pre-created LVs.
Logical volumes will be created on-demand with unique names when you run tests.
Each Claude instance will get its own set of LVs automatically.

All LVM operations use sudo - no special permissions configuration needed.
VG name '{vg_name}' is persistent across reboots.
"""

    response_text += "\nConfiguration saved to: ~/.kerneldev-mcp/device-pool.json"
    response_text += (
        f"\n\nTo use this pool automatically:\n  export KERNELDEV_DEVICE_POOL={pool_name}"
    )
    response_text += (
        "\n\nLVs will be created automatically when running:\n  fstests_vm_boot_and_run ..."
    )

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_status(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_status tool."""
    pool_name = arguments.get("pool_name", "default")

    config_manager = ConfigManager()
    pool = config_manager.get_pool(pool_name)

    if pool is None:
        return [
            TextContent(
                type="text",
                text=f"Pool '{pool_name}' not found.\n\nUse device_pool_list to see available pools.",
            )
        ]

    # Create LVM manager for validation
    manager = LVMPoolManager(config_manager)

    # Validate pool
    validation = manager.validate_pool(pool_name)

    # Get active allocations
    from .device_pool import VolumeStateManager

    state_mgr = VolumeStateManager()
    state = state_mgr._load_state()
    pool_allocations = [a for a in state.get("allocations", []) if a["pool_name"] == pool_name]

    # Get VG info
    vg_name = pool.lvm_config.vg_name if pool.lvm_config else "N/A"
    try:
        result = subprocess.run(
            ["sudo", "vgs", "--noheadings", "-o", "vg_size,vg_free", "--units", "g", vg_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            vg_size = parts[0] if len(parts) > 0 else "unknown"
            vg_free = parts[1] if len(parts) > 1 else "unknown"
        else:
            vg_size = vg_free = "unknown"
    except Exception:
        vg_size = vg_free = "unknown"

    # Format response
    response_text = f"""LVM Device Pool Status: {pool_name}

Device: {pool.device}
Volume Group: {vg_name}
VG Size: {vg_size}
VG Free: {vg_free}
Created: {pool.created_at}
User: {pool.created_by}

Active LVs: {len(pool_allocations)}
"""
    if pool_allocations:
        response_text += "\nCurrently Allocated Volumes:\n"
        for alloc in pool_allocations:
            response_text += f"  - {alloc['lv_name']}: {alloc['volume_spec']['size']}"
            response_text += f" (PID {alloc['pid']}, session {alloc['session_id'][:8]}...)\n"

    response_text += "\n\nHealth Status: "
    if validation.level == ValidationLevel.OK:
        response_text += "✓ HEALTHY"
    elif validation.level == ValidationLevel.WARNING:
        response_text += "⚠ WARNING"
    else:
        response_text += "✗ ERROR"

    response_text += f"\n{validation.message}"

    if pool_allocations:
        response_text += "\n\nNote: LVs are automatically created/deleted per test run."
        response_text += "\nUse device_pool_cleanup to remove orphaned LVs from dead processes."

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_teardown(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_teardown tool."""
    pool_name = arguments["pool_name"]
    wipe_data = arguments.get("wipe_data", False)

    config_manager = ConfigManager()
    pool = config_manager.get_pool(pool_name)

    if pool is None:
        return [TextContent(type="text", text=f"Pool '{pool_name}' not found.")]

    # Create LVM manager
    manager = LVMPoolManager(config_manager)

    # Teardown pool
    success = manager.teardown_pool(pool_name, wipe_data=wipe_data)

    if success:
        response_text = f"""Device Pool Removed Successfully!

Pool '{pool_name}' has been torn down.
Device {pool.device} is now available for other uses.
"""
        if wipe_data:
            response_text += "\nData has been wiped from the device."
    else:
        response_text = f"Failed to remove pool '{pool_name}'."

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_resize(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_resize tool."""
    pool_name = arguments.get("pool_name", "default")
    lv_name = arguments["lv_name"]
    new_size = arguments["new_size"]

    config_manager = ConfigManager()
    pool = config_manager.get_pool(pool_name)

    if pool is None:
        return [TextContent(type="text", text=f"Pool '{pool_name}' not found.")]

    manager = LVMPoolManager(config_manager)
    success = manager.resize_volume(pool_name, lv_name, new_size)

    if success:
        response_text = f"""Volume Resized Successfully!

Pool: {pool_name}
LV: {lv_name}
New Size: {new_size}
"""
    else:
        response_text = f"Failed to resize LV '{lv_name}'."

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_snapshot(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_snapshot tool."""
    pool_name = arguments.get("pool_name", "default")
    lv_name = arguments["lv_name"]
    snapshot_name = arguments["snapshot_name"]
    action = arguments["action"]
    snapshot_size = arguments.get("snapshot_size", "1G")

    config_manager = ConfigManager()
    pool = config_manager.get_pool(pool_name)

    if pool is None:
        return [TextContent(type="text", text=f"Pool '{pool_name}' not found.")]

    manager = LVMPoolManager(config_manager)

    if action == "create":
        success = manager.create_snapshot(pool_name, lv_name, snapshot_name, snapshot_size)
        if success:
            response_text = f"""Snapshot Created Successfully!

Pool: {pool_name}
Source LV: {lv_name}
Snapshot: {snapshot_name}
Size: {snapshot_size}

Use this snapshot for debugging or rollback purposes.
To delete: device_pool_snapshot with action='delete'
"""
        else:
            response_text = f"Failed to create snapshot '{snapshot_name}' from LV '{lv_name}'."

    elif action == "delete":
        success = manager.delete_snapshot(pool_name, snapshot_name)
        if success:
            response_text = f"""Snapshot Deleted Successfully!

Pool: {pool_name}
Snapshot: {snapshot_name} has been removed.
"""
        else:
            response_text = f"Failed to delete snapshot '{snapshot_name}'."

    else:
        response_text = f"Unknown action: {action}. Use 'create' or 'delete'."

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_list(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_list tool."""
    config_manager = ConfigManager()
    pools = config_manager.load_pools()

    if not pools:
        return [
            TextContent(
                type="text",
                text="No device pools configured.\n\nUse device_pool_setup to create a pool.",
            )
        ]

    # Get active allocations
    from .device_pool import VolumeStateManager

    state_mgr = VolumeStateManager()
    state = state_mgr._load_state()

    response_text = f"LVM Device Pools ({len(pools)}):\n\n"

    for pool_name, pool in pools.items():
        vg_name = pool.lvm_config.vg_name if pool.lvm_config else "N/A"

        # Count active LVs for this pool
        active_lvs = sum(1 for a in state.get("allocations", []) if a["pool_name"] == pool_name)

        response_text += f"• {pool_name}:\n"
        response_text += f"  Device: {pool.device}\n"
        response_text += f"  Volume Group: {vg_name}\n"
        response_text += f"  Active LVs: {active_lvs}\n"
        response_text += f"  Created: {pool.created_at} by {pool.created_by}\n"
        response_text += "\n"

    response_text += "Use device_pool_status to see detailed information about a pool."

    return [TextContent(type="text", text=response_text)]


async def _handle_device_pool_cleanup(arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle device_pool_cleanup tool."""
    pool_name = arguments.get("pool_name", "default")

    config_manager = ConfigManager()
    manager = LVMPoolManager(config_manager)

    # Clean up orphaned volumes
    cleaned = manager.cleanup_orphaned_volumes(pool_name)

    if cleaned:
        response_text = f"""Cleanup Complete!

Pool: {pool_name}
Cleaned up {len(cleaned)} orphaned volume(s) from dead processes:
"""
        for lv_name in cleaned:
            response_text += f"  - {lv_name}\n"
    else:
        response_text = f"""Cleanup Complete!

Pool: {pool_name}
No orphaned volumes found. All active volumes belong to running processes.
"""

    return [TextContent(type="text", text=response_text)]
