import json
from pathlib import Path

import pytest
from PIL import Image

import winremote.recording as recording


@pytest.fixture
def fake_recordings_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recording, "_recordings_root", lambda: tmp_path)
    return tmp_path


def _write_sample_gif(path: Path):
    frame1 = Image.new("RGB", (32, 24), color="red")
    frame2 = Image.new("RGB", (32, 24), color="blue")
    frame1.save(path, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)


def test_analyze_recording_extracts_keyframes_and_writes_report(
    fake_recordings_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    rec_id = "rec_test"
    rec_dir = fake_recordings_root / rec_id
    rec_dir.mkdir(parents=True, exist_ok=True)

    gif_path = rec_dir / "recording.gif"
    _write_sample_gif(gif_path)

    manifest = {
        "recording_id": rec_id,
        "target": "window",
        "started_at": "2026-04-28T12:01:02+00:00",
        "ended_at": "2026-04-28T12:01:04+00:00",
        "duration_seconds": 2.0,
        "fps": 5,
        "result": {"output_path": str(gif_path)},
    }
    (rec_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rec_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-04-28T12:01:03+00:00", "type": "click", "summary": "Clicked Run"}),
                json.dumps({"timestamp": "2026-04-28T12:01:04+00:00", "type": "dialog", "summary": "Error: Build failed"}),
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(recording, "_ocr_image_file", lambda path: "Build failed: exception raised")

    result = recording.analyze_recording(rec_id, include_ocr=True, include_event_timeline=True, root_dir=fake_recordings_root)

    assert result["recording_id"] == rec_id
    assert result["keyframes_extracted"] >= 1
    assert len(result["timeline"]) == 2
    assert len(result["likely_errors"]) >= 1
    assert Path(result["analysis_path"]).exists()


def test_analyze_recording_handles_missing_events(fake_recordings_root: Path):
    rec_id = "rec_no_events"
    rec_dir = fake_recordings_root / rec_id
    rec_dir.mkdir(parents=True, exist_ok=True)

    gif_path = rec_dir / "recording.gif"
    _write_sample_gif(gif_path)

    manifest = {
        "recording_id": rec_id,
        "target": "monitor",
        "started_at": "2026-04-28T12:01:02+00:00",
        "ended_at": "2026-04-28T12:01:04+00:00",
        "duration_seconds": 2.0,
        "fps": 5,
        "result": {"output_path": str(gif_path)},
    }
    (rec_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = recording.analyze_recording(rec_id, include_event_timeline=True, root_dir=fake_recordings_root)
    assert result["event_count"] == 0
    assert result["analysis_path"].endswith("analysis.md")
