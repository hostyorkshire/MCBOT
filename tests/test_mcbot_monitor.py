"""Tests for mcbot_monitor graceful-degradation behaviour."""

from __future__ import annotations

import importlib
import sys
from io import StringIO
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_monitor(dotenv_available: bool):
    """Import (or re-import) mcbot_monitor, controlling dotenv availability."""
    # Remove any cached module so we can re-import cleanly.
    sys.modules.pop("mcbot_monitor", None)

    if dotenv_available:
        # Ensure a real (or stub) dotenv is importable.
        import types

        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *args, **kwargs: None  # no-op stub
        sys.modules.setdefault("dotenv", fake_dotenv)
    else:
        # Make dotenv un-importable for this reload.
        sys.modules["dotenv"] = None  # type: ignore[assignment]

    try:
        import mcbot_monitor

        return mcbot_monitor
    finally:
        # Restore dotenv slot so other tests are unaffected.
        sys.modules.pop("dotenv", None)
        sys.modules.pop("mcbot_monitor", None)


# ---------------------------------------------------------------------------
# Tests: dotenv unavailable
# ---------------------------------------------------------------------------


class TestDotenvMissing:
    def test_import_does_not_crash(self):
        """mcbot_monitor must import cleanly even when python-dotenv is absent."""
        mod = _reload_monitor(dotenv_available=False)
        assert mod is not None

    def test_dotenv_available_flag_is_false(self):
        mod = _reload_monitor(dotenv_available=False)
        assert mod._DOTENV_AVAILABLE is False

    def test_warning_written_to_stderr(self, capsys):
        _reload_monitor(dotenv_available=False)
        captured = capsys.readouterr()
        assert "python-dotenv not installed" in captured.err
        assert "requirements.txt" in captured.err

    def test_cmd_info_shows_not_installed(self, capsys):
        mod = _reload_monitor(dotenv_available=False)
        # Patch _check_groq to avoid a real network call or missing groq package.
        with patch.object(mod, "_check_groq"):
            mod.cmd_info()
        captured = capsys.readouterr()
        assert "NOT installed" in captured.out

    def test_load_dotenv_noop_does_not_raise(self):
        mod = _reload_monitor(dotenv_available=False)
        # Calling the no-op should not raise.
        mod.load_dotenv()


# ---------------------------------------------------------------------------
# Tests: dotenv available
# ---------------------------------------------------------------------------


class TestDotenvPresent:
    def test_dotenv_available_flag_is_true(self):
        mod = _reload_monitor(dotenv_available=True)
        assert mod._DOTENV_AVAILABLE is True

    def test_no_warning_on_stderr(self, capsys):
        _reload_monitor(dotenv_available=True)
        captured = capsys.readouterr()
        assert "python-dotenv not installed" not in captured.err

    def test_cmd_info_shows_installed(self, capsys):
        mod = _reload_monitor(dotenv_available=True)
        # Patch _check_groq to avoid a real network call or missing groq package.
        with patch.object(mod, "_check_groq"):
            mod.cmd_info()
        captured = capsys.readouterr()
        assert "installed" in captured.out
        assert "NOT installed" not in captured.out
