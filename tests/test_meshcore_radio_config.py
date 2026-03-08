"""Tests for meshcore_radio_config helper functions.

These tests only exercise pure helper functions (validation, command building,
port listing) and do not require real serial hardware.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper: import the module with serial stubbed out so tests run without
# pyserial being installed.
# ---------------------------------------------------------------------------


def _load_module():
    """Import meshcore_radio_config with a stub serial module."""
    sys.modules.pop("meshcore_radio_config", None)

    # Provide a minimal stub for 'serial' so the import succeeds.
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = MagicMock()
    fake_serial.SerialException = OSError
    sys.modules.setdefault("serial", fake_serial)

    import meshcore_radio_config as mrc

    return mrc


mrc = _load_module()


# ===========================================================================
# validate_frequency
# ===========================================================================


class TestValidateFrequency:
    def test_default_uk_eu_frequency_is_valid(self):
        ok, reason = mrc.validate_frequency(869.525)
        assert ok is True
        assert reason == ""

    def test_lower_eu868_bound_is_valid(self):
        ok, _ = mrc.validate_frequency(863.0)
        assert ok is True

    def test_upper_eu868_bound_is_valid(self):
        ok, _ = mrc.validate_frequency(870.0)
        assert ok is True

    def test_midband_eu868_is_valid(self):
        ok, _ = mrc.validate_frequency(868.1)
        assert ok is True

    def test_915_mhz_is_rejected(self):
        ok, reason = mrc.validate_frequency(915.0)
        assert ok is False
        assert "915" in reason or "ILLEGAL" in reason.upper() or "NOT legal" in reason

    def test_915_mhz_reason_mentions_uk_eu(self):
        _, reason = mrc.validate_frequency(915.0)
        assert "UK" in reason or "EU" in reason

    def test_zero_is_rejected(self):
        ok, _ = mrc.validate_frequency(0.0)
        assert ok is False

    def test_negative_is_rejected(self):
        ok, _ = mrc.validate_frequency(-1.0)
        assert ok is False

    def test_below_eu868_range_is_rejected(self):
        ok, _ = mrc.validate_frequency(433.0)
        assert ok is False

    def test_above_eu868_range_but_below_900_is_rejected(self):
        # 871 MHz is just above the band but not yet 915-band.
        ok, _ = mrc.validate_frequency(871.0)
        assert ok is False

    def test_900_mhz_boundary_is_rejected_as_915band(self):
        ok, _ = mrc.validate_frequency(900.0)
        assert ok is False


# ===========================================================================
# validate_node_name
# ===========================================================================


class TestValidateNodeName:
    def test_simple_name_is_valid(self):
        ok, _ = mrc.validate_node_name("MyNode")
        assert ok is True

    def test_name_with_hyphens_is_valid(self):
        ok, _ = mrc.validate_node_name("my-node-01")
        assert ok is True

    def test_name_with_underscores_is_valid(self):
        ok, _ = mrc.validate_node_name("my_node_01")
        assert ok is True

    def test_empty_name_is_rejected(self):
        ok, reason = mrc.validate_node_name("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_whitespace_only_is_rejected(self):
        ok, _ = mrc.validate_node_name("   ")
        assert ok is False

    def test_name_with_spaces_is_rejected(self):
        ok, reason = mrc.validate_node_name("my node")
        assert ok is False
        assert "space" in reason.lower()

    def test_name_exactly_32_chars_is_valid(self):
        name = "a" * 32
        ok, _ = mrc.validate_node_name(name)
        assert ok is True

    def test_name_33_chars_is_rejected(self):
        name = "a" * 33
        ok, reason = mrc.validate_node_name(name)
        assert ok is False
        assert "long" in reason.lower() or "32" in reason


# ===========================================================================
# validate_latitude
# ===========================================================================


class TestValidateLatitude:
    def test_zero_is_valid(self):
        ok, _ = mrc.validate_latitude(0.0)
        assert ok is True

    def test_positive_90_is_valid(self):
        ok, _ = mrc.validate_latitude(90.0)
        assert ok is True

    def test_negative_90_is_valid(self):
        ok, _ = mrc.validate_latitude(-90.0)
        assert ok is True

    def test_typical_uk_lat_is_valid(self):
        ok, _ = mrc.validate_latitude(51.5)  # London
        assert ok is True

    def test_above_90_is_rejected(self):
        ok, reason = mrc.validate_latitude(91.0)
        assert ok is False
        assert "90" in reason

    def test_below_minus_90_is_rejected(self):
        ok, _ = mrc.validate_latitude(-91.0)
        assert ok is False


# ===========================================================================
# validate_longitude
# ===========================================================================


class TestValidateLongitude:
    def test_zero_is_valid(self):
        ok, _ = mrc.validate_longitude(0.0)
        assert ok is True

    def test_positive_180_is_valid(self):
        ok, _ = mrc.validate_longitude(180.0)
        assert ok is True

    def test_negative_180_is_valid(self):
        ok, _ = mrc.validate_longitude(-180.0)
        assert ok is True

    def test_typical_uk_lon_is_valid(self):
        ok, _ = mrc.validate_longitude(-0.12)  # London
        assert ok is True

    def test_above_180_is_rejected(self):
        ok, reason = mrc.validate_longitude(181.0)
        assert ok is False
        assert "180" in reason

    def test_below_minus_180_is_rejected(self):
        _, _ = mrc.validate_longitude(-181.0)
        assert _ is not None


# ===========================================================================
# build_set_command
# ===========================================================================


class TestBuildSetCommand:
    def test_freq_command(self):
        assert mrc.build_set_command("freq", 869.525) == "set freq 869.525"

    def test_name_command(self):
        assert mrc.build_set_command("name", "MyNode") == "set name MyNode"

    def test_lat_command(self):
        assert mrc.build_set_command("lat", 53.8) == "set lat 53.8"

    def test_lon_command(self):
        assert mrc.build_set_command("lon", -1.5) == "set lon -1.5"


# ===========================================================================
# build_commands
# ===========================================================================


class TestBuildCommands:
    def test_no_args_returns_empty_list(self):
        assert mrc.build_commands() == []

    def test_freq_only(self):
        cmds = mrc.build_commands(freq_mhz=869.525)
        assert cmds == ["set freq 869.525"]

    def test_name_only(self):
        cmds = mrc.build_commands(name="MyNode")
        assert cmds == ["set name MyNode"]

    def test_all_args(self):
        cmds = mrc.build_commands(freq_mhz=869.525, name="Node1", lat=53.8, lon=-1.5)
        assert "set freq 869.525" in cmds
        assert "set name Node1" in cmds
        assert "set lat 53.8" in cmds
        assert "set lon -1.5" in cmds
        assert len(cmds) == 4

    def test_order_is_freq_name_lat_lon(self):
        cmds = mrc.build_commands(freq_mhz=869.525, name="Node1", lat=53.8, lon=-1.5)
        assert cmds[0].startswith("set freq")
        assert cmds[1].startswith("set name")
        assert cmds[2].startswith("set lat")
        assert cmds[3].startswith("set lon")


# ===========================================================================
# RadioSettings
# ===========================================================================


class TestRadioSettings:
    def test_default_all_none(self):
        s = mrc.RadioSettings()
        assert s.freq_mhz is None
        assert s.name is None
        assert s.lat is None
        assert s.lon is None

    def test_to_commands_empty_when_no_settings(self):
        s = mrc.RadioSettings()
        assert s.to_commands() == []

    def test_to_commands_with_all_settings(self):
        s = mrc.RadioSettings(freq_mhz=869.525, name="Node1", lat=53.8, lon=-1.5)
        cmds = s.to_commands()
        assert len(cmds) == 4

    def test_summary_shows_not_set_for_none_values(self):
        s = mrc.RadioSettings()
        summary = s.summary()
        assert any("(not set)" in line for line in summary)

    def test_summary_shows_values_when_set(self):
        s = mrc.RadioSettings(freq_mhz=869.525, name="Node1", lat=53.8, lon=-1.5)
        summary = "\n".join(s.summary())
        assert "869.525" in summary
        assert "Node1" in summary
        assert "53.8" in summary
        assert "-1.5" in summary


# ===========================================================================
# list_serial_ports (mocked glob)
# ===========================================================================


class TestListSerialPorts:
    def test_returns_sorted_list(self):
        with patch("meshcore_radio_config._glob.glob") as mock_glob:
            mock_glob.side_effect = lambda pattern: (
                ["/dev/ttyUSB1", "/dev/ttyUSB0"] if "USB" in pattern else ["/dev/ttyACM0"]
            )
            ports = mrc.list_serial_ports()
        # Each pattern's results are sorted individually, then concatenated.
        assert "/dev/ttyUSB0" in ports
        assert "/dev/ttyUSB1" in ports
        assert "/dev/ttyACM0" in ports

    def test_returns_empty_when_no_devices(self):
        with patch("meshcore_radio_config._glob.glob", return_value=[]):
            ports = mrc.list_serial_ports()
        assert ports == []

    def test_returns_only_usb_and_acm(self):
        with patch("meshcore_radio_config._glob.glob") as mock_glob:
            mock_glob.side_effect = lambda p: ["/dev/ttyUSB0"] if "USB" in p else []
            ports = mrc.list_serial_ports()
        assert ports == ["/dev/ttyUSB0"]


# ===========================================================================
# open_serial error paths (no hardware required)
# ===========================================================================


class TestOpenSerialErrors:
    def test_raises_import_error_when_serial_not_available(self):
        saved = mrc._SERIAL_AVAILABLE
        try:
            mrc._SERIAL_AVAILABLE = False
            with pytest.raises(ImportError, match="pyserial"):
                mrc.open_serial("/dev/ttyUSB0")
        finally:
            mrc._SERIAL_AVAILABLE = saved

    def test_raises_permission_error_for_unreadable_device(self, tmp_path):
        # Create a file that exists but has no read/write permissions.
        device = tmp_path / "ttyUSB0"
        device.write_text("")
        device.chmod(0o000)
        try:
            with pytest.raises(PermissionError, match="dialout"):
                mrc.open_serial(str(device))
        finally:
            device.chmod(0o644)  # Restore so tmp_path cleanup works.


# ===========================================================================
# parse_pubkey_from_response
# ===========================================================================


class TestParsePubkeyFromResponse:
    def test_colon_prefixed_line_returns_key(self):
        response = "pubkey: abcdef1234567890abcdef1234567890"
        key = mrc.parse_pubkey_from_response(response)
        assert key == "abcdef1234567890abcdef1234567890"

    def test_ok_prefixed_line_returns_key(self):
        response = "OK abcdef1234567890abcdef1234567890"
        key = mrc.parse_pubkey_from_response(response)
        assert key == "abcdef1234567890abcdef1234567890"

    def test_bare_hex_string_returns_key(self):
        key_hex = "abcdef1234567890abcdef1234567890"
        key = mrc.parse_pubkey_from_response(key_hex)
        assert key == key_hex

    def test_multiline_response_extracts_key(self):
        response = "Status: OK\npubkey: 0011223344556677\nEOF"
        key = mrc.parse_pubkey_from_response(response)
        assert key == "0011223344556677"

    def test_empty_response_returns_none(self):
        assert mrc.parse_pubkey_from_response("") is None

    def test_no_hex_content_returns_none(self):
        assert mrc.parse_pubkey_from_response("Error: command not found") is None

    def test_key_shorter_than_minimum_is_ignored_for_bare(self):
        # Bare hex strings shorter than 16 chars should not be treated as a key.
        assert mrc.parse_pubkey_from_response("deadbeef") is None

    def test_colon_key_at_least_8_chars_is_returned(self):
        response = "key: deadbeef"
        key = mrc.parse_pubkey_from_response(response)
        assert key == "deadbeef"

    def test_uppercase_hex_is_accepted(self):
        response = "pubkey: ABCDEF1234567890ABCDEF1234567890"
        key = mrc.parse_pubkey_from_response(response)
        assert key == "ABCDEF1234567890ABCDEF1234567890"

    def test_whitespace_around_key_is_stripped(self):
        response = "pubkey:   abcdef1234567890   "
        key = mrc.parse_pubkey_from_response(response)
        assert key == "abcdef1234567890"

    def test_non_hex_after_colon_returns_none(self):
        response = "info: this is not a key"
        assert mrc.parse_pubkey_from_response(response) is None


# ===========================================================================
# fetch_pubkey
# ===========================================================================


class TestFetchPubkey:
    def test_sends_get_pubkey_command(self):
        mock_ser = MagicMock()
        with patch.object(mrc, "send_command", return_value="") as mock_send:
            mrc.fetch_pubkey(mock_ser)
        mock_send.assert_called_once_with(mock_ser, "get pubkey")

    def test_returns_raw_response_string(self):
        mock_ser = MagicMock()
        expected = "pubkey: abcdef1234567890"
        with patch.object(mrc, "send_command", return_value=expected):
            result = mrc.fetch_pubkey(mock_ser)
        assert result == expected

    def test_returns_empty_string_on_no_response(self):
        mock_ser = MagicMock()
        with patch.object(mrc, "send_command", return_value=""):
            result = mrc.fetch_pubkey(mock_ser)
        assert result == ""
