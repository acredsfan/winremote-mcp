"""Portable FFmpeg helper utilities for user-space installs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.request import urlopen
import zipfile
import io


def get_localappdata_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP"
    return Path.home() / ".local" / "share" / "WinRemoteMCP"


def get_ffmpeg_path(*, root_dir: Path | None = None) -> Path:
    root = root_dir or get_localappdata_root()
    return root / "bin" / "ffmpeg.exe"


def ffmpeg_available(*, root_dir: Path | None = None) -> bool:
    """Return True when ffmpeg is available either portable or in PATH."""
    portable = get_ffmpeg_path(root_dir=root_dir)
    if portable.exists():
        return True
    return shutil.which("ffmpeg") is not None


def ensure_ffmpeg(
    *,
    root_dir: Path | None = None,
    download: bool = True,
    archive_url: str = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
) -> Path:
    """Ensure portable ffmpeg exists in %LOCALAPPDATA%/WinRemoteMCP/bin.

    If `download` is False and ffmpeg is missing, FileNotFoundError is raised.
    """
    target = get_ffmpeg_path(root_dir=root_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        return target

    if not download:
        raise FileNotFoundError(f"Portable ffmpeg not found: {target}")

    with urlopen(archive_url, timeout=60) as response:
        data = response.read()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        ffmpeg_members = [name for name in zf.namelist() if name.endswith("/bin/ffmpeg.exe")]
        if not ffmpeg_members:
            raise RuntimeError("Downloaded FFmpeg archive did not contain bin/ffmpeg.exe")
        member = ffmpeg_members[0]
        with zf.open(member) as src, target.open("wb") as dst:
            dst.write(src.read())

    return target
