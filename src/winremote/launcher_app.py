"""Entry point for the winremote-mcp tray launcher.

Run with:
    python -m winremote.launcher_app
or (after installing):
    winremote-tray
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    # Guard: pystray only works on Windows (and limited macOS/Linux support)
    # but our server is Windows-only anyway.
    if sys.platform != "win32":
        print("winremote-tray currently only supports Windows.", file=sys.stderr)
        sys.exit(1)

    try:
        from .launcher_ui import TrayApp
    except ImportError as exc:
        print(f"Failed to import launcher UI: {exc}", file=sys.stderr)
        print("Install GUI dependencies with: pip install winremote-mcp[gui]", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--startup", action="store_true", help="Indicates launcher was started by Windows startup")
    args, _ = parser.parse_known_args()

    app = TrayApp()
    app.set_startup_mode(args.startup)
    app.run()


if __name__ == "__main__":
    main()
