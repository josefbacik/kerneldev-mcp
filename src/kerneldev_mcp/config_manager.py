"""
Kernel configuration management - generation, merging, and manipulation.
"""
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Union
from dataclasses import dataclass

from .templates import TemplateManager


@dataclass
class CrossCompileConfig:
    """Cross-compilation configuration."""

    arch: str  # Target architecture (arm64, arm, x86_64, riscv, etc.)
    cross_compile_prefix: Optional[str] = None  # e.g., "aarch64-linux-gnu-"
    use_llvm: bool = False  # Use LLVM toolchain instead of GCC

    # Common architecture to toolchain mappings
    ARCH_TOOLCHAINS = {
        "arm64": "aarch64-linux-gnu-",
        "arm": "arm-linux-gnueabihf-",
        "riscv": "riscv64-linux-gnu-",
        "powerpc": "powerpc64le-linux-gnu-",
        "mips": "mips-linux-gnu-",
        "x86_64": None,  # Native compilation
        "x86": None,
    }

    def __post_init__(self):
        """Auto-detect cross-compile prefix if not specified."""
        if self.cross_compile_prefix is None and not self.use_llvm:
            self.cross_compile_prefix = self.ARCH_TOOLCHAINS.get(self.arch)

    def to_make_env(self) -> Dict[str, str]:
        """Convert to environment variables for make commands.

        Returns:
            Dictionary of environment variables (ARCH, CROSS_COMPILE, or LLVM)
        """
        env = {"ARCH": self.arch}

        if self.use_llvm:
            env["LLVM"] = "1"
        elif self.cross_compile_prefix:
            env["CROSS_COMPILE"] = self.cross_compile_prefix

        return env

    def to_make_args(self) -> List[str]:
        """Convert to make command-line arguments.

        Returns:
            List of make arguments (ARCH=..., CROSS_COMPILE=..., etc.)
        """
        args = [f"ARCH={self.arch}"]

        if self.use_llvm:
            args.append("LLVM=1")
        elif self.cross_compile_prefix:
            args.append(f"CROSS_COMPILE={self.cross_compile_prefix}")

        return args


@dataclass
class ConfigOption:
    """Represents a single kernel config option."""

    name: str
    value: Optional[str]  # None means 'is not set'

    def to_config_line(self) -> str:
        """Convert to .config file format."""
        if self.value is None:
            return f"# {self.name} is not set"
        elif self.value in ("y", "m", "n"):
            return f"{self.name}={self.value}"
        else:
            # String or numeric value
            return f"{self.name}={self.value}"

    @classmethod
    def from_config_line(cls, line: str) -> Optional["ConfigOption"]:
        """Parse a config line into a ConfigOption."""
        line = line.strip()

        # Handle "# CONFIG_XXX is not set"
        match = re.match(r"#\s*(CONFIG_\w+)\s+is not set", line)
        if match:
            return cls(name=match.group(1), value=None)

        # Handle "CONFIG_XXX=y|m|n|value"
        match = re.match(r"(CONFIG_\w+)=(.*)", line)
        if match:
            name, value = match.groups()
            # Remove quotes from string values
            value = value.strip('"')
            return cls(name=name, value=value)

        return None


class KernelConfig:
    """Represents a complete kernel configuration."""

    def __init__(self):
        self.options: Dict[str, ConfigOption] = {}
        self.header_comments: List[str] = []

    def set_option(self, name: str, value: Optional[str]) -> None:
        """Set a configuration option."""
        if not name.startswith("CONFIG_"):
            name = f"CONFIG_{name}"
        self.options[name] = ConfigOption(name=name, value=value)

    def get_option(self, name: str) -> Optional[ConfigOption]:
        """Get a configuration option."""
        if not name.startswith("CONFIG_"):
            name = f"CONFIG_{name}"
        return self.options.get(name)

    def merge(self, other: "KernelConfig", overwrite: bool = True) -> None:
        """Merge another config into this one.

        Args:
            other: Config to merge from
            overwrite: If True, other's values overwrite this config's values
        """
        for name, option in other.options.items():
            if overwrite or name not in self.options:
                self.options[name] = option

    def to_config_text(self) -> str:
        """Convert to .config file format."""
        lines = []

        # Add header comments
        for comment in self.header_comments:
            lines.append(f"# {comment}")

        if self.header_comments:
            lines.append("")

        # Sort options for consistent output
        for name in sorted(self.options.keys()):
            option = self.options[name]
            lines.append(option.to_config_line())

        return "\n".join(lines) + "\n"

    @classmethod
    def from_config_text(cls, text: str) -> "KernelConfig":
        """Parse .config file content into KernelConfig."""
        config = cls()
        in_header = True

        for line in text.splitlines():
            line = line.strip()

            # Skip empty lines
            if not line:
                in_header = False
                continue

            # Collect header comments
            if in_header and line.startswith("#") and "is not set" not in line:
                config.header_comments.append(line.lstrip("#").strip())
                continue

            in_header = False

            # Parse config option
            option = ConfigOption.from_config_line(line)
            if option:
                config.options[option.name] = option

        return config

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "KernelConfig":
        """Load config from file."""
        return cls.from_config_text(Path(path).read_text())

    def to_file(self, path: Union[str, Path]) -> None:
        """Save config to file."""
        Path(path).write_text(self.to_config_text())


class ConfigManager:
    """Manages kernel configuration generation and manipulation."""

    def __init__(self, kernel_path: Optional[Path] = None):
        """Initialize config manager.

        Args:
            kernel_path: Path to Linux kernel source tree
        """
        self.template_manager = TemplateManager()
        self.kernel_path = Path(kernel_path) if kernel_path else None

    def generate_config(
        self,
        target: str,
        debug_level: str = "basic",
        architecture: str = "x86_64",
        additional_options: Optional[Dict[str, Optional[str]]] = None,
        fragments: Optional[List[str]] = None
    ) -> KernelConfig:
        """Generate a complete kernel configuration.

        Args:
            target: Target use case (networking, btrfs, etc.)
            debug_level: Debug level (minimal, basic, full_debug, etc.)
            architecture: Target architecture
            additional_options: Additional CONFIG options to set
            fragments: List of fragment names to merge

        Returns:
            Complete kernel configuration
        """
        config = KernelConfig()
        config.header_comments = [
            f"Automatically generated kernel configuration",
            f"Target: {target}",
            f"Debug level: {debug_level}",
            f"Architecture: {architecture}",
        ]

        # Load target template
        target_template = self.template_manager.get_target_template(target)
        if target_template:
            target_config = KernelConfig.from_config_text(target_template.load())
            config.merge(target_config)
        else:
            raise ValueError(f"Unknown target: {target}")

        # Load debug template
        debug_template = self.template_manager.get_debug_template(debug_level)
        if debug_template:
            debug_config = KernelConfig.from_config_text(debug_template.load())
            config.merge(debug_config)
        else:
            raise ValueError(f"Unknown debug level: {debug_level}")

        # Apply fragments
        if fragments:
            for fragment_name in fragments:
                fragment = self.template_manager.get_fragment(fragment_name)
                if fragment:
                    fragment_config = KernelConfig.from_config_text(fragment.load())
                    config.merge(fragment_config)
                else:
                    raise ValueError(f"Unknown fragment: {fragment_name}")

        # Apply additional options
        if additional_options:
            for name, value in additional_options.items():
                config.set_option(name, value)

        # Set architecture
        config.set_option("CONFIG_X86_64", "y" if architecture == "x86_64" else None)
        config.set_option("CONFIG_ARM64", "y" if architecture == "arm64" else None)

        return config

    def merge_configs(
        self,
        base: Union[str, Path, KernelConfig],
        fragments: List[Union[str, Path]],
        output: Optional[Path] = None
    ) -> KernelConfig:
        """Merge multiple configuration fragments.

        Args:
            base: Base configuration (file path, template name, or KernelConfig)
            fragments: List of fragment names or file paths
            output: Optional output file path

        Returns:
            Merged configuration
        """
        # Load base config
        if isinstance(base, KernelConfig):
            config = base
        elif isinstance(base, (str, Path)):
            path = Path(base)
            if path.exists():
                config = KernelConfig.from_file(path)
            else:
                # Try as template name
                parts = str(base).split("/")
                if len(parts) == 2:
                    category, name = parts
                    template = self.template_manager.get_template(category, name)
                    if template:
                        config = KernelConfig.from_config_text(template.load())
                    else:
                        raise ValueError(f"Unknown template: {base}")
                else:
                    raise ValueError(f"Invalid base config: {base}")
        else:
            raise ValueError(f"Invalid base config type: {type(base)}")

        # Merge fragments
        for fragment in fragments:
            if isinstance(fragment, (str, Path)):
                path = Path(fragment)
                if path.exists():
                    fragment_config = KernelConfig.from_file(path)
                else:
                    # Try as fragment name
                    template = self.template_manager.get_fragment(str(fragment))
                    if template:
                        fragment_config = KernelConfig.from_config_text(template.load())
                    else:
                        raise ValueError(f"Unknown fragment: {fragment}")

                config.merge(fragment_config)

        # Save if output specified
        if output:
            config.to_file(output)

        return config

    def apply_config(
        self,
        config: Union[KernelConfig, str, Path],
        kernel_path: Optional[Path] = None,
        merge_with_existing: bool = False,
        cross_compile: Optional[CrossCompileConfig] = None
    ) -> bool:
        """Apply configuration to kernel source tree.

        Args:
            config: Configuration to apply
            kernel_path: Path to kernel source (uses self.kernel_path if None)
            merge_with_existing: If True, merge with existing .config
            cross_compile: Cross-compilation configuration

        Returns:
            True if successful
        """
        if kernel_path is None:
            kernel_path = self.kernel_path

        if kernel_path is None:
            raise ValueError("kernel_path must be specified")

        kernel_path = Path(kernel_path)
        if not kernel_path.exists():
            raise ValueError(f"Kernel path does not exist: {kernel_path}")

        config_path = kernel_path / ".config"

        # Load config if needed
        if isinstance(config, (str, Path)):
            config_obj = KernelConfig.from_file(config)
        else:
            config_obj = config

        # Merge with existing if requested
        if merge_with_existing and config_path.exists():
            existing = KernelConfig.from_file(config_path)
            existing.merge(config_obj)
            config_obj = existing

        # Write config
        config_obj.to_file(config_path)

        # Run olddefconfig to resolve dependencies
        try:
            cmd = ["make", "olddefconfig"]

            # Add cross-compilation arguments if specified
            if cross_compile:
                cmd.extend(cross_compile.to_make_args())

            subprocess.run(
                cmd,
                cwd=kernel_path,
                check=True,
                capture_output=True,
                text=True
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Warning: olddefconfig failed: {e.stderr}")
            return False

    def search_config_options(
        self,
        query: str,
        kernel_path: Optional[Path] = None
    ) -> List[Dict[str, str]]:
        """Search for config options in Kconfig files.

        Args:
            query: Search term
            kernel_path: Path to kernel source

        Returns:
            List of matching config options with info
        """
        if kernel_path is None:
            kernel_path = self.kernel_path

        if kernel_path is None:
            return []

        kernel_path = Path(kernel_path)
        results = []

        # Search in Kconfig files
        for kconfig_file in kernel_path.rglob("Kconfig*"):
            try:
                content = kconfig_file.read_text()
                # Simple search for config options
                for match in re.finditer(
                    rf"config\s+(\w+).*?(?=\nconfig\s|\nendmenu|\nmenu\s|\Z)",
                    content,
                    re.DOTALL | re.IGNORECASE
                ):
                    config_name = match.group(1)
                    if query.lower() in config_name.lower():
                        # Extract help text if available
                        help_match = re.search(r"help\n\s+(.*?)(?=\n\S|\Z)", match.group(0), re.DOTALL)
                        help_text = help_match.group(1).strip() if help_match else "No description available"

                        results.append({
                            "name": f"CONFIG_{config_name}",
                            "description": help_text[:200],  # Limit description length
                            "file": str(kconfig_file.relative_to(kernel_path))
                        })
            except Exception:
                continue

        return results[:50]  # Limit results
