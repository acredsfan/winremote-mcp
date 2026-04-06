"""Tests for non-admin compatibility helpers in launcher_ui."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from winremote.launcher_ui import (
    LauncherSettings,
    _apply_non_admin_safe_defaults,
    _coerce_setting_value,
    _enabled_profiles,
    _parse_csv,
    _set_profile_enabled,
    load_launcher_settings,
    save_launcher_settings,
)


def test_non_admin_safe_defaults_disables_tier3_and_low_port():
    s = LauncherSettings(enable_tier3=True, server_port=443, enforce_non_admin_safe_mode=True)
    changes = _apply_non_admin_safe_defaults(s, is_admin=False)

    assert s.enable_tier3 is False
    assert s.server_port == 8090
    assert len(changes) == 2


def test_non_admin_safe_defaults_noop_when_admin():
    s = LauncherSettings(enable_tier3=True, server_port=443, enforce_non_admin_safe_mode=True)
    changes = _apply_non_admin_safe_defaults(s, is_admin=True)

    assert s.enable_tier3 is True
    assert s.server_port == 443
    assert changes == []


def test_non_admin_safe_defaults_respects_opt_out():
    s = LauncherSettings(enable_tier3=True, server_port=443, enforce_non_admin_safe_mode=False)
    changes = _apply_non_admin_safe_defaults(s, is_admin=False)

    assert s.enable_tier3 is True
    assert s.server_port == 443
    assert changes == []


def test_coerce_setting_value_bool_strings():
    assert _coerce_setting_value(False, "false") is False
    assert _coerce_setting_value(False, "0") is False
    assert _coerce_setting_value(False, "true") is True
    assert _coerce_setting_value(False, "yes") is True


def test_parse_csv_trims_and_drops_empty_tokens():
    assert _parse_csv(" Snapshot, UIFind ,, Shell ") == ["Snapshot", "UIFind", "Shell"]
    assert _parse_csv("") == []


def test_settings_roundtrip_preserves_false_booleans(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "launcher_settings.toml"

    import winremote.launcher_ui as launcher_ui

    monkeypatch.setattr(launcher_ui, "_settings_path", lambda: settings_file)

    s = LauncherSettings(
        auto_start_server=False,
        enable_tier3=False,
        disable_tier2=False,
        enforce_non_admin_safe_mode=False,
    )
    save_launcher_settings(s)

    loaded = load_launcher_settings()
    assert loaded.auto_start_server is False
    assert loaded.enable_tier3 is False
    assert loaded.disable_tier2 is False
    assert loaded.enforce_non_admin_safe_mode is False


def test_enabled_profiles_defaults_all_enabled():
    s = LauncherSettings()
    enabled = _enabled_profiles(s)
    assert set(enabled) == {"default", "chatgpt", "copilot", "claude", "excel"}


def test_disable_non_selected_profile():
    s = LauncherSettings(selected_profile="copilot")
    changed, msg = _set_profile_enabled(s, "excel", False)
    assert changed is True
    assert "disabled" in msg
    assert s.profile_excel_enabled is False
    assert s.selected_profile == "copilot"


def test_disable_selected_profile_switches_active():
    s = LauncherSettings(selected_profile="copilot")
    changed, msg = _set_profile_enabled(s, "copilot", False)
    assert changed is True
    assert "switched" in msg
    assert s.profile_copilot_enabled is False
    assert s.selected_profile != "copilot"
    assert s.selected_profile in _enabled_profiles(s)


def test_cannot_disable_last_enabled_profile():
    s = LauncherSettings(
        selected_profile="default",
        profile_default_enabled=True,
        profile_chatgpt_enabled=False,
        profile_copilot_enabled=False,
        profile_claude_enabled=False,
        profile_excel_enabled=False,
    )
    changed, msg = _set_profile_enabled(s, "default", False)
    assert changed is False
    assert "At least one profile" in msg
    assert s.profile_default_enabled is True


def test_enable_profile_after_disabled():
    s = LauncherSettings(profile_excel_enabled=False)
    changed, msg = _set_profile_enabled(s, "excel", True)
    assert changed is True
    assert "enabled" in msg
    assert s.profile_excel_enabled is True


# ---------------------------------------------------------------------------
# SSL certificate helper tests
# ---------------------------------------------------------------------------


def test_ssl_dir_returns_path_under_winremote():
    from winremote.launcher_ui import _ssl_dir
    d = _ssl_dir()
    assert isinstance(d, Path)
    assert d.name == "ssl"
    assert d.parent.name == "winremote"


def test_generate_ssl_cert_raises_when_cryptography_missing(monkeypatch):
    """If 'cryptography' is not installed, a clear RuntimeError is raised."""
    import builtins
    real_import = builtins.__import__

    def _block_cryptography(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    from winremote.launcher_ui import _generate_ssl_cert
    monkeypatch.setattr(builtins, "__import__", _block_cryptography)

    with pytest.raises(RuntimeError, match="cryptography"):
        _generate_ssl_cert(Path("/tmp/ignored"), trust=False)


def test_generate_ssl_cert_creates_pem_files(tmp_path: Path):
    """When cryptography is available, cert and key PEM files are written."""
    pytest.importorskip("cryptography")

    from winremote.launcher_ui import _generate_ssl_cert
    cert_p, key_p = _generate_ssl_cert(tmp_path, trust=False)

    assert cert_p.exists()
    assert key_p.exists()
    assert cert_p.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert key_p.read_bytes().startswith(b"-----BEGIN RSA PRIVATE KEY-----")


def test_generate_ssl_cert_uses_output_dir(tmp_path: Path):
    """Cert and key land in the given output directory."""
    pytest.importorskip("cryptography")

    from winremote.launcher_ui import _generate_ssl_cert
    sub = tmp_path / "ssl_out"
    cert_p, key_p = _generate_ssl_cert(sub, trust=False)

    assert cert_p.parent == sub
    assert key_p.parent == sub


def test_generate_ssl_cert_san_covers_localhost(tmp_path: Path):
    """The generated cert must have a SAN for 'localhost'."""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from winremote.launcher_ui import _generate_ssl_cert

    cert_p, _ = _generate_ssl_cert(tmp_path, trust=False)
    cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san.value.get_values_for_type(x509.DNSName)
    assert "localhost" in dns_names


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only trust store")
def test_trust_cert_windows_user_store_uses_cryptoapi(tmp_path: Path, monkeypatch):
    """_trust_cert_windows_user_store calls the expected CryptoAPI entry points."""
    pytest.importorskip("cryptography")

    import ctypes
    from winremote.launcher_ui import _generate_ssl_cert, _trust_cert_windows_user_store

    cert_p, _ = _generate_ssl_cert(tmp_path, trust=False)

    calls: list[str] = []

    class _FakeFunc:
        """Callable stub that records invocations and accepts restype/argtypes assignment."""
        def __init__(self, name: str, ret):
            self.name = name
            self._ret = ret
            self.restype = None
            self.argtypes = None

        def __call__(self, *_a, **_kw):
            calls.append(self.name)
            return self._ret

    class _FakeDLL:
        def __init__(self):
            self.CertCreateCertificateContext = _FakeFunc("CertCreateCertificateContext", 0xDEADBEEF)
            self.CertOpenStore = _FakeFunc("CertOpenStore", 0xCAFEBABE)
            self.CertAddCertificateContextToStore = _FakeFunc("CertAddCertificateContextToStore", 1)
            self.CertCloseStore = _FakeFunc("CertCloseStore", 1)
            self.CertFreeCertificateContext = _FakeFunc("CertFreeCertificateContext", 1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: _FakeDLL())

    _trust_cert_windows_user_store(cert_p)

    assert "CertCreateCertificateContext" in calls
    assert "CertOpenStore" in calls
    assert "CertAddCertificateContextToStore" in calls
    assert "CertCloseStore" in calls
    assert "CertFreeCertificateContext" in calls
