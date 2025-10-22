"""
Tests for boot management and validation.
"""
import pytest
from pathlib import Path
from kerneldev_mcp.boot_manager import DmesgMessage, DmesgParser, BootResult, BootManager


def test_dmesg_message_creation():
    """Test creating DmesgMessage objects."""
    msg = DmesgMessage(
        timestamp=1.234567,
        level="err",
        subsystem="EXT4",
        message="Failed to mount filesystem"
    )

    assert msg.timestamp == 1.234567
    assert msg.level == "err"
    assert msg.subsystem == "EXT4"
    assert "Failed to mount filesystem" in str(msg)


def test_dmesg_parser_simple_line():
    """Test parsing a simple dmesg line."""
    line = "[    1.234567] This is a test message"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.timestamp == 1.234567
    assert "This is a test message" in msg.message


def test_dmesg_parser_with_level():
    """Test parsing dmesg line with log level."""
    line = "[    1.234567] <3>Error: Something went wrong"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "err"
    assert "Error: Something went wrong" in msg.message


def test_dmesg_parser_with_subsystem():
    """Test parsing dmesg line with subsystem."""
    line = "[    1.234567] BTRFS: space cache generation has changed"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.subsystem == "BTRFS"
    assert "space cache generation has changed" in msg.message


def test_dmesg_parser_detect_error():
    """Test detecting errors in dmesg."""
    line = "[    5.123456] Device initialization failed"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "err"


def test_dmesg_parser_detect_warning():
    """Test detecting warnings in dmesg."""
    line = "[    2.345678] Warning: deprecated feature in use"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "warn"


def test_dmesg_parser_detect_panic():
    """Test detecting kernel panic."""
    line = "[   10.123456] Kernel panic - not syncing: VFS: Unable to mount root fs"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "emerg"
    assert "panic" in msg.message.lower()


def test_dmesg_parser_detect_oops():
    """Test detecting kernel oops."""
    line = "[   15.987654] BUG: unable to handle kernel NULL pointer dereference"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "crit"


def test_dmesg_parser_analyze_clean_boot():
    """Test analyzing clean boot dmesg output."""
    dmesg_text = """[    0.000000] Linux version 6.11.0-test
[    0.123456] Command line: BOOT_IMAGE=/boot/vmlinuz
[    1.234567] Calibrating delay loop... done.
[    2.345678] Mount-cache hash table entries: 2048
[    3.456789] NET: Registered PF_INET protocol family
[    4.567890] EXT4-fs: mounted filesystem with ordered data mode
"""

    errors, warnings, panics, oops = DmesgParser.analyze_dmesg(dmesg_text)

    assert len(errors) == 0
    assert len(warnings) == 0
    assert len(panics) == 0
    assert len(oops) == 0


def test_dmesg_parser_analyze_with_errors():
    """Test analyzing dmesg with errors."""
    dmesg_text = """[    0.000000] Linux version 6.11.0-test
[    1.234567] Device initialization failed
[    2.345678] Error: Unable to allocate memory
[    3.456789] Warning: deprecated syscall used
[    4.567890] Normal message here
"""

    errors, warnings, panics, oops = DmesgParser.analyze_dmesg(dmesg_text)

    assert len(errors) >= 2  # At least the two error messages
    assert len(warnings) >= 1  # At least the warning
    assert len(panics) == 0
    assert len(oops) == 0


def test_dmesg_parser_analyze_with_panic():
    """Test analyzing dmesg with kernel panic."""
    dmesg_text = """[    0.000000] Linux version 6.11.0-test
[    1.234567] Normal boot message
[   10.123456] Kernel panic - not syncing: Fatal exception
[   10.234567] Call trace follows
"""

    errors, warnings, panics, oops = DmesgParser.analyze_dmesg(dmesg_text)

    assert len(panics) >= 1
    assert any("panic" in p.message.lower() for p in panics)


def test_dmesg_parser_analyze_with_oops():
    """Test analyzing dmesg with kernel oops."""
    dmesg_text = """[    0.000000] Linux version 6.11.0-test
[    5.123456] Normal message
[   15.987654] BUG: unable to handle kernel paging request
[   15.987655] Oops: 0002 [#1] SMP
"""

    errors, warnings, panics, oops = DmesgParser.analyze_dmesg(dmesg_text)

    assert len(oops) >= 1


def test_boot_result_properties():
    """Test BootResult property calculations."""
    result = BootResult(
        success=True,
        duration=10.5,
        boot_completed=True,
        kernel_version="6.11.0-test",
        errors=[
            DmesgMessage(1.0, "err", None, "Error 1"),
            DmesgMessage(2.0, "err", None, "Error 2"),
        ],
        warnings=[
            DmesgMessage(3.0, "warn", None, "Warning 1"),
        ],
        panics=[],
        oops=[]
    )

    assert result.error_count == 2
    assert result.warning_count == 1
    assert result.panic_count == 0
    assert result.oops_count == 0
    assert result.has_critical_issues is False


def test_boot_result_critical_issues():
    """Test detecting critical issues in boot result."""
    result = BootResult(
        success=False,
        duration=5.0,
        boot_completed=True,
        panics=[
            DmesgMessage(10.0, "emerg", None, "Kernel panic"),
        ],
        oops=[]
    )

    assert result.has_critical_issues is True
    assert "CRITICAL" in result.summary()


def test_boot_result_summary_clean():
    """Test boot result summary for clean boot."""
    result = BootResult(
        success=True,
        duration=8.5,
        boot_completed=True,
        kernel_version="6.11.0"
    )

    summary = result.summary()
    assert "✓" in summary
    assert "successful" in summary.lower()
    assert "no issues" in summary.lower()


def test_boot_result_summary_with_warnings():
    """Test boot result summary with warnings."""
    result = BootResult(
        success=True,
        duration=10.0,
        boot_completed=True,
        warnings=[DmesgMessage(1.0, "warn", None, "Test warning")]
    )

    summary = result.summary()
    assert "✓" in summary
    assert "1 warnings" in summary or "1 warning" in summary


def test_boot_result_summary_failed():
    """Test boot result summary for failed boot."""
    result = BootResult(
        success=False,
        duration=30.0,
        boot_completed=False,
        timeout_occurred=True
    )

    summary = result.summary()
    assert "✗" in summary
    assert "failed" in summary.lower() or "timed out" in summary.lower()


def test_boot_manager_check_virtme_ng():
    """Test checking if virtme-ng is available."""
    manager = BootManager(Path.cwd())

    # This test will pass or fail depending on whether virtme-ng is installed
    # We just verify the method doesn't crash
    result = manager.check_virtme_ng()
    assert isinstance(result, bool)


def test_dmesg_parser_empty_line():
    """Test parsing empty lines."""
    msg = DmesgParser.parse_dmesg_line("")
    assert msg is None

    msg = DmesgParser.parse_dmesg_line("   ")
    assert msg is None


def test_dmesg_parser_malformed_line():
    """Test parsing malformed dmesg lines."""
    # Line without timestamp
    msg = DmesgParser.parse_dmesg_line("Just a plain message")
    assert msg is not None
    assert msg.message == "Just a plain message"


def test_boot_result_no_errors():
    """Test BootResult with no errors."""
    result = BootResult(
        success=True,
        duration=5.0,
        boot_completed=True
    )

    assert result.error_count == 0
    assert result.warning_count == 0
    assert result.panic_count == 0
    assert result.oops_count == 0


def test_dmesg_multiple_error_keywords():
    """Test message with multiple error keywords."""
    line = "[    1.234567] Device failed: Error during initialization"
    msg = DmesgParser.parse_dmesg_line(line)

    assert msg is not None
    assert msg.level == "err"
    assert "failed" in msg.message.lower()
    assert "error" in msg.message.lower()
