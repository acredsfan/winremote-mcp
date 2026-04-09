"""System tray app and dashboard window for the winremote-mcp launcher.

Architecture
------------
* ``TrayApp`` is the main coordinator. It owns ``ServerManager``,
  ``TunnelManager``, and ``HistoryStore``.
* The pystray icon runs in a daemon thread so that tkinter can own the
  main thread's event loop (``root.mainloop()``).
* State-change callbacks from manager threads post actions into a
  ``queue.Queue`` which is drained by a 100 ms ``root.after()`` poll.
* ``DashboardWindow``, ``LogWindow``, and ``SettingsDialog`` are
  ``tk.Toplevel`` widgets created lazily when requested.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
import tkinter.ttk as ttk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import pystray
    from pystray import Icon as TrayIcon
    from pystray import Menu as TrayMenu
    from pystray import MenuItem as TrayItem
    _HAS_PYSTRAY = True
except ImportError:
    _HAS_PYSTRAY = False

from PIL import Image, ImageDraw

from .launcher import ServerManager, ServerSettings, ServerState, VALID_PROFILES, PROFILE_DESCRIPTIONS
from .launcher_history import EventType, HistoryEvent, HistoryStore
from .launcher_tunnel import TunnelManager, TunnelSettings, TunnelState


# ---------------------------------------------------------------------------
# Launcher-level settings (GUI / non-server concerns)
# ---------------------------------------------------------------------------

@dataclass
class LauncherSettings:
    """Launcher-only preferences stored in launcher_settings.toml."""

    server_config_path: str = ""          # path to winremote.toml or ""
    cloudflared_path: str = ""            # path to cloudflared binary or ""
    poll_interval: float = 4.0
    history_retention_days: int = 30
    log_max_lines: int = 2000
    auto_start_server: bool = False
    selected_profile: str = "default"
    server_host: str = "127.0.0.1"
    server_port: int = 8090
    auth_key: str = ""
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    transport: str = "streamable-http"
    enable_tier3: bool = False
    disable_tier2: bool = False
    ip_allowlist_csv: str = ""
    tools_enable_csv: str = ""
    tools_exclude_csv: str = ""
    enforce_non_admin_safe_mode: bool = True

    # Profile availability toggles (GUI activate/deactivate)
    profile_default_enabled: bool = True
    profile_chatgpt_enabled: bool = True
    profile_copilot_enabled: bool = True
    profile_claude_enabled: bool = True
    profile_excel_enabled: bool = True

    # Tunnel
    tunnel_target_url: str = ""           # derived from server settings if blank
    tunnel_config_path: str = ""          # optional named-tunnel config YAML
    tunnel_api_token: str = ""            # Cloudflare Zero Trust tunnel token (takes priority over config)
    cloudflare_api_key: str = ""          # Cloudflare REST API token (for managed tunnel + DNS)
    cloudflare_account_id: str = ""       # Cloudflare account id
    cloudflare_zone_id: str = ""          # Cloudflare DNS zone id
    tunnel_dns_name: str = ""             # e.g. mcp.example.com
    tunnel_name: str = "winremote-mcp"    # display name for created tunnel


def _settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
    else:
        base = Path("~/.config").expanduser()
    return base / "winremote" / "launcher_settings.toml"


def _is_user_admin() -> bool:
    """Return True when current process has admin rights.

    On non-Windows platforms we conservatively return True because this launcher
    is Windows-focused and permission semantics differ.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _apply_non_admin_safe_defaults(settings: LauncherSettings, *, is_admin: bool) -> list[str]:
    """Mutate settings for standard-user compatibility and return applied changes."""
    changes: list[str] = []
    if is_admin or not settings.enforce_non_admin_safe_mode:
        return changes

    if settings.enable_tier3:
        settings.enable_tier3 = False
        changes.append("Disabled tier3 tools in non-admin mode")

    if settings.server_port < 1024:
        settings.server_port = 8090
        changes.append("Moved server port to 8090 (non-privileged)")

    return changes


_PROFILE_ENABLE_FIELDS: dict[str, str] = {
    "default": "profile_default_enabled",
    "chatgpt": "profile_chatgpt_enabled",
    "copilot": "profile_copilot_enabled",
    "claude": "profile_claude_enabled",
    "excel": "profile_excel_enabled",
}


def _enabled_profiles(settings: LauncherSettings) -> list[str]:
    """Return enabled profiles and normalize settings to keep at least one active."""
    enabled = [
        p for p in VALID_PROFILES if bool(getattr(settings, _PROFILE_ENABLE_FIELDS[p], True))
    ]

    # Always keep at least one profile enabled.
    if not enabled:
        settings.profile_default_enabled = True
        enabled = ["default"]

    # Ensure selected profile is enabled.
    if settings.selected_profile not in enabled:
        settings.selected_profile = enabled[0]

    return enabled


def _set_profile_enabled(settings: LauncherSettings, profile: str, enabled: bool) -> tuple[bool, str]:
    """Enable/disable a profile in settings, ensuring validity.

    Returns ``(changed, message)``.
    """
    if profile not in _PROFILE_ENABLE_FIELDS:
        return False, f"Unknown profile: {profile}"

    field = _PROFILE_ENABLE_FIELDS[profile]
    current = bool(getattr(settings, field, True))
    if current == enabled:
        return False, f"Profile '{profile}' already {'enabled' if enabled else 'disabled'}"

    # Guard: at least one profile must remain enabled.
    if not enabled:
        currently_enabled = _enabled_profiles(settings)
        if len(currently_enabled) <= 1 and profile in currently_enabled:
            return False, "At least one profile must remain enabled"

    was_selected = settings.selected_profile == profile
    setattr(settings, field, enabled)
    enabled_now = _enabled_profiles(settings)

    if not enabled and was_selected:
        return True, (
            f"Profile '{profile}' disabled; active profile switched to "
            f"'{settings.selected_profile}'"
        )

    if not enabled:
        return True, f"Profile '{profile}' disabled"

    # If nothing selected (corrupt settings), normalization may have selected this/another.
    if settings.selected_profile not in enabled_now:
        settings.selected_profile = enabled_now[0]

    return True, f"Profile '{profile}' enabled"


def load_launcher_settings() -> LauncherSettings:
    path = _settings_path()
    if not path.exists():
        return LauncherSettings()
    try:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        s = LauncherSettings()
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, _coerce_setting_value(getattr(s, k), v))
        return s
    except Exception:
        return LauncherSettings()


def save_launcher_settings(s: LauncherSettings) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = {_toml_value(v)}" for k, v in s.__dict__.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # string
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _coerce_setting_value(current: Any, raw: Any) -> Any:
    """Coerce persisted values to the type of *current* safely."""
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return str(raw)


def _parse_csv(value: str) -> list[str]:
    """Parse comma-separated string into non-empty trimmed tokens."""
    return [tok.strip() for tok in value.split(",") if tok.strip()]


# ---------------------------------------------------------------------------
# SSL certificate helpers
# ---------------------------------------------------------------------------

def _ssl_dir() -> Path:
    """Return the default directory for generated SSL files."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
    else:
        base = Path("~/.config").expanduser()
    return base / "winremote" / "ssl"


def _generate_ssl_cert(
    output_dir: Path,
    hostname: str = "localhost",
    *,
    trust: bool = True,
) -> tuple[Path, Path]:
    """Generate a self-signed TLS cert + key pair.

    Covers *localhost*, *127.0.0.1*, and *::1* via Subject Alternative Names.
    When *trust* is ``True`` and running on Windows, the certificate is
    imported into ``CurrentUser\\Root`` via the Windows CryptoAPI — no
    administrator rights are required.

    Returns ``(cert_path, key_path)``.
    """
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required for SSL cert generation. "
            "Install it with: pip install winremote-mcp[gui]"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    cert_path = output_dir / "winremote.crt"
    key_path = output_dir / "winremote.key"

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "winremote-mcp"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    if trust and sys.platform == "win32":
        _trust_cert_windows_user_store(cert_path)

    return cert_path, key_path


def _trust_cert_windows_user_store(cert_path: Path) -> None:
    """Import *cert_path* into ``CurrentUser\\Root`` via the Windows CryptoAPI.

    Does not require administrator privileges.  Depending on the Windows
    security policy, the user may see a one-time confirmation dialog.
    """
    import base64
    import ctypes
    import ctypes.wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    # Declare argument and return types so 64-bit pointers are not truncated.
    _HANDLE = ctypes.c_void_p
    crypt32.CertCreateCertificateContext.restype = _HANDLE
    crypt32.CertCreateCertificateContext.argtypes = [
        ctypes.c_uint32,                     # dwCertEncodingType
        ctypes.POINTER(ctypes.c_ubyte),      # pbCertEncoded
        ctypes.c_uint32,                     # cbCertEncoded
    ]
    crypt32.CertOpenStore.restype = _HANDLE
    crypt32.CertOpenStore.argtypes = [
        ctypes.c_void_p,   # lpszStoreProvider  (CERT_STORE_PROV_SYSTEM = 10)
        ctypes.c_uint32,   # dwEncodingType
        ctypes.c_void_p,   # hCryptProv
        ctypes.c_uint32,   # dwFlags
        ctypes.c_wchar_p,  # pvPara (LPCWSTR store name for CERT_STORE_PROV_SYSTEM)
    ]
    crypt32.CertAddCertificateContextToStore.restype = ctypes.wintypes.BOOL
    crypt32.CertAddCertificateContextToStore.argtypes = [
        _HANDLE,           # hCertStore
        _HANDLE,           # pCertContext
        ctypes.c_uint32,   # dwAddDisposition
        ctypes.c_void_p,   # ppStoreContext (optional out, pass NULL)
    ]
    crypt32.CertCloseStore.restype = ctypes.wintypes.BOOL
    crypt32.CertCloseStore.argtypes = [_HANDLE, ctypes.c_uint32]
    crypt32.CertFreeCertificateContext.restype = ctypes.wintypes.BOOL
    crypt32.CertFreeCertificateContext.argtypes = [_HANDLE]

    # Convert PEM → DER
    pem = cert_path.read_bytes()
    b64 = b"".join(
        line for line in pem.splitlines()
        if line and not line.startswith(b"-----")
    )
    der = base64.b64decode(b64)
    der_buf = (ctypes.c_ubyte * len(der))(*der)

    X509_ASN_ENCODING = 1
    context = crypt32.CertCreateCertificateContext(X509_ASN_ENCODING, der_buf, len(der))
    if not context:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        CERT_STORE_PROV_SYSTEM = 10
        CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
        store = crypt32.CertOpenStore(
            CERT_STORE_PROV_SYSTEM,
            0,
            None,
            CERT_SYSTEM_STORE_CURRENT_USER,
            "ROOT",
        )
        if not store:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            CERT_STORE_ADD_REPLACE_EXISTING = 4
            ok = crypt32.CertAddCertificateContextToStore(
                store, context, CERT_STORE_ADD_REPLACE_EXISTING, None
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            crypt32.CertCloseStore(store, 0)
    finally:
        crypt32.CertFreeCertificateContext(context)


# ---------------------------------------------------------------------------
# Tray icon images (PIL)
# ---------------------------------------------------------------------------

_ICON_SIZE = 64


def _make_icon(state: ServerState, tunnel: TunnelState = TunnelState.STOPPED) -> Image.Image:
    """Draw a simple coloured icon representing server + tunnel state."""
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Server circle (left half)
    server_color = {
        ServerState.RUNNING: "#22c55e",     # green
        ServerState.STARTING: "#f59e0b",    # amber
        ServerState.DEGRADED: "#f97316",    # orange
        ServerState.STOPPING: "#f59e0b",    # amber
        ServerState.ERROR: "#ef4444",       # red
        ServerState.STOPPED: "#6b7280",     # gray
    }.get(state, "#6b7280")

    # Tunnel dot (top-right)
    tunnel_color = {
        TunnelState.CONNECTED: "#22c55e",
        TunnelState.STARTING: "#f59e0b",
        TunnelState.STOPPING: "#f59e0b",
        TunnelState.ERROR: "#ef4444",
        TunnelState.STOPPED: "#6b7280",
    }.get(tunnel, "#6b7280")

    # Main server circle
    margin = 6
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=server_color,
    )

    # Small tunnel indicator dot (top right)
    dot_r = 14
    draw.ellipse(
        [_ICON_SIZE - dot_r - 2, 2, _ICON_SIZE - 2, dot_r + 2],
        fill=tunnel_color,
        outline="#ffffff",
        width=2,
    )
    return img


# ---------------------------------------------------------------------------
# Log buffer (thread-safe ring buffer for captured process output)
# ---------------------------------------------------------------------------

class _LogBuffer:
    def __init__(self, max_lines: int = 2000) -> None:
        self._lines: list[tuple[str, bool]] = []   # (text, is_stderr)
        self._max = max_lines
        self._lock = threading.Lock()
        self._callbacks: list[Any] = []

    def append(self, line: str, is_stderr: bool) -> None:
        with self._lock:
            self._lines.append((line, is_stderr))
            if len(self._lines) > self._max:
                self._lines = self._lines[-self._max :]

    def get_all(self) -> list[tuple[str, bool]]:
        with self._lock:
            return list(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------

class DashboardWindow:
    """A tkinter Toplevel that shows server/tunnel status and recent history."""

    def __init__(self, root: tk.Tk, app: "TrayApp") -> None:
        self._root = root
        self._app = app
        self._win: tk.Toplevel | None = None

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Toplevel(self._root)
        win.title("winremote-mcp Launcher")
        win.geometry("680x560")
        win.resizable(True, True)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        self._win = win

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # --- Status tab ---
        status_frame = ttk.Frame(nb)
        nb.add(status_frame, text="  Status  ")
        self._build_status_tab(status_frame)

        # --- Events tab ---
        events_frame = ttk.Frame(nb)
        nb.add(events_frame, text="  Events  ")
        self._build_events_tab(events_frame)

        # --- Logs tab ---
        logs_frame = ttk.Frame(nb)
        nb.add(logs_frame, text="  Logs  ")
        self._build_logs_tab(logs_frame)

        # --- Controls tab ---
        controls_frame = ttk.Frame(nb)
        nb.add(controls_frame, text="  Controls  ")
        self._build_controls_tab(controls_frame)

        # Schedule periodic refresh
        self._refresh_dashboard()

    def _build_status_tab(self, parent: ttk.Frame) -> None:
        # Server card
        srv_lf = ttk.LabelFrame(parent, text="Server")
        srv_lf.pack(fill=tk.X, padx=8, pady=(8, 4))

        self._srv_state_var = tk.StringVar(value="–")
        self._srv_uptime_var = tk.StringVar(value="–")
        self._srv_profile_var = tk.StringVar(value="–")
        self._srv_url_var = tk.StringVar(value="–")
        self._srv_conflict_var = tk.StringVar(value="–")

        _grid_row(srv_lf, 0, "State:", self._srv_state_var)
        _grid_row(srv_lf, 1, "Uptime:", self._srv_uptime_var)
        _grid_row(srv_lf, 2, "Profile:", self._srv_profile_var)
        _grid_row(srv_lf, 3, "URL:", self._srv_url_var)
        _grid_row(srv_lf, 4, "Port conflict:", self._srv_conflict_var)

        # Server action buttons
        srv_btn = ttk.Frame(parent)
        srv_btn.pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(srv_btn, text="Start", command=self._app.start_server).pack(side=tk.LEFT, padx=2)
        ttk.Button(srv_btn, text="Stop", command=self._app.stop_server).pack(side=tk.LEFT, padx=2)
        ttk.Button(srv_btn, text="Restart", command=self._app.restart_server).pack(side=tk.LEFT, padx=2)
        ttk.Button(srv_btn, text="Stop Port Occupant", command=self._app.stop_conflicting_server).pack(side=tk.LEFT, padx=8)

        # Tunnel card
        tun_lf = ttk.LabelFrame(parent, text="Cloudflare Tunnel")
        tun_lf.pack(fill=tk.X, padx=8, pady=(8, 4))

        self._tun_state_var = tk.StringVar(value="–")
        self._tun_url_var = tk.StringVar(value="–")
        self._tun_uptime_var = tk.StringVar(value="–")

        _grid_row(tun_lf, 0, "State:", self._tun_state_var)
        _grid_row(tun_lf, 1, "Public URL:", self._tun_url_var)
        _grid_row(tun_lf, 2, "Uptime:", self._tun_uptime_var)

        tun_btn = ttk.Frame(parent)
        tun_btn.pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(tun_btn, text="Start Tunnel", command=self._app.start_tunnel).pack(side=tk.LEFT, padx=2)
        ttk.Button(tun_btn, text="Stop Tunnel", command=self._app.stop_tunnel).pack(side=tk.LEFT, padx=2)

    def _build_events_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=8, pady=4)
        ttk.Button(toolbar, text="Refresh", command=self._load_events).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Clear display", command=self._clear_events).pack(side=tk.LEFT, padx=4)

        cols = ("time", "type", "detail")
        tree = ttk.Treeview(parent, columns=cols, show="headings", height=18)
        tree.heading("time", text="Time")
        tree.heading("type", text="Event")
        tree.heading("detail", text="Detail")
        tree.column("time", width=140, stretch=False)
        tree.column("type", width=160, stretch=False)
        tree.column("detail", width=320)

        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=4)
        vsb.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8), pady=4)
        self._events_tree = tree
        self._load_events()

    def _build_logs_tab(self, parent: ttk.Frame) -> None:
        """Live stdout/stderr view for quick debugging from the dashboard."""
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=8, pady=4)

        self._dash_logs_autoscroll_var = tk.BooleanVar(value=True)
        self._dash_logs_errors_only_var = tk.BooleanVar(value=False)

        ttk.Button(toolbar, text="Clear", command=self._clear_dashboard_logs).pack(side=tk.LEFT)
        ttk.Checkbutton(
            toolbar,
            text="Auto-scroll",
            variable=self._dash_logs_autoscroll_var,
        ).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Checkbutton(
            toolbar,
            text="Errors only",
            variable=self._dash_logs_errors_only_var,
        ).pack(side=tk.LEFT, padx=4)

        self._dash_logs_count_var = tk.StringVar(value="0 lines")
        ttk.Label(toolbar, textvariable=self._dash_logs_count_var, foreground="#6b7280").pack(side=tk.RIGHT)

        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        txt = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9), state=tk.DISABLED)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        txt.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        txt.tag_config("stderr", foreground="#ef4444")

        self._dash_logs_text = txt
        self._dash_logs_last_key: tuple[int, bool] = (-1, False)
        self._poll_dashboard_logs()

    def _poll_dashboard_logs(self) -> None:
        if not (self._win and self._win.winfo_exists()):
            return
        if not hasattr(self, "_dash_logs_text"):
            self._win.after(500, self._poll_dashboard_logs)
            return

        errors_only = bool(self._dash_logs_errors_only_var.get())
        lines = self._app._log_buffer.get_all()
        visible_lines = [ln for ln in lines if (ln[1] if errors_only else True)]
        refresh_key = (len(visible_lines), errors_only)

        if refresh_key != self._dash_logs_last_key:
            txt = self._dash_logs_text
            txt.configure(state=tk.NORMAL)
            txt.delete("1.0", tk.END)
            for line_text, is_stderr in visible_lines:
                tag = "stderr" if is_stderr else ""
                txt.insert(tk.END, line_text + "\n", tag)
            txt.configure(state=tk.DISABLED)
            if self._dash_logs_autoscroll_var.get():
                txt.see(tk.END)
            self._dash_logs_last_key = refresh_key
            self._dash_logs_count_var.set(f"{len(visible_lines)} lines")

        self._win.after(500, self._poll_dashboard_logs)

    def _clear_dashboard_logs(self) -> None:
        self._app._log_buffer.clear()
        if hasattr(self, "_dash_logs_text"):
            self._dash_logs_text.configure(state=tk.NORMAL)
            self._dash_logs_text.delete("1.0", tk.END)
            self._dash_logs_text.configure(state=tk.DISABLED)
        self._dash_logs_last_key = (0, bool(self._dash_logs_errors_only_var.get()))
        self._dash_logs_count_var.set("0 lines")

    def _build_controls_tab(self, parent: ttk.Frame) -> None:
        """Runtime controls that mirror key CLI launch options."""
        s = self._app.settings
        _enabled_profiles(s)

        # Scrollable container
        outer = ttk.Frame(parent)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._ctrl_vars: dict[str, tk.Variable] = {}
        row = 0

        def add_text(label: str, key: str, value: str, width: int = 40) -> None:
            nonlocal row
            var = tk.StringVar(value=value)
            self._ctrl_vars[key] = var
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(body, textvariable=var, width=width).grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            row += 1

        def add_bool(label: str, key: str, value: bool) -> None:
            nonlocal row
            var = tk.BooleanVar(value=value)
            self._ctrl_vars[key] = var
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(body, variable=var).grid(row=row, column=1, sticky="w", padx=4, pady=3)
            row += 1

        def add_file_picker(label: str, key: str, value: str) -> None:
            nonlocal row
            var = tk.StringVar(value=value)
            self._ctrl_vars[key] = var
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(body, textvariable=var, width=36).grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            ttk.Button(
                body, text="…",
                command=lambda v=var: v.set(filedialog.askopenfilename(title=f"Select {label}") or v.get()),
                width=3,
            ).grid(row=row, column=2, padx=(0, 8), pady=3)
            row += 1

        # Core CLI-equivalent options
        add_text("Config path", "server_config_path", s.server_config_path)
        add_text("Transport", "transport", s.transport)
        add_text("Host", "server_host", s.server_host)
        add_text("Port", "server_port", str(s.server_port), width=10)
        add_text("Auth key", "auth_key", s.auth_key)
        add_file_picker("SSL certfile", "ssl_certfile", s.ssl_certfile)
        add_file_picker("SSL keyfile", "ssl_keyfile", s.ssl_keyfile)

        # SSL cert generation section
        ssl_lf = ttk.LabelFrame(body, text="Generate self-signed SSL cert")
        ssl_lf.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=(4, 3))
        row += 1
        ssl_lf.columnconfigure(1, weight=1)

        ssl_dir_var = tk.StringVar(value=str(_ssl_dir()))
        ttk.Label(ssl_lf, text="Output folder:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(ssl_lf, textvariable=ssl_dir_var, width=36).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(
            ssl_lf, text="…",
            command=lambda: ssl_dir_var.set(
                filedialog.askdirectory(title="SSL output folder") or ssl_dir_var.get()
            ),
            width=3,
        ).grid(row=0, column=2, padx=(0, 8), pady=3)

        ssl_status_var = tk.StringVar(value="")
        ssl_status_lbl = ttk.Label(ssl_lf, textvariable=ssl_status_var, foreground="#6b7280", wraplength=480)
        ssl_status_lbl.grid(row=1, column=0, columnspan=3, sticky="w", padx=8)

        def _do_generate_ssl() -> None:
            ssl_status_var.set("Generating\u2026")
            ssl_status_lbl.configure(foreground="#6b7280")

            def _worker() -> None:
                try:
                    out_dir = Path(ssl_dir_var.get().strip() or str(_ssl_dir()))
                    cert_p, key_p = _generate_ssl_cert(out_dir)
                    body.after(0, lambda cp=str(cert_p), kp=str(key_p): _on_done(cp, kp, None))
                except Exception as exc:  # noqa: BLE001
                    body.after(0, lambda e=str(exc): _on_done(None, None, e))

            def _on_done(cert_p: str | None, key_p: str | None, err: str | None) -> None:
                if err:
                    ssl_status_var.set(f"Error: {err}")
                    ssl_status_lbl.configure(foreground="#ef4444")
                else:
                    self._ctrl_vars["ssl_certfile"].set(cert_p)
                    self._ctrl_vars["ssl_keyfile"].set(key_p)
                    parent_dir = Path(cert_p).parent
                    ssl_status_var.set(
                        f"\u2713 Saved to {parent_dir} \u2014 trusted in CurrentUser\\Root"
                    )
                    ssl_status_lbl.configure(foreground="#22c55e")

            threading.Thread(target=_worker, daemon=True).start()

        ttk.Button(
            ssl_lf,
            text="Generate & Trust (no admin needed)",
            command=_do_generate_ssl,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(3, 6))

        # Tunnel controls (dashboard-native, no separate settings screen required)
        tun_lf = ttk.LabelFrame(body, text="Cloudflare Tunnel settings")
        tun_lf.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 3))
        row += 1
        tun_lf.columnconfigure(1, weight=1)

        tun_cf_var = tk.StringVar(value=s.cloudflared_path)
        self._ctrl_vars["cloudflared_path"] = tun_cf_var
        ttk.Label(tun_lf, text="cloudflared binary").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(tun_lf, textvariable=tun_cf_var, width=36).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(
            tun_lf,
            text="…",
            command=lambda v=tun_cf_var: v.set(filedialog.askopenfilename(title="cloudflared binary") or v.get()),
            width=3,
        ).grid(row=0, column=2, padx=(0, 8), pady=3)

        tun_token_var = tk.StringVar(value=s.tunnel_api_token)
        self._ctrl_vars["tunnel_api_token"] = tun_token_var
        ttk.Label(tun_lf, text="Tunnel token").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        tun_token_entry = ttk.Entry(tun_lf, textvariable=tun_token_var, width=36, show="*")
        tun_token_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        tun_token_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            tun_lf,
            text="Show",
            variable=tun_token_show,
            command=lambda: tun_token_entry.config(show="" if tun_token_show.get() else "*"),
        ).grid(row=1, column=2, padx=(0, 8), pady=3)

        cf_api_var = tk.StringVar(value=s.cloudflare_api_key)
        self._ctrl_vars["cloudflare_api_key"] = cf_api_var
        ttk.Label(tun_lf, text="Cloudflare API token").grid(row=2, column=0, sticky="w", padx=8, pady=3)
        cf_api_entry = ttk.Entry(tun_lf, textvariable=cf_api_var, width=36, show="*")
        cf_api_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=3)
        cf_api_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            tun_lf,
            text="Show",
            variable=cf_api_show,
            command=lambda: cf_api_entry.config(show="" if cf_api_show.get() else "*"),
        ).grid(row=2, column=2, padx=(0, 8), pady=3)

        add_tun = [3]

        def _tun_text(label: str, key: str, value: str, *, hint: str = "") -> None:
            r = add_tun[0]
            var = tk.StringVar(value=value)
            self._ctrl_vars[key] = var
            ttk.Label(tun_lf, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(tun_lf, textvariable=var, width=36).grid(row=r, column=1, sticky="ew", padx=4, pady=3)
            if hint:
                ttk.Label(tun_lf, text=hint, foreground="#6b7280").grid(
                    row=r, column=2, sticky="w", padx=(0, 8), pady=3
                )
            add_tun[0] = r + 1

        _tun_text("Cloudflare account id", "cloudflare_account_id", s.cloudflare_account_id)
        _tun_text("Cloudflare zone id", "cloudflare_zone_id", s.cloudflare_zone_id)
        _tun_text("DNS hostname", "tunnel_dns_name", s.tunnel_dns_name, hint="e.g. mcp.example.com")
        _tun_text("Tunnel name", "tunnel_name", s.tunnel_name)

        ttk.Label(
            tun_lf,
            text=(
                "Auth mode priority: Tunnel token first. If blank, API token + account/zone + DNS name "
                "will auto-create a managed tunnel and update DNS."
            ),
            foreground="#6b7280",
            wraplength=520,
            justify=tk.LEFT,
        ).grid(row=add_tun[0], column=0, columnspan=3, sticky="w", padx=8, pady=(4, 6))

        add_text("IP allowlist CSV", "ip_allowlist_csv", s.ip_allowlist_csv)
        add_text("Tools include CSV", "tools_enable_csv", s.tools_enable_csv)
        add_text("Tools exclude CSV", "tools_exclude_csv", s.tools_exclude_csv)
        add_bool("Enable tier3", "enable_tier3", s.enable_tier3)
        add_bool("Disable tier2", "disable_tier2", s.disable_tier2)

        # Active profile selector + availability toggles
        ttk.Label(body, text="Active profile").grid(row=row, column=0, sticky="w", padx=8, pady=3)
        profile_var = tk.StringVar(value=s.selected_profile)
        self._ctrl_vars["selected_profile"] = profile_var
        active_values = _enabled_profiles(s)
        ttk.Combobox(body, textvariable=profile_var, values=active_values, state="readonly", width=18).grid(
            row=row, column=1, sticky="w", padx=4, pady=3
        )
        row += 1

        pf = ttk.LabelFrame(body, text="Profile availability")
        pf.grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 3))
        row += 1
        for i, p in enumerate(VALID_PROFILES):
            key = _PROFILE_ENABLE_FIELDS[p]
            var = tk.BooleanVar(value=bool(getattr(s, key)))
            self._ctrl_vars[key] = var
            ttk.Checkbutton(pf, text=p, variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=2)

        # Buttons
        actions = ttk.Frame(body)
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        ttk.Button(actions, text="Apply", command=lambda: self._apply_controls(restart=False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Apply + Restart Server", command=lambda: self._apply_controls(restart=True)).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            body,
            text="Tip: Apply updates launcher settings and next start command. Restart applies immediately.",
            foreground="#6b7280",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        body.columnconfigure(1, weight=1)

    def _apply_controls(self, *, restart: bool) -> None:
        vars_ = self._ctrl_vars
        s = self._app.settings

        s.server_config_path = vars_["server_config_path"].get().strip()
        s.transport = vars_["transport"].get().strip() or "streamable-http"
        s.server_host = vars_["server_host"].get().strip() or "127.0.0.1"
        try:
            s.server_port = int(vars_["server_port"].get())
        except (ValueError, tk.TclError):
            s.server_port = 8090
        s.auth_key = vars_["auth_key"].get()
        s.ssl_certfile = vars_["ssl_certfile"].get()
        s.ssl_keyfile = vars_["ssl_keyfile"].get()
        s.cloudflared_path = vars_["cloudflared_path"].get().strip()
        s.tunnel_api_token = vars_["tunnel_api_token"].get().strip()
        s.cloudflare_api_key = vars_["cloudflare_api_key"].get().strip()
        s.cloudflare_account_id = vars_["cloudflare_account_id"].get().strip()
        s.cloudflare_zone_id = vars_["cloudflare_zone_id"].get().strip()
        s.tunnel_dns_name = vars_["tunnel_dns_name"].get().strip()
        s.tunnel_name = vars_["tunnel_name"].get().strip() or "winremote-mcp"
        s.ip_allowlist_csv = vars_["ip_allowlist_csv"].get()
        s.tools_enable_csv = vars_["tools_enable_csv"].get()
        s.tools_exclude_csv = vars_["tools_exclude_csv"].get()
        s.enable_tier3 = bool(vars_["enable_tier3"].get())
        s.disable_tier2 = bool(vars_["disable_tier2"].get())
        s.selected_profile = vars_["selected_profile"].get()

        for p, field in _PROFILE_ENABLE_FIELDS.items():
            if field in vars_:
                setattr(s, field, bool(vars_[field].get()))

        # Normalize and enforce standard-user safety constraints.
        _enabled_profiles(s)
        _apply_non_admin_safe_defaults(s, is_admin=self._app._is_admin)
        save_launcher_settings(s)

        # Rebuild runtime managers
        self._app.server_manager.update_settings(self._app._make_server_settings())
        self._app.tunnel_manager.update_settings(self._app._make_tunnel_settings())
        self._app._rebuild_tray_menu()

        if restart:
            self._app.restart_server()
            messagebox.showinfo("winremote-mcp", "Controls applied and server restart requested.")
            return

        if self._app.server_manager.state == ServerState.RUNNING:
            messagebox.showinfo("winremote-mcp", "Controls applied. Restart server to apply active process changes.")
            return

        messagebox.showinfo("winremote-mcp", "Controls applied.")

    def _load_events(self) -> None:
        tree = self._events_tree
        tree.delete(*tree.get_children())
        evts = self._app.history.tail(200)
        for evt in reversed(evts):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(evt.timestamp))
            detail = " | ".join(f"{k}={v}" for k, v in evt.data.items())
            tree.insert("", "end", values=(ts, evt.event_type, detail))

    def _clear_events(self) -> None:
        if hasattr(self, "_events_tree"):
            self._events_tree.delete(*self._events_tree.get_children())

    def _refresh_dashboard(self) -> None:
        """Update status labels every 2 s."""
        if not (self._win and self._win.winfo_exists()):
            return
        app = self._app

        # Server
        srv = app.server_manager
        self._srv_state_var.set(srv.state.value)
        up = srv.uptime_seconds
        self._srv_uptime_var.set(_fmt_uptime(up))
        self._srv_profile_var.set(srv.settings.profile)
        self._srv_url_var.set(srv.base_url)
        self._srv_conflict_var.set(srv.port_conflict_summary)

        # Tunnel
        tun = app.tunnel_manager
        self._tun_state_var.set(tun.state.value)
        self._tun_url_var.set(tun.public_url or "–")
        self._tun_uptime_var.set(_fmt_uptime(tun.uptime_seconds))

        self._win.after(2000, self._refresh_dashboard)


# ---------------------------------------------------------------------------
# Log window
# ---------------------------------------------------------------------------

class LogWindow:
    """Scrolling text window showing captured server/tunnel stdout+stderr."""

    def __init__(self, root: tk.Tk, log_buffer: _LogBuffer) -> None:
        self._root = root
        self._log_buffer = log_buffer
        self._win: tk.Toplevel | None = None
        self._text: tk.Text | None = None
        self._last_count = 0

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Toplevel(self._root)
        win.title("winremote-mcp Logs")
        win.geometry("900x500")
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        self._win = win

        toolbar = ttk.Frame(win)
        toolbar.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(toolbar, text="Clear", command=self._clear).pack(side=tk.LEFT)

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        txt = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9), state=tk.DISABLED)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        txt.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        txt.tag_config("stderr", foreground="#ef4444")
        self._text = txt

        self._last_count = 0
        self._poll_logs()

    def _poll_logs(self) -> None:
        if not (self._win and self._win.winfo_exists()):
            return
        lines = self._log_buffer.get_all()
        if len(lines) != self._last_count:
            txt = self._text
            txt.configure(state=tk.NORMAL)
            txt.delete("1.0", tk.END)
            for line_text, is_stderr in lines:
                tag = "stderr" if is_stderr else ""
                txt.insert(tk.END, line_text + "\n", tag)
            txt.configure(state=tk.DISABLED)
            txt.see(tk.END)
            self._last_count = len(lines)
        self._win.after(500, self._poll_logs)

    def _clear(self) -> None:
        self._log_buffer.clear()
        if self._text:
            self._text.configure(state=tk.NORMAL)
            self._text.delete("1.0", tk.END)
            self._text.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog:
    """Modal settings dialog backed by LauncherSettings."""

    def __init__(self, root: tk.Tk, settings: LauncherSettings, on_save: Any, *, is_admin: bool) -> None:
        self._root = root
        self._settings = settings
        self._on_save = on_save
        self._is_admin = is_admin

    def show(self) -> None:
        dlg = tk.Toplevel(self._root)
        dlg.title("Launcher Settings")
        dlg.geometry("540x430")
        dlg.resizable(False, False)
        dlg.grab_set()

        nb = ttk.Notebook(dlg)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # --- Server tab ---
        sf = ttk.Frame(nb)
        nb.add(sf, text="  Server  ")
        vars_s = self._build_server_tab(sf)

        # --- General tab ---
        gf = ttk.Frame(nb)
        nb.add(gf, text="  General  ")
        vars_g = self._build_general_tab(gf)

        # Buttons
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        if not self._is_admin:
            ttk.Label(
                dlg,
                text="Running as standard user: admin-required options are constrained for compatibility.",
                foreground="#b45309",
                justify=tk.LEFT,
            ).pack(fill=tk.X, padx=10, pady=(0, 6))

        def _save():
            self._apply_server(vars_s)
            self._apply_general(vars_g)
            save_launcher_settings(self._settings)
            if self._on_save:
                self._on_save(self._settings)
            dlg.destroy()

        ttk.Button(btn_frame, text="Save", command=_save).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT)

    def _build_server_tab(self, parent: ttk.Frame) -> dict[str, tk.Variable]:
        s = self._settings
        _enabled_profiles(s)
        v: dict[str, tk.Variable] = {}

        _lbl_entry(parent, 0, "Config file (winremote.toml):", (v := {}) or v,
                   "config_path", s.server_config_path, browse_file=True)
        row = [1]

        def add(label: str, key: str, val: Any, is_bool: bool = False) -> None:
            if is_bool:
                var = tk.BooleanVar(value=bool(val))
            else:
                var = tk.StringVar(value=str(val))
            v[key] = var
            ttk.Label(parent, text=label).grid(row=row[0], column=0, sticky="w", padx=8, pady=3)
            if is_bool:
                cb = ttk.Checkbutton(parent, variable=var)
                cb.grid(row=row[0], column=1, sticky="w", padx=4)
                if key == "enable_tier3" and not self._is_admin:
                    cb.configure(state=tk.DISABLED)
            else:
                ttk.Entry(parent, textvariable=var, width=30).grid(row=row[0], column=1, sticky="ew", padx=4)
            row[0] += 1

        # Profile combobox
        prof_var = tk.StringVar(value=s.selected_profile)
        v["selected_profile"] = prof_var
        ttk.Label(parent, text="Profile:").grid(row=row[0], column=0, sticky="w", padx=8, pady=3)
        cb = ttk.Combobox(parent, textvariable=prof_var, values=list(VALID_PROFILES), state="readonly", width=15)
        cb.grid(row=row[0], column=1, sticky="w", padx=4)
        row[0] += 1

        add("Host:", "server_host", s.server_host)
        add("Port:", "server_port", s.server_port)
        add("Auth key:", "auth_key", s.auth_key)
        add("SSL certfile:", "ssl_certfile", s.ssl_certfile)
        add("SSL keyfile:", "ssl_keyfile", s.ssl_keyfile)
        add("Transport (stdio/streamable-http):", "transport", s.transport)
        add("IP allowlist CSV:", "ip_allowlist_csv", s.ip_allowlist_csv)
        add("Tools include CSV:", "tools_enable_csv", s.tools_enable_csv)
        add("Tools exclude CSV:", "tools_exclude_csv", s.tools_exclude_csv)
        add("Enable tier3 tools:", "enable_tier3", s.enable_tier3, is_bool=True)
        add("Disable tier2 tools:", "disable_tier2", s.disable_tier2, is_bool=True)
        add("Enforce non-admin safe mode:", "enforce_non_admin_safe_mode", s.enforce_non_admin_safe_mode, is_bool=True)
        add("Auto-start on launcher open:", "auto_start_server", s.auto_start_server, is_bool=True)

        # Profile activation/deactivation controls
        pf = ttk.LabelFrame(parent, text="Profile availability")
        pf.grid(row=row[0], column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 2))
        row[0] += 1

        for i, p in enumerate(VALID_PROFILES):
            key = _PROFILE_ENABLE_FIELDS[p]
            var = tk.BooleanVar(value=bool(getattr(s, key)))
            v[key] = var
            ttk.Checkbutton(pf, text=p, variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=2)

        parent.columnconfigure(1, weight=1)
        return v

    def _build_general_tab(self, parent: ttk.Frame) -> dict[str, tk.Variable]:
        s = self._settings
        v: dict[str, tk.Variable] = {}

        pol_var = tk.DoubleVar(value=s.poll_interval)
        v["poll_interval"] = pol_var
        ttk.Label(parent, text="Health poll interval (sec):").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Spinbox(parent, from_=1, to=60, textvariable=pol_var, width=8).grid(row=0, column=1, sticky="w", padx=4)

        ret_var = tk.IntVar(value=s.history_retention_days)
        v["history_retention_days"] = ret_var
        ttk.Label(parent, text="History retention (days):").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Spinbox(parent, from_=1, to=365, textvariable=ret_var, width=8).grid(row=1, column=1, sticky="w", padx=4)

        log_var = tk.IntVar(value=s.log_max_lines)
        v["log_max_lines"] = log_var
        ttk.Label(parent, text="Log buffer max lines:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Spinbox(parent, from_=200, to=10000, textvariable=log_var, width=8).grid(row=2, column=1, sticky="w", padx=4)

        parent.columnconfigure(1, weight=1)
        return v

    def _apply_server(self, v: dict[str, tk.Variable]) -> None:
        s = self._settings
        s.selected_profile = v["selected_profile"].get()
        s.server_host = v["server_host"].get()
        try:
            s.server_port = int(v["server_port"].get())
        except ValueError:
            pass
        s.auth_key = v["auth_key"].get()
        s.ssl_certfile = v["ssl_certfile"].get()
        s.ssl_keyfile = v["ssl_keyfile"].get()
        s.transport = v["transport"].get().strip() or "streamable-http"
        s.ip_allowlist_csv = v["ip_allowlist_csv"].get()
        s.tools_enable_csv = v["tools_enable_csv"].get()
        s.tools_exclude_csv = v["tools_exclude_csv"].get()
        requested_tier3 = bool(v["enable_tier3"].get())
        s.enable_tier3 = requested_tier3 if self._is_admin else False
        s.disable_tier2 = bool(v["disable_tier2"].get())
        s.enforce_non_admin_safe_mode = bool(v["enforce_non_admin_safe_mode"].get())
        s.auto_start_server = bool(v["auto_start_server"].get())

        for p, field_name in _PROFILE_ENABLE_FIELDS.items():
            if field_name in v:
                setattr(s, field_name, bool(v[field_name].get()))

        # Normalize after profile availability changes.
        _enabled_profiles(s)

        if "config_path" in v:
            s.server_config_path = v["config_path"].get()

    def _apply_general(self, v: dict[str, tk.Variable]) -> None:
        s = self._settings
        try:
            s.poll_interval = float(v["poll_interval"].get())
        except (ValueError, tk.TclError):
            pass
        try:
            s.history_retention_days = int(v["history_retention_days"].get())
        except (ValueError, tk.TclError):
            pass
        try:
            s.log_max_lines = int(v["log_max_lines"].get())
        except (ValueError, tk.TclError):
            pass


# ---------------------------------------------------------------------------
# Main tray application
# ---------------------------------------------------------------------------

class TrayApp:
    """Coordinates server/tunnel managers, history, and the tray UI."""

    def __init__(self) -> None:
        self.settings = load_launcher_settings()
        _enabled_profiles(self.settings)
        self._is_admin = _is_user_admin()
        changes = _apply_non_admin_safe_defaults(self.settings, is_admin=self._is_admin)
        if changes:
            save_launcher_settings(self.settings)

        self.history = HistoryStore()
        self._log_buffer = _LogBuffer(max_lines=self.settings.log_max_lines)

        # Build managers from current settings
        self.server_manager = ServerManager(
            settings=self._make_server_settings(),
            on_state_change=self._on_server_state,
            on_log_line=self._on_server_log,
            poll_interval=self.settings.poll_interval,
        )
        self.tunnel_manager = TunnelManager(
            settings=self._make_tunnel_settings(),
            on_state_change=self._on_tunnel_state,
            on_log_line=self._on_tunnel_log,
        )

        # tkinter root (hidden; only Toplevels are shown)
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("winremote-mcp Launcher")

        # Cross-thread UI action queue
        self._ui_queue: queue.Queue[tuple[str, ...]] = queue.Queue()

        # Lazy child windows
        self._dashboard = DashboardWindow(self._root, self)
        self._log_win = LogWindow(self._root, self._log_buffer)
        self._tray_icon: Any = None
        self._startup_setting_changes = changes

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the tray icon and enter the tkinter main loop."""
        if not _HAS_PYSTRAY:
            messagebox.showerror(
                "Missing dependency",
                "pystray is not installed.\n\nRun: pip install pystray",
            )
            return

        self.history.append(HistoryEvent(EventType.LAUNCHER_START))
        self.history.append(
            HistoryEvent(
                EventType.TOOL_CALL,
                data={
                    "launcher_mode": "admin" if self._is_admin else "standard-user",
                    "non_admin_safe_mode": str(self.settings.enforce_non_admin_safe_mode),
                },
            )
        )

        if self._startup_setting_changes:
            self._ui_queue.put((
                "notify",
                "Applied non-admin safety defaults:\n- " + "\n- ".join(self._startup_setting_changes),
            ))

        if self.settings.auto_start_server:
            self.start_server()

        self._start_tray()
        self._root.after(100, self._drain_ui_queue)
        self._root.mainloop()

    def quit(self) -> None:
        """Stop everything and exit."""
        if self.server_manager.state not in (ServerState.STOPPED,):
            self.server_manager.stop()
        if self.tunnel_manager.state not in (TunnelState.STOPPED,):
            self.tunnel_manager.stop()
        self.history.append(HistoryEvent(EventType.LAUNCHER_STOP))
        if self._tray_icon:
            self._tray_icon.stop()
        self._root.after(0, self._root.destroy)

    # ------------------------------------------------------------------
    # Server actions (safe to call from any thread via ui_queue or directly)
    # ------------------------------------------------------------------

    def start_server(self) -> None:
        if self.server_manager.start():
            self.history.append(HistoryEvent(
                EventType.SERVER_START,
                data={"profile": self.settings.selected_profile,
                      "host": self.settings.server_host,
                      "port": str(self.settings.server_port)},
            ))
            return

        if self.server_manager.has_port_conflict:
            summary = self.server_manager.port_conflict_summary
            self._ui_queue.put((
                "notify",
                "Could not start server: configured port is already in use by "
                f"{summary}.\n\n"
                "Use 'Stop Port Occupant' in Dashboard/Tray, then start again.",
            ))
            self._rebuild_tray_menu()
            return

        self._ui_queue.put(("notify", "Server is already running or in transition."))

    def stop_server(self) -> None:
        threading.Thread(target=self.server_manager.stop, daemon=True).start()

    def restart_server(self) -> None:
        threading.Thread(target=self._do_restart_server, daemon=True).start()

    def _do_restart_server(self) -> None:
        self.history.append(HistoryEvent(EventType.SERVER_RESTART))
        self.server_manager.restart()

    def stop_conflicting_server(self) -> None:
        """Stop the process that currently occupies the configured port (if tracked)."""
        def _worker() -> None:
            ok, msg = self.server_manager.stop_conflicting_process()
            self._ui_queue.put(("notify", msg))
            if ok:
                self.history.append(HistoryEvent(
                    EventType.TOOL_CALL,
                    data={
                        "action": "stop_port_occupant",
                        "port": str(self.settings.server_port),
                    },
                ))
            self._rebuild_tray_menu()

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Tunnel actions
    # ------------------------------------------------------------------

    def start_tunnel(self) -> None:
        if not _preflight_tunnel_check(self.settings, self.server_manager):
            return
        if not self.tunnel_manager.start():
            state = self.tunnel_manager.state.value
            self._ui_queue.put(("notify", f"Could not start tunnel (state: {state}). Stop it first if it is running."))
        else:
            self.history.append(HistoryEvent(EventType.TUNNEL_START))

    def stop_tunnel(self) -> None:
        threading.Thread(target=self.tunnel_manager.stop, daemon=True).start()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def open_settings(self) -> None:
        self._ui_queue.put(("open_settings",))

    def _show_settings_dialog(self) -> None:
        def on_save(new_settings: LauncherSettings) -> None:
            self.settings = new_settings
            changes = _apply_non_admin_safe_defaults(self.settings, is_admin=self._is_admin)
            if changes:
                self._ui_queue.put((
                    "notify",
                    "Adjusted for non-admin compatibility:\n- " + "\n- ".join(changes),
                ))
            self._log_buffer._max = new_settings.log_max_lines
            # Rebuild managers with new settings
            self.server_manager.update_settings(self._make_server_settings())
            self.tunnel_manager.update_settings(self._make_tunnel_settings())
            self._rebuild_tray_menu()

        SettingsDialog(self._root, self.settings, on_save=on_save, is_admin=self._is_admin).show()

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    def _make_server_settings(self) -> ServerSettings:
        s = self.settings
        _enabled_profiles(s)
        return ServerSettings(
            config_path=Path(s.server_config_path) if s.server_config_path else None,
            profile=s.selected_profile,
            host=s.server_host,
            port=s.server_port,
            auth_key=s.auth_key or None,
            ssl_certfile=s.ssl_certfile or None,
            ssl_keyfile=s.ssl_keyfile or None,
            transport=(s.transport or "streamable-http"),
            enable_tier3=s.enable_tier3,
            disable_tier2=s.disable_tier2,
            ip_allowlist=_parse_csv(s.ip_allowlist_csv),
            selected_tools=_parse_csv(s.tools_enable_csv),
            excluded_tools=_parse_csv(s.tools_exclude_csv),
        )

    def _make_tunnel_settings(self) -> TunnelSettings:
        s = self.settings
        target = s.tunnel_target_url or f"http://{s.server_host}:{s.server_port}"
        return TunnelSettings(
            cloudflared_path=s.cloudflared_path or None,
            api_token=s.tunnel_api_token or None,
            cloudflare_api_key=s.cloudflare_api_key or None,
            cloudflare_account_id=s.cloudflare_account_id or None,
            cloudflare_zone_id=s.cloudflare_zone_id or None,
            tunnel_dns_name=s.tunnel_dns_name or None,
            tunnel_name=s.tunnel_name or "winremote-mcp",
            target_url=target,
        )

    # ------------------------------------------------------------------
    # Callbacks from manager threads → queued to main thread
    # ------------------------------------------------------------------

    def _on_server_state(self, state: ServerState, reason: str) -> None:
        self._ui_queue.put(("server_state", state.value, reason))

    def _on_server_log(self, line: str, is_stderr: bool) -> None:
        self._log_buffer.append(line, is_stderr)

    def _on_tunnel_state(self, state: TunnelState, reason: str) -> None:
        self._ui_queue.put(("tunnel_state", state.value, reason))

    def _on_tunnel_log(self, line: str, is_stderr: bool) -> None:
        self._log_buffer.append(f"[tunnel] {line}", is_stderr)

    # ------------------------------------------------------------------
    # UI queue drainer (runs on main thread via root.after)
    # ------------------------------------------------------------------

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                action, *args = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            if action == "server_state":
                state_str, reason = args
                state = ServerState(state_str)
                self._on_server_state_main(state, reason)
            elif action == "tunnel_state":
                state_str, reason = args
                state = TunnelState(state_str)
                self._on_tunnel_state_main(state, reason)
            elif action == "notify":
                messagebox.showinfo("winremote-mcp", args[0])
            elif action == "open_settings":
                self._show_settings_dialog()
            elif action == "show_dashboard":
                self._dashboard.show()
            elif action == "show_logs":
                self._log_win.show()
            elif action == "quit":
                self.quit()
                return

        self._root.after(100, self._drain_ui_queue)

    def _on_server_state_main(self, state: ServerState, reason: str) -> None:
        """Handle server state change on the main thread."""
        type_map = {
            ServerState.RUNNING: EventType.SERVER_HEALTHY,
            ServerState.ERROR: EventType.SERVER_ERROR,
            ServerState.DEGRADED: EventType.SERVER_DEGRADED,
            ServerState.STOPPED: EventType.SERVER_STOP,
        }
        if state in type_map:
            self.history.append(HistoryEvent(type_map[state], data={"reason": reason}))
        self._rebuild_tray_menu()
        self._update_tray_icon()

    def _on_tunnel_state_main(self, state: TunnelState, reason: str) -> None:
        """Handle tunnel state change on the main thread."""
        type_map = {
            TunnelState.CONNECTED: EventType.TUNNEL_START,
            TunnelState.ERROR: EventType.TUNNEL_ERROR,
            TunnelState.STOPPED: EventType.TUNNEL_STOP,
        }
        if state in type_map:
            evt_data: dict[str, Any] = {"reason": reason}
            if state == TunnelState.CONNECTED and self.tunnel_manager.public_url:
                evt_data["url"] = self.tunnel_manager.public_url
            self.history.append(HistoryEvent(type_map[state], data=evt_data))
        self._rebuild_tray_menu()
        self._update_tray_icon()

    # ------------------------------------------------------------------
    # Tray icon management
    # ------------------------------------------------------------------

    def _start_tray(self) -> None:
        icon_img = _make_icon(self.server_manager.state, self.tunnel_manager.state)
        menu = self._build_tray_menu()
        self._tray_icon = pystray.Icon(
            "winremote-mcp",
            icon_img,
            "winremote-mcp",
            menu,
        )
        t = threading.Thread(target=self._tray_icon.run, daemon=True)
        t.start()

    def _update_tray_icon(self) -> None:
        if self._tray_icon is None:
            return
        self._tray_icon.icon = _make_icon(
            self.server_manager.state, self.tunnel_manager.state
        )
        srv_state = self.server_manager.state.value
        tun_state = self.tunnel_manager.state.value
        self._tray_icon.title = f"winremote-mcp  server:{srv_state}  tunnel:{tun_state}"

    def _rebuild_tray_menu(self) -> None:
        if self._tray_icon is None:
            return
        self._tray_icon.menu = self._build_tray_menu()
        self._tray_icon.update_menu()

    def _build_tray_menu(self) -> TrayMenu:
        srv = self.server_manager
        tun = self.tunnel_manager
        enabled_profiles = _enabled_profiles(self.settings)

        server_running = srv.state == ServerState.RUNNING
        server_stopped = srv.state in (ServerState.STOPPED, ServerState.ERROR)
        tunnel_running = tun.state == TunnelState.CONNECTED
        tunnel_stopped = tun.state in (TunnelState.STOPPED, TunnelState.ERROR)

        # Profile submenu
        profile_items = [
            TrayItem(
                f"{'→ ' if self.settings.selected_profile == p else '   '}{p}",
                self._make_profile_action(p),
                enabled=self.settings.selected_profile != p,
            )
            for p in enabled_profiles
        ]

        # Profile availability toggles
        availability_items = [
            TrayItem(
                f"[{'x' if p in enabled_profiles else ' '}] {p}",
                self._make_profile_toggle_action(p),
            )
            for p in VALID_PROFILES
        ]

        # Server submenu
        server_items = [
            TrayItem("Start", lambda _: self.start_server(), enabled=server_stopped),
            TrayItem("Stop", lambda _: self.stop_server(), enabled=not server_stopped),
            TrayItem("Restart", lambda _: self.restart_server(), enabled=not server_stopped),
            TrayItem(
                f"Stop Port Occupant ({srv.port_conflict_summary})",
                lambda _: self.stop_conflicting_server(),
                enabled=srv.has_port_conflict,
            ),
            TrayMenu.SEPARATOR,
            TrayItem("Active Profile", TrayMenu(*profile_items)),
            TrayItem("Profile Availability", TrayMenu(*availability_items)),
        ]

        # Tunnel submenu
        tunnel_items = [
            TrayItem("Start Tunnel", lambda _: self.start_tunnel(), enabled=tunnel_stopped),
            TrayItem("Stop Tunnel", lambda _: self.stop_tunnel(), enabled=not tunnel_stopped),
        ]
        if tun.public_url:
            tunnel_items.append(TrayMenu.SEPARATOR)
            tunnel_items.append(TrayItem(f"URL: {tun.public_url}", None, enabled=False))

        return TrayMenu(
            TrayItem("winremote-mcp", None, enabled=False),
            TrayItem(
                f"Mode: {'Admin' if self._is_admin else 'Standard User'}",
                None,
                enabled=False,
            ),
            TrayMenu.SEPARATOR,
            TrayItem("Server", TrayMenu(*server_items)),
            TrayItem("Tunnel", TrayMenu(*tunnel_items)),
            TrayMenu.SEPARATOR,
            TrayItem("Dashboard", lambda _: self._ui_queue.put(("show_dashboard",))),
            TrayItem("View Logs", lambda _: self._ui_queue.put(("show_logs",))),
            TrayMenu.SEPARATOR,
            TrayItem("Quit", lambda _: self._ui_queue.put(("quit",))),
        )

    def _make_profile_action(self, profile: str):
        def _action(_icon, _item):
            if profile not in _enabled_profiles(self.settings):
                self._ui_queue.put(("notify", f"Profile '{profile}' is currently disabled."))
                return
            if self.settings.selected_profile == profile:
                return
            old = self.settings.selected_profile
            self.settings.selected_profile = profile
            save_launcher_settings(self.settings)
            self.history.append(HistoryEvent(
                EventType.SERVER_PROFILE_CHANGE,
                data={"from": old, "to": profile},
            ))
            self.server_manager.update_settings(self._make_server_settings())
            self._rebuild_tray_menu()
            # If server is running, offer restart
            if self.server_manager.state == ServerState.RUNNING:
                self._ui_queue.put((
                    "notify",
                    f"Profile changed to '{profile}'.\n"
                    "Restart the server for the new profile to take effect.",
                ))
        return _action

    def _make_profile_toggle_action(self, profile: str):
        def _action(_icon, _item):
            enabled_now = _enabled_profiles(self.settings)
            target_enabled = profile not in enabled_now
            changed, msg = _set_profile_enabled(self.settings, profile, target_enabled)
            if not changed:
                self._ui_queue.put(("notify", msg))
                return

            # If current profile became disabled, normalize selected profile.
            prev_selected = self.server_manager.settings.profile
            enabled_after = _enabled_profiles(self.settings)
            if self.settings.selected_profile not in enabled_after:
                self.settings.selected_profile = enabled_after[0]

            save_launcher_settings(self.settings)
            self.server_manager.update_settings(self._make_server_settings())
            self._rebuild_tray_menu()

            # If server is running and effective profile changed, suggest restart.
            if self.server_manager.state == ServerState.RUNNING and prev_selected != self.settings.selected_profile:
                self._ui_queue.put((
                    "notify",
                    f"{msg}. Active profile switched to '{self.settings.selected_profile}'.\n"
                    "Restart the server for changes to take effect.",
                ))
                return

            if self.server_manager.state == ServerState.RUNNING:
                self._ui_queue.put((
                    "notify",
                    f"{msg}. Restart the server to apply profile availability changes.",
                ))
                return

            self._ui_queue.put(("notify", msg))

        return _action


# ---------------------------------------------------------------------------
# Preflight sanity check before enabling the tunnel
# ---------------------------------------------------------------------------

def _preflight_tunnel_check(
    settings: LauncherSettings, server_manager: ServerManager
) -> bool:
    """Return True if it's safe to start the tunnel; show a warning otherwise."""
    issues: list[str] = []

    if server_manager.state != ServerState.RUNNING:
        issues.append("• The MCP server is not currently running.")

    if not settings.auth_key:
        issues.append(
            "• No auth key is configured. Anyone who discovers the tunnel URL\n"
            "  can access your desktop. Set an auth key in Settings first."
        )

    if settings.server_host not in ("127.0.0.1", "localhost", "::1"):
        issues.append(
            f"• Server is bound to {settings.server_host}, not localhost.\n"
            "  Verify this is intentional before exposing via a public tunnel."
        )

    if issues:
        proceed = messagebox.askyesno(
            "Tunnel Security Warning",
            "The following issues were found before starting the tunnel:\n\n"
            + "\n".join(issues)
            + "\n\nStart the tunnel anyway?",
            icon="warning",
        )
        return proceed
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _grid_row(parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=2)
    ttk.Label(parent, textvariable=var, foreground="#1d4ed8").grid(
        row=row, column=1, sticky="w", padx=4, pady=2
    )
    parent.columnconfigure(1, weight=1)


def _lbl_entry(
    parent: ttk.Frame,
    row: int,
    label: str,
    v: dict[str, tk.Variable],
    key: str,
    default: str,
    *,
    browse_file: bool = False,
    parent_win: tk.Widget | None = None,
) -> None:
    var = tk.StringVar(value=default)
    v[key] = var
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
    ttk.Entry(parent, textvariable=var, width=30).grid(row=row, column=1, sticky="ew", padx=4)
    if browse_file:
        ttk.Button(
            parent,
            text="Browse…",
            command=lambda: var.set(filedialog.askopenfilename(title=label) or var.get()),
        ).grid(row=row, column=2, padx=4)
