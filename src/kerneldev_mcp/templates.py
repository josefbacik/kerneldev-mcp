"""
Configuration template management for kernel configurations.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ConfigTemplate:
    """Represents a kernel configuration template."""

    name: str
    category: str  # 'target', 'debug', or 'fragment'
    description: str
    path: Path

    def load(self) -> str:
        """Load the template content."""
        return self.path.read_text()


class TemplateManager:
    """Manages kernel configuration templates."""

    def __init__(self, templates_dir: Optional[Path] = None):
        """Initialize template manager.

        Args:
            templates_dir: Path to templates directory. If None, uses default location.
        """
        if templates_dir is None:
            # Default to templates dir relative to this file
            self.templates_dir = Path(__file__).parent.parent / "config_templates"
        else:
            self.templates_dir = Path(templates_dir)

        self._templates: Dict[Tuple[str, str], ConfigTemplate] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Scan and load all available templates."""
        # Load target templates
        targets_dir = self.templates_dir / "targets"
        if targets_dir.exists():
            for template_file in targets_dir.glob("*.conf"):
                name = template_file.stem
                self._templates[("target", name)] = ConfigTemplate(
                    name=name,
                    category="target",
                    description=self._extract_description(template_file),
                    path=template_file,
                )

        # Load debug templates
        debug_dir = self.templates_dir / "debug"
        if debug_dir.exists():
            for template_file in debug_dir.glob("*.conf"):
                name = template_file.stem
                self._templates[("debug", name)] = ConfigTemplate(
                    name=name,
                    category="debug",
                    description=self._extract_description(template_file),
                    path=template_file,
                )

        # Load fragments
        fragments_dir = self.templates_dir / "fragments"
        if fragments_dir.exists():
            for template_file in fragments_dir.glob("*.conf"):
                name = template_file.stem
                self._templates[("fragment", name)] = ConfigTemplate(
                    name=name,
                    category="fragment",
                    description=self._extract_description(template_file),
                    path=template_file,
                )

    def _extract_description(self, template_file: Path) -> str:
        """Extract description from template comment header."""
        try:
            lines = template_file.read_text().splitlines()
            description_lines = []
            for line in lines:
                if line.startswith("#"):
                    # Remove leading # and whitespace
                    desc_line = line.lstrip("#").strip()
                    if desc_line:
                        description_lines.append(desc_line)
                elif line.strip():
                    # Stop at first non-comment, non-empty line
                    break
            return (
                " ".join(description_lines)
                if description_lines
                else f"Configuration template: {template_file.stem}"
            )
        except Exception:
            return f"Configuration template: {template_file.stem}"

    def list_presets(self, category: Optional[str] = None) -> List[Dict[str, str]]:
        """List all available presets.

        Args:
            category: Optional filter by category ('target', 'debug', 'fragment')

        Returns:
            List of preset information dictionaries
        """
        presets = []
        for (cat, name), template in self._templates.items():
            if category is None or cat == category:
                presets.append({"name": name, "category": cat, "description": template.description})
        return sorted(presets, key=lambda x: (x["category"], x["name"]))

    def get_template(self, category: str, name: str) -> Optional[ConfigTemplate]:
        """Get a specific template.

        Args:
            category: Template category
            name: Template name

        Returns:
            ConfigTemplate if found, None otherwise
        """
        return self._templates.get((category, name))

    def get_target_template(self, name: str) -> Optional[ConfigTemplate]:
        """Get a target template by name."""
        return self.get_template("target", name)

    def get_debug_template(self, name: str) -> Optional[ConfigTemplate]:
        """Get a debug template by name."""
        return self.get_template("debug", name)

    def get_fragment(self, name: str) -> Optional[ConfigTemplate]:
        """Get a fragment by name."""
        return self.get_template("fragment", name)

    def get_targets(self) -> List[str]:
        """Get list of available target names."""
        return sorted([name for (cat, name) in self._templates.keys() if cat == "target"])

    def get_debug_levels(self) -> List[str]:
        """Get list of available debug level names."""
        return sorted([name for (cat, name) in self._templates.keys() if cat == "debug"])

    def get_fragments(self) -> List[str]:
        """Get list of available fragment names."""
        return sorted([name for (cat, name) in self._templates.keys() if cat == "fragment"])
