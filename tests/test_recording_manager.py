import base64
from pathlib import Path

import pytest

import winremote.recording as recording


@pytest.fixture
def fake_recordings_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recording, "_recordings_root", lambda: tmp_path)
    return tmp_path


def test_start_recording_creates_handle():
    handle = recording.start_recording(target="monitor", fps=7, max_duration_seconds=20)
    assert handle.recording_id.startswith("rec_")
    assert handle.fps == 7
    assert handle.max_duration_seconds == 20


def test_stop_recording_writes_manifest_and_artifact(fake_recordings_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recording, "record_screen", lambda **_: base64.b64encode(b"GIF89a").decode())

    handle = recording.start_recording(target="region", left=1, top=2, right=30, bottom=40, fps=5)
    result = recording.stop_recording(handle.recording_id, save_format="mp4")

    output = Path(result.output_path)
    manifest = Path(result.manifest_path)

    assert output.exists()
    assert output.suffix == ".gif"
    assert manifest.exists()
    assert result.success is True
    assert result.note is not None

    loaded_manifest = recording.get_recording_manifest(handle.recording_id, root_dir=fake_recordings_root)
    assert loaded_manifest["recording_id"] == handle.recording_id

    listed = recording.list_recordings(root_dir=fake_recordings_root)
    assert any(item.get("recording_id") == handle.recording_id for item in listed)


def test_stop_recording_missing_id_errors():
    with pytest.raises(ValueError):
        recording.stop_recording("does-not-exist")
