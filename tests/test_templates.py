"""
Tests for template management.
"""
import pytest
from pathlib import Path
from kerneldev_mcp.templates import TemplateManager, ConfigTemplate


def test_template_manager_initialization():
    """Test that TemplateManager initializes correctly."""
    manager = TemplateManager()
    assert manager.templates_dir.exists()


def test_list_presets():
    """Test listing all presets."""
    manager = TemplateManager()
    presets = manager.list_presets()

    assert len(presets) > 0
    assert all("name" in p for p in presets)
    assert all("category" in p for p in presets)
    assert all("description" in p for p in presets)


def test_list_presets_by_category():
    """Test filtering presets by category."""
    manager = TemplateManager()

    targets = manager.list_presets(category="target")
    assert all(p["category"] == "target" for p in targets)

    debug_levels = manager.list_presets(category="debug")
    assert all(p["category"] == "debug" for p in debug_levels)

    fragments = manager.list_presets(category="fragment")
    assert all(p["category"] == "fragment" for p in fragments)


def test_get_target_template():
    """Test getting a target template."""
    manager = TemplateManager()

    # Should have networking template
    template = manager.get_target_template("networking")
    assert template is not None
    assert template.category == "target"
    assert template.name == "networking"

    # Load content
    content = template.load()
    assert "CONFIG_NET=y" in content
    assert "CONFIG_INET=y" in content


def test_get_debug_template():
    """Test getting a debug template."""
    manager = TemplateManager()

    template = manager.get_debug_template("basic")
    assert template is not None
    assert template.category == "debug"

    content = template.load()
    assert "CONFIG_DEBUG_KERNEL=y" in content


def test_get_fragment():
    """Test getting a fragment."""
    manager = TemplateManager()

    template = manager.get_fragment("kasan")
    assert template is not None
    assert template.category == "fragment"

    content = template.load()
    assert "CONFIG_KASAN=y" in content


def test_get_targets_list():
    """Test getting list of target names."""
    manager = TemplateManager()
    targets = manager.get_targets()

    assert "networking" in targets
    assert "btrfs" in targets
    assert "filesystem" in targets
    assert "boot" in targets
    assert "virtualization" in targets


def test_get_debug_levels_list():
    """Test getting list of debug level names."""
    manager = TemplateManager()
    debug_levels = manager.get_debug_levels()

    assert "minimal" in debug_levels
    assert "basic" in debug_levels
    assert "full_debug" in debug_levels
    assert "sanitizers" in debug_levels
    assert "lockdep" in debug_levels


def test_get_fragments_list():
    """Test getting list of fragment names."""
    manager = TemplateManager()
    fragments = manager.get_fragments()

    assert "kasan" in fragments
    assert "ubsan" in fragments
    assert "kcov" in fragments
    assert "virtme" in fragments
