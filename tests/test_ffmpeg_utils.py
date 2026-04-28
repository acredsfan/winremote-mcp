from pathlib import Path

import pytest

from winremote.ffmpeg_utils import ensure_ffmpeg, ffmpeg_available, get_ffmpeg_path


def test_get_ffmpeg_path_uses_root_override(tmp_path: Path):
    path = get_ffmpeg_path(root_dir=tmp_path)
    assert path == tmp_path / "bin" / "ffmpeg.exe"


def test_ffmpeg_available_true_when_portable_exists(tmp_path: Path):
    ffmpeg = tmp_path / "bin" / "ffmpeg.exe"
    ffmpeg.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.write_bytes(b"fake")

    assert ffmpeg_available(root_dir=tmp_path) is True


def test_ensure_ffmpeg_raises_when_missing_and_download_disabled(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ensure_ffmpeg(root_dir=tmp_path, download=False)


def test_ensure_ffmpeg_returns_existing_portable(tmp_path: Path):
    ffmpeg = tmp_path / "bin" / "ffmpeg.exe"
    ffmpeg.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.write_bytes(b"existing")

    returned = ensure_ffmpeg(root_dir=tmp_path, download=False)
    assert returned == ffmpeg
    assert returned.read_bytes() == b"existing"
