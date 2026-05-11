"""Tests for non-admin compatibility helpers in launcher_ui."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from winremote.launcher_ui import (
    LauncherSettings,
    _apply_non_admin_safe_defaults,
    _coerce_setting_value,
    _enabled_profiles,
    _is_windows_startup_enabled,
    _launcher_startup_command,
    _parse_csv,
    _set_windows_startup_enabled,
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


def test_launcher_startup_command_includes_startup_flag(tmp_path: Path, monkeypatch):
    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(exe))

    cmd = _launcher_startup_command()

    assert "winremote.launcher_app" in cmd
    assert "--startup" in cmd


class _FakeWinRegKey:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_winreg_module(store: dict[str, str]):
    def open_key(_root, _path, *_args):
        if "winremote-tray" not in store:
            raise OSError("missing")
        return _FakeWinRegKey(store)

    def create_key(_root, _path):
        return _FakeWinRegKey(store)

    def query_value_ex(_key, name):
        if name not in store:
            raise OSError("missing")
        return store[name], 1

    def set_value_ex(_key, name, _reserved, _reg_type, value):
        store[name] = value

    def delete_value(_key, name):
        if name not in store:
            raise FileNotFoundError(name)
        del store[name]

    return types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_READ=1,
        REG_SZ=1,
        OpenKey=open_key,
        CreateKey=create_key,
        QueryValueEx=query_value_ex,
        SetValueEx=set_value_ex,
        DeleteValue=delete_value,
    )


def test_windows_startup_registration_roundtrip(monkeypatch):
    store: dict[str, str] = {}
    fake_winreg = _fake_winreg_module(store)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    ok_enable, _ = _set_windows_startup_enabled(True)
    assert ok_enable is True
    assert "winremote-tray" in store
    assert _is_windows_startup_enabled() is True

    ok_disable, _ = _set_windows_startup_enabled(False)
    assert ok_disable is True
    assert "winremote-tray" not in store
    assert _is_windows_startup_enabled() is False


def test_trayapp_describe_startup_status(monkeypatch):
    store: dict[str, str] = {}
    fake_winreg = _fake_winreg_module(store)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    import winremote.launcher_ui as launcher_ui

    class _FakeTk:
        def withdraw(self):
            return None

        def title(self, _s):
            return None

    monkeypatch.setattr(launcher_ui.tk, "Tk", _FakeTk)

    app = launcher_ui.TrayApp()
    app.settings.start_server_on_windows_startup = True

    assert app.describe_startup_status() == "Disabled"

    _set_windows_startup_enabled(True)
    assert app.describe_startup_status() == "Enabled (server auto-start on startup: on)"

    app.settings.start_server_on_windows_startup = False
    assert app.describe_startup_status() == "Enabled (server auto-start on startup: off)"


def test_trayapp_starts_roblox_studio_harness(monkeypatch):
    import winremote.launcher_ui as launcher_ui

    class _FakeTk:
        def withdraw(self):
            return None

        def title(self, _s):
            return None

    class _FakeHistory:
        def append(self, _event):
            return None

    class _FakeProc:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return None

        def kill(self):
            return None

    monkeypatch.setattr(launcher_ui.tk, "Tk", _FakeTk)
    monkeypatch.setattr(launcher_ui, "HistoryStore", lambda: _FakeHistory())
    monkeypatch.setattr(launcher_ui, "load_launcher_settings", lambda: launcher_ui.LauncherSettings())
    monkeypatch.setattr(launcher_ui, "save_launcher_settings", lambda _settings: None)
    monkeypatch.setattr(launcher_ui, "_sync_windows_startup_registration", lambda _settings: (True, ""))
    monkeypatch.setattr(launcher_ui.TrayApp, "_harness_running_elsewhere", lambda self: False)
    monkeypatch.setattr(launcher_ui.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())

    app = launcher_ui.TrayApp()
    app.start_roblox_studio_harness()

    assert app._managed_harness_process() is not None
    assert app.describe_harness_status() == "Running (managed PID 4321)"


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
    assert set(enabled) == {"default", "chatgpt", "copilot", "copilot-cli", "claude", "excel"}


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
        profile_copilot_cli_enabled=False,
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
