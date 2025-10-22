# Example Usage

This document shows practical examples of using the kerneldev-mcp server.

## Basic Configuration Generation

### Example 1: BTRFS Testing with Sanitizers

```python
# Request
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",
    "debug_level": "sanitizers"
  }
}

# Response: Complete .config file optimized for BTRFS testing with KASAN, UBSAN, etc.
```

This generates a configuration with:
- BTRFS filesystem with all debug options
- KASAN for memory error detection
- UBSAN for undefined behavior detection
- Full debug symbols
- All necessary block layer features

### Example 2: Network Testing with Lockdep

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "networking",
    "debug_level": "lockdep",
    "additional_options": {
      "CONFIG_NET_DROP_MONITOR": "y"
    }
  }
}
```

Features:
- Complete network stack (TCP/IP, IPv6, netfilter)
- Lock debugging and deadlock detection
- Network namespaces and virtual networking
- eBPF/XDP support
- Packet drop monitoring

### Example 3: Fast Virtme-ng Testing

```python
{
  "tool": "get_config_template",
  "params": {
    "target": "virtualization",
    "debug_level": "minimal",
    "fragments": ["virtme"]
  }
}
```

Optimized for:
- Quick boot times
- VirtioFS for fast I/O
- Minimal unnecessary drivers
- Overlay filesystem for CoW

## Advanced Workflows

### Workflow 1: Creating and Using Custom Fragments

```python
# Step 1: Create custom fragment
{
  "tool": "create_config_fragment",
  "params": {
    "name": "my_custom_debug",
    "options": {
      "CONFIG_DYNAMIC_DEBUG": "y",
      "CONFIG_DEBUG_VM": "y",
      "CONFIG_DEBUG_MEMORY_INIT": "y"
    },
    "description": "Custom memory debugging options"
  }
}

# Step 2: Save the fragment to src/config_templates/fragments/my_custom_debug.conf

# Step 3: Use in configuration
{
  "tool": "get_config_template",
  "params": {
    "target": "filesystem",
    "debug_level": "basic",
    "fragments": ["my_custom_debug"]
  }
}
```

### Workflow 2: Merging Multiple Configurations

```python
# Create a custom config from multiple sources
{
  "tool": "merge_configs",
  "params": {
    "base": "target/boot",
    "fragments": ["kasan", "ubsan", "virtme"],
    "output": "/tmp/custom.config"
  }
}
```

### Workflow 3: Applying Configuration to Kernel

```python
# Generate config
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",
    "debug_level": "full_debug",
    "additional_options": {
      "CONFIG_BTRFS_DEBUG": "y"
    }
  }
}

# Apply to kernel (separate call)
{
  "tool": "apply_config",
  "params": {
    "kernel_path": "/home/user/linux",
    "config_source": "inline",
    "config_content": "<paste generated config here>"
  }
}
```

## Complete Testing Scenarios

### Scenario 1: Setting Up for Filesystem Testing (fstests)

```python
# 1. Generate configuration
{
  "tool": "get_config_template",
  "params": {
    "target": "filesystem",
    "debug_level": "full_debug",
    "additional_options": {
      "CONFIG_FAULT_INJECTION": "y",
      "CONFIG_FAIL_MAKE_REQUEST": "y"
    }
  }
}

# 2. Apply to kernel
{
  "tool": "apply_config",
  "params": {
    "kernel_path": "~/linux",
    "config_source": "inline",
    "config_content": "<config from step 1>"
  }
}

# 3. Get build commands
{
  "tool": "generate_build_config",
  "params": {
    "target": "filesystem",
    "optimization": "debug",
    "ccache": true,
    "kernel_path": "~/linux"
  }
}

# 4. Build and test with virtme-ng
# cd ~/linux
# vng --build
# vng -- <run fstests>
```

### Scenario 2: Network Stack Development

```python
# Development config with moderate debugging
{
  "tool": "get_config_template",
  "params": {
    "target": "networking",
    "debug_level": "basic",
    "fragments": ["virtme"],
    "additional_options": {
      "CONFIG_NET_SCH_NETEM": "y",  # Network emulation
      "CONFIG_NET_DROP_MONITOR": "y"
    }
  }
}

# Apply and build
{
  "tool": "apply_config",
  "params": {
    "kernel_path": "~/linux",
    "config_source": "target/networking",
    "merge_with_existing": false
  }
}

# Fast iteration with virtme-ng
# vng --build -- ip link show
# vng -- tc qdisc show
```

### Scenario 3: Finding Memory Bugs with KASAN

```python
# Heavy sanitizer build
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",
    "debug_level": "sanitizers",
    "additional_options": {
      "CONFIG_KASAN_INLINE": "y",
      "CONFIG_KASAN_STACK": "y"
    }
  }
}

# Note: This will be slow (~3x performance impact)
# but catches memory errors effectively
```

### Scenario 4: Performance Testing

```python
# Minimal overhead for accurate performance measurements
{
  "tool": "get_config_template",
  "params": {
    "target": "btrfs",
    "debug_level": "performance",
    "fragments": ["performance"]
  }
}

# This config includes:
# - No debug features
# - Performance monitoring tools (perf)
# - Minimal overhead
```

## Searching for Configuration Options

```python
# Find KASAN-related options
{
  "tool": "search_config_options",
  "params": {
    "query": "KASAN",
    "kernel_path": "~/linux"
  }
}

# Returns:
# CONFIG_KASAN - Kernel Address Sanitizer
# CONFIG_KASAN_INLINE - Inline instrumentation
# CONFIG_KASAN_GENERIC - Generic mode
# ... etc
```

## Validating Configurations

```python
# Check what's in a config
{
  "tool": "validate_config",
  "params": {
    "config_path": "~/linux/.config",
    "kernel_path": "~/linux"
  }
}

# Returns statistics:
# Total options: 8234
# Built-in (y): 1234
# Modules (m): 567
# Disabled: 6433
```

## Integration with AI Assistant

When working with an AI assistant that has access to this MCP server:

### Example Conversation

**User**: "I need to test BTRFS compression with KASAN enabled"

**AI**: I'll configure the kernel for BTRFS testing with KASAN. Let me generate the configuration:

```
<uses get_config_template tool>
target: btrfs
debug_level: sanitizers
additional_options: { CONFIG_BTRFS_DEBUG: y }
```

The configuration is ready. Key features enabled:
- BTRFS with all debugging options
- KASAN for memory error detection
- Compression support (zlib, lzo, zstd)
- Full debug symbols

Would you like me to:
1. Apply this to your kernel at ~/linux?
2. Generate the build commands?
3. Add additional features?

**User**: "Yes, apply it and give me the build commands"

**AI**:
```
<uses apply_config tool>
<uses generate_build_config tool>
```

Configuration applied to ~/linux/.config

Build commands:
```bash
cd ~/linux
export CCACHE_DIR=$HOME/.ccache
export KBUILD_BUILD_TIMESTAMP=''
make O=build CC="ccache gcc" -j$(nproc)
```

After building, you can test with virtme-ng:
```bash
vng --build -- mount -t btrfs /dev/vda /mnt
```

## Tips and Best Practices

1. **Start with minimal debug for fast iteration**, then add sanitizers when hunting bugs
2. **Use fragments** to compose configurations instead of modifying templates
3. **Save custom fragments** for project-specific options
4. **Use virtme-ng fragment** for development to speed up boot times
5. **Enable lockdep early** in development to catch locking issues
6. **Use performance config** for benchmarking to avoid measurement interference
7. **Merge with existing** when you want to preserve some manual settings
