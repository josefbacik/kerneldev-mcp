"""Tests for _prepare_vng_qemu_opts to prevent argument formatting bugs."""

import pytest
from kerneldev_mcp.boot_manager import _prepare_vng_qemu_opts


class TestPrepareVngQemuOpts:
    """Test that QEMU options are formatted correctly for vng command."""

    def test_no_extra_args_returns_q35(self):
        """With no extra args, should return --disable-microvm and --qemu-opts with q35 machine type."""
        result = _prepare_vng_qemu_opts(None)
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_empty_extra_args_returns_q35(self):
        """With empty extra args list, should return --disable-microvm and --qemu-opts with q35."""
        result = _prepare_vng_qemu_opts([])
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_extra_args_without_machine_returns_q35(self):
        """With extra args but no machine type, should return --disable-microvm and q35."""
        result = _prepare_vng_qemu_opts(["--verbose", "--memory", "4G"])
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_user_specified_machine_via_qemu_opts(self):
        """When user specifies machine type, should return empty list."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-machine virt"])
        assert result == []

    def test_user_specified_machine_with_M_flag(self):
        """When user specifies -M instead of -machine, should detect it."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-M virt"])
        assert result == []

    def test_qemu_opts_with_other_options_no_machine(self):
        """--qemu-opts with other options but no machine should still add --disable-microvm and q35."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-cpu host"])
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_multiple_qemu_opts_one_with_machine(self):
        """Multiple --qemu-opts, one with machine, should return empty."""
        result = _prepare_vng_qemu_opts(
            ["--qemu-opts", "-cpu host", "--verbose", "--qemu-opts", "-machine virt"]
        )
        assert result == []

    def test_machine_in_middle_of_qemu_opts_string(self):
        """Machine type in middle of QEMU options string should be detected."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-cpu host -machine q35 -m 4G"])
        assert result == []

    def test_qemu_opts_with_equals_syntax(self):
        """User can specify --qemu-opts=VALUE syntax."""
        result = _prepare_vng_qemu_opts(["--qemu-opts=-machine virt"])
        assert result == []

    def test_qemu_opts_with_equals_syntax_no_machine(self):
        """--qemu-opts=VALUE without machine should still add q35."""
        result = _prepare_vng_qemu_opts(["--qemu-opts=-cpu host"])
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]


class TestArgumentFormatting:
    """Critical tests to prevent the argument formatting bug from returning."""

    def test_result_is_exactly_two_elements(self):
        """Result must be exactly 2 elements when adding --disable-microvm and q35 (using = syntax)."""
        result = _prepare_vng_qemu_opts(None)
        assert len(result) == 2, f"Expected 2 args, got {len(result)}: {result}"

    def test_first_element_is_disable_microvm(self):
        """First element must be --disable-microvm to prevent virtme-ng from auto-selecting microvm."""
        result = _prepare_vng_qemu_opts(None)
        assert result[0] == "--disable-microvm"

    def test_second_element_uses_equals_syntax(self):
        """Second element must use --qemu-opts=VALUE syntax to prevent argparse issues."""
        result = _prepare_vng_qemu_opts(None)
        assert result[1] == "--qemu-opts=-machine q35"
        assert isinstance(result[1], str)
        assert result[1].startswith("--qemu-opts=")
        assert "-machine q35" in result[1]

    def test_not_split_into_multiple_args(self):
        """CRITICAL: Must not split into multiple args like the original bug."""
        result = _prepare_vng_qemu_opts(None)
        # The bug was: ["--qemu-opts", "-machine", "q35"] (too many args)
        # Also wrong: ["--qemu-opts", "-machine q35"] (argparse issue)
        # Correct is:  ["--disable-microvm", "--qemu-opts=-machine q35"]
        assert result[1] != "--qemu-opts", (
            "REGRESSION: QEMU options are split incorrectly (the original bug)"
        )
        assert len(result) == 2, "REGRESSION: Wrong number of elements in result"
        assert "--qemu-opts=" in result[1], (
            "REGRESSION: Not using = syntax which causes argparse issues"
        )

    def test_machine_and_value_in_same_element(self):
        """-machine and q35 must be in the same element with = binding."""
        result = _prepare_vng_qemu_opts(None)
        # The second element should contain everything for QEMU opts
        assert "-machine" in result[1]
        assert "q35" in result[1]
        assert "--qemu-opts=" in result[1]

    def test_vng_command_line_compatibility(self):
        """Result should be compatible with vng command line parsing."""
        result = _prepare_vng_qemu_opts(None)
        # Simulate how the result would be used in a command
        cmd = ["vng", "--verbose"] + result + ["--memory", "4G"]

        # Should be: vng --verbose --disable-microvm --qemu-opts=-machine q35 --memory 4G
        # This prevents argparse from treating -machine as a separate flag
        assert cmd == [
            "vng",
            "--verbose",
            "--disable-microvm",
            "--qemu-opts=-machine q35",
            "--memory",
            "4G",
        ]

    def test_empty_result_when_user_specifies_machine(self):
        """When user specifies machine, result must be empty list, not None."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-machine virt"])
        assert result == []
        assert isinstance(result, list)

    def test_original_bug_regression_3_args(self):
        """Prevent regression to the 3-arg bug from first commit."""
        result = _prepare_vng_qemu_opts(None)
        # The original bug: ["--qemu-opts", "-machine", "q35"]
        assert result != ["--qemu-opts", "-machine", "q35"], (
            "REGRESSION: Original 3-arg bug from commit 659177d"
        )

    def test_second_attempt_regression_disable_microvm_only(self):
        """Prevent regression to only using --disable-microvm (commit 68eccb9)."""
        result = _prepare_vng_qemu_opts(None)
        # The second attempt bug: ["--disable-microvm"] (missing explicit q35)
        assert result != ["--disable-microvm"], (
            "REGRESSION: Only disabling microvm without setting q35 (commit 68eccb9)"
        )

    def test_third_attempt_regression_2_args_no_equals(self):
        """Prevent regression to 2-arg format without = syntax (commit 912ba7c)."""
        result = _prepare_vng_qemu_opts(None)
        # The third attempt bug: ["--qemu-opts", "-machine q35"] (argparse issue)
        assert result != ["--qemu-opts", "-machine q35"], (
            "REGRESSION: Using 2 args without = syntax (commit 912ba7c)"
        )

    def test_fourth_attempt_regression_missing_disable_microvm(self):
        """Prevent regression to missing --disable-microvm (commit 98a3940)."""
        result = _prepare_vng_qemu_opts(None)
        # The fourth attempt bug: ["--qemu-opts=-machine q35"] (missing --disable-microvm)
        assert result != ["--qemu-opts=-machine q35"], (
            "REGRESSION: Missing --disable-microvm flag (commit 98a3940)"
        )


class TestEdgeCases:
    """Test edge cases and unusual input."""

    def test_qemu_opts_at_end(self):
        """--qemu-opts at end of list with machine type."""
        result = _prepare_vng_qemu_opts(
            ["--verbose", "--memory", "4G", "--qemu-opts", "-machine virt"]
        )
        assert result == []

    def test_multiple_qemu_opts_none_with_machine(self):
        """Multiple --qemu-opts, none with machine, should add q35."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-cpu host", "--qemu-opts", "-smp 4"])
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_case_sensitivity(self):
        """Test that -Machine is not detected (should be -machine)."""
        # -Machine is not a valid QEMU option (should be -machine)
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-Machine virt"])
        # Since we look for "-machine" or "-M ", this shouldn't match
        # So we should still add --disable-microvm and q35
        assert result == ["--disable-microvm", "--qemu-opts=-machine q35"]

    def test_preserves_user_choice_with_microvm(self):
        """If user explicitly wants microvm, we should respect it."""
        # User explicitly specifies microvm machine type
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-machine microvm"])
        # We should respect their choice and not add our defaults
        assert result == []

    def test_machine_type_with_options(self):
        """Machine type with additional options should be detected."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-machine virt,accel=kvm"])
        assert result == []

    def test_M_with_space(self):
        """-M flag with space should be detected."""
        result = _prepare_vng_qemu_opts(["--qemu-opts", "-M virt"])
        assert result == []

    def test_qemu_opts_equals_with_M(self):
        """--qemu-opts=VALUE syntax with -M flag."""
        result = _prepare_vng_qemu_opts(["--qemu-opts=-M virt"])
        assert result == []


class TestReturnValueInvariant:
    """Test that return value invariants always hold."""

    @pytest.mark.parametrize(
        "extra_args",
        [
            None,
            [],
            ["--verbose"],
        ],
    )
    def test_always_two_elements_when_adding_machine(self, extra_args):
        """When adding machine type, result must be exactly 2 elements (--disable-microvm and --qemu-opts)."""
        result = _prepare_vng_qemu_opts(extra_args)
        assert len(result) == 2
        assert result[0] == "--disable-microvm"
        assert result[1].startswith("--qemu-opts=")

    @pytest.mark.parametrize(
        "extra_args",
        [
            ["--qemu-opts", "-machine virt"],
            ["--qemu-opts", "-M virt"],
            ["--qemu-opts=-machine virt"],
            ["--qemu-opts", "-cpu host -machine q35"],
        ],
    )
    def test_always_empty_when_user_specifies_machine(self, extra_args):
        """When user specifies machine, result must always be empty list."""
        result = _prepare_vng_qemu_opts(extra_args)
        assert result == []
        assert isinstance(result, list)

    def test_result_is_always_list(self):
        """Result must always be a list, never None or other type."""
        assert isinstance(_prepare_vng_qemu_opts(None), list)
        assert isinstance(_prepare_vng_qemu_opts([]), list)
        assert isinstance(_prepare_vng_qemu_opts(["--verbose"]), list)
        assert isinstance(_prepare_vng_qemu_opts(["--qemu-opts", "-machine virt"]), list)

    def test_result_contains_only_strings(self):
        """Result must only contain strings, no other types."""
        result = _prepare_vng_qemu_opts(None)
        assert all(isinstance(item, str) for item in result)

        result = _prepare_vng_qemu_opts(["--verbose"])
        assert all(isinstance(item, str) for item in result)
