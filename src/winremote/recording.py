"""Screen recording — capture frames and produce an animated GIF."""

from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from PIL import Image

from winremote import desktop


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _recordings_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "recordings"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "recordings"


@dataclass
class RecordingHandle:
    recording_id: str
    target: str
    started_at: str
    max_duration_seconds: float
    left: int | None = None
    top: int | None = None
    right: int | None = None
    bottom: int | None = None
    fps: int = 5
    max_width: int = 800
    redact_secrets: bool = True


@dataclass
class RecordingResult:
    recording_id: str
    success: bool
    started_at: str
    ended_at: str
    duration_seconds: float
    output_path: str
    output_format: str
    manifest_path: str
    note: str | None = None


_ACTIVE_RECORDINGS: dict[str, RecordingHandle] = {}


def list_active_recordings() -> list[dict[str, object]]:
    """Return active in-memory recording handles."""
    return [asdict(handle) for handle in _ACTIVE_RECORDINGS.values()]


def has_active_recordings() -> bool:
    """Return whether there are active recording sessions."""
    return bool(_ACTIVE_RECORDINGS)


def start_recording(
    *,
    target: str = "monitor",
    left: int | None = None,
    top: int | None = None,
    right: int | None = None,
    bottom: int | None = None,
    fps: int = 5,
    max_width: int = 800,
    max_duration_seconds: float = 30.0,
    redact_secrets: bool = True,
) -> RecordingHandle:
    """Create an active recording handle.

    This phase introduces stateful start/stop lifecycle. Capture backend remains
    GIF-based for now and is executed when `stop_recording` is called.
    """
    rec_id = f"rec_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    handle = RecordingHandle(
        recording_id=rec_id,
        target=target,
        started_at=_utc_now_iso(),
        max_duration_seconds=max(1.0, float(max_duration_seconds)),
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        fps=max(1, min(int(fps), 10)),
        max_width=max(100, int(max_width)),
        redact_secrets=bool(redact_secrets),
    )
    _ACTIVE_RECORDINGS[rec_id] = handle
    return handle


def stop_recording(recording_id: str, *, save_format: str = "mp4") -> RecordingResult:
    """Stop an active recording and persist artifact + manifest.

    Current implementation captures elapsed duration as GIF. If `save_format`
    requests non-GIF, this function returns GIF with a note for compatibility.
    """
    handle = _ACTIVE_RECORDINGS.pop(recording_id, None)
    if handle is None:
        raise ValueError(f"Recording not found: {recording_id}")

    started_dt = datetime.fromisoformat(handle.started_at)
    elapsed = (datetime.now(tz=timezone.utc) - started_dt).total_seconds()
    duration = min(max(elapsed, 0.5), handle.max_duration_seconds)

    region = {}
    if (
        handle.left is not None
        and handle.top is not None
        and handle.right is not None
        and handle.bottom is not None
    ):
        region = {
            "left": handle.left,
            "top": handle.top,
            "right": handle.right,
            "bottom": handle.bottom,
        }

    gif_b64 = record_screen(duration=duration, fps=handle.fps, max_width=handle.max_width, **region)
    gif_bytes = base64.b64decode(gif_b64)

    recording_dir = _recordings_root() / recording_id
    recording_dir.mkdir(parents=True, exist_ok=True)

    requested_format = (save_format or "gif").strip().lower()
    output_format = "gif"
    note = None
    if requested_format not in {"gif", "frames", "webm", "mp4"}:
        note = f"Unsupported save_format '{save_format}', saved as gif"
    elif requested_format != "gif":
        note = f"save_format='{save_format}' requested; GIF capture backend used in this phase"

    output_path = recording_dir / f"recording.{output_format}"
    output_path.write_bytes(gif_bytes)

    ended_at = _utc_now_iso()
    result = RecordingResult(
        recording_id=recording_id,
        success=True,
        started_at=handle.started_at,
        ended_at=ended_at,
        duration_seconds=duration,
        output_path=str(output_path),
        output_format=output_format,
        manifest_path=str(recording_dir / "manifest.json"),
        note=note,
    )

    manifest = {
        "recording_id": recording_id,
        "target": handle.target,
        "started_at": handle.started_at,
        "ended_at": ended_at,
        "duration_seconds": duration,
        "fps": handle.fps,
        "max_width": handle.max_width,
        "redact_secrets": handle.redact_secrets,
        "region": {
            "left": handle.left,
            "top": handle.top,
            "right": handle.right,
            "bottom": handle.bottom,
        },
        "result": asdict(result),
    }
    (recording_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return result


def list_recordings(*, root_dir: Path | None = None) -> list[dict[str, object]]:
    """List recordings from the recording root."""
    root = root_dir or _recordings_root()
    if not root.exists():
        return []

    out: list[dict[str, object]] = []
    for rec_dir in sorted([p for p in root.iterdir() if p.is_dir()], reverse=True):
        manifest_path = rec_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                out.append(manifest)
            except Exception:
                out.append({"recording_id": rec_dir.name, "manifest_error": True})
        else:
            out.append({"recording_id": rec_dir.name, "manifest_missing": True})
    return out


def get_recording_manifest(recording_id: str, *, root_dir: Path | None = None) -> dict[str, object]:
    """Read a recording manifest by recording id."""
    root = root_dir or _recordings_root()
    manifest_path = root / recording_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Recording manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _recording_dir(recording_id: str, *, root_dir: Path | None = None) -> Path:
    root = root_dir or _recordings_root()
    return root / recording_id


def _parse_events_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
            if isinstance(item, dict):
                out.append(item)
        except Exception:
            continue
    return out


def _extract_keyframes_from_gif(
    *,
    gif_path: Path,
    out_dir: Path,
    keyframe_interval_seconds: float,
    fps_hint: float,
    max_keyframes: int = 120,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    frame_step = max(1, int(round(max(0.1, keyframe_interval_seconds) * max(0.1, fps_hint))))

    with Image.open(gif_path) as img:
        total = int(getattr(img, "n_frames", 1) or 1)
        selected_indices = list(range(0, total, frame_step))
        if (total - 1) not in selected_indices:
            selected_indices.append(total - 1)
        selected_indices = selected_indices[:max_keyframes]

        for idx, frame_index in enumerate(selected_indices, start=1):
            img.seek(frame_index)
            frame = img.convert("RGB")
            out_path = out_dir / f"{idx:06d}.png"
            frame.save(out_path, format="PNG")
            timestamp_seconds = frame_index / max(0.1, fps_hint)
            frames.append(
                {
                    "index": idx,
                    "source_frame": frame_index,
                    "timestamp_seconds": round(timestamp_seconds, 3),
                    "path": str(out_path),
                }
            )
    return frames


def _ocr_image_file(path: Path) -> str:
    try:
        import pytesseract
    except Exception:
        return ""
    try:
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img)
        return (text or "").strip()
    except Exception:
        return ""


def _collect_error_hints(lines: list[str]) -> list[str]:
    pattern = re.compile(r"\b(error|failed|exception|traceback|denied|timeout|not responding|fatal)\b", re.IGNORECASE)
    hints: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if text and pattern.search(text):
            hints.append(text)
        if len(hints) >= 12:
            break
    return hints


def _build_analysis_markdown(
    *,
    recording_id: str,
    output_format: str,
    question: str | None,
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    keyframe_ocr: list[dict[str, Any]],
    likely_errors: list[str],
    next_suggested_step: str,
) -> str:
    target = manifest.get("target")
    lines: list[str] = [
        f"# Recording Analysis: {recording_id}",
        "",
        f"- Output format: `{output_format}`",
        f"- Started: `{manifest.get('started_at')}`",
        f"- Ended: `{manifest.get('ended_at')}`",
        f"- Duration seconds: `{manifest.get('duration_seconds')}`",
        f"- Target: `{target}`",
        f"- Events captured: `{len(events)}`",
        f"- Keyframes: `{len(keyframes)}`",
    ]
    if question:
        lines.extend(["", "## Question", "", question])

    lines.extend(["", "## Likely errors", ""])
    if likely_errors:
        lines.extend([f"- {item}" for item in likely_errors])
    else:
        lines.append("- No obvious error text detected.")

    lines.extend(["", "## Timeline", ""])
    if events:
        for event in events[:30]:
            timestamp = event.get("timestamp") or event.get("ts") or "(no-ts)"
            event_type = event.get("type") or event.get("tool") or "event"
            summary = event.get("summary") or event.get("action") or event.get("message") or ""
            lines.append(f"- `{timestamp}` **{event_type}** {summary}".strip())
    else:
        lines.append("- No event timeline file found.")

    if keyframe_ocr:
        lines.extend(["", "## Keyframe OCR highlights", ""])
        for item in keyframe_ocr[:12]:
            ts = item.get("timestamp_seconds")
            excerpt = str(item.get("text_excerpt") or "").strip()
            if excerpt:
                lines.append(f"- t={ts}s: {excerpt}")

    lines.extend(["", "## Suggested next step", "", f"- {next_suggested_step}"])
    return "\n".join(lines)


def analyze_recording(
    recording_id: str,
    *,
    question: str | None = None,
    extract_keyframes: bool = True,
    keyframe_interval_seconds: float = 2.0,
    include_ocr: bool = True,
    include_ui_context: bool = True,
    include_event_timeline: bool = True,
    output_format: str = "debug_report",
    root_dir: Path | None = None,
) -> dict[str, Any]:
    """Analyze a saved recording using local artifacts only.

    This implementation is local-first and does not upload media.
    """
    manifest = get_recording_manifest(recording_id, root_dir=root_dir)
    rec_dir = _recording_dir(recording_id, root_dir=root_dir)

    events_path = rec_dir / "events.jsonl"
    events = _parse_events_jsonl(events_path) if include_event_timeline else []

    manifest_result = manifest.get("result") if isinstance(manifest.get("result"), dict) else {}
    output_path_raw = manifest_result.get("output_path") if isinstance(manifest_result, dict) else None
    output_path = Path(str(output_path_raw)) if output_path_raw else rec_dir / "recording.gif"
    if not output_path.is_absolute():
        output_path = rec_dir / output_path

    fps_hint = float(manifest.get("fps") or 5.0)
    keyframes_dir = rec_dir / "keyframes"
    keyframes: list[dict[str, Any]] = []
    keyframe_notes: list[str] = []

    if extract_keyframes:
        if output_path.exists() and output_path.suffix.lower() == ".gif":
            try:
                keyframes = _extract_keyframes_from_gif(
                    gif_path=output_path,
                    out_dir=keyframes_dir,
                    keyframe_interval_seconds=keyframe_interval_seconds,
                    fps_hint=fps_hint,
                )
            except Exception as e:
                keyframe_notes.append(f"Keyframe extraction failed: {e}")
        else:
            keyframe_notes.append("Keyframe extraction currently supports GIF artifacts in this phase.")
    elif keyframes_dir.exists():
        for idx, path in enumerate(sorted(keyframes_dir.glob("*.png")), start=1):
            keyframes.append({"index": idx, "timestamp_seconds": None, "path": str(path)})

    keyframe_ocr: list[dict[str, Any]] = []
    if include_ocr and keyframes:
        for frame in keyframes[:20]:
            frame_path = Path(str(frame.get("path") or ""))
            if not frame_path.exists():
                continue
            text = _ocr_image_file(frame_path)
            if text:
                keyframe_ocr.append(
                    {
                        "index": frame.get("index"),
                        "timestamp_seconds": frame.get("timestamp_seconds"),
                        "text_excerpt": text[:240],
                    }
                )

    error_lines: list[str] = []
    if keyframe_ocr:
        error_lines.extend([str(item.get("text_excerpt") or "") for item in keyframe_ocr])
    if events:
        error_lines.extend(
            [
                str(item.get("summary") or item.get("message") or item.get("action") or "")
                for item in events
            ]
        )
    likely_errors = _collect_error_hints(error_lines)

    major_state_changes: list[str] = []
    if include_ui_context and manifest.get("target"):
        major_state_changes.append(f"Target context: {manifest.get('target')}")
    if keyframe_ocr:
        major_state_changes.extend(
            [f"Keyframe {item.get('index')} OCR: {item.get('text_excerpt')}" for item in keyframe_ocr[:6]]
        )

    if likely_errors:
        next_suggested_step = "Inspect error-related keyframes and correlate with events timeline for root cause."
    elif not keyframes:
        next_suggested_step = "Re-record or ensure a GIF artifact exists so keyframes can be extracted locally."
    else:
        next_suggested_step = "Review keyframe OCR timeline and rerun with shorter keyframe interval if needed."

    timeline: list[dict[str, Any]] = []
    if include_event_timeline:
        for event in events[:200]:
            timeline.append(
                {
                    "timestamp": event.get("timestamp") or event.get("ts"),
                    "type": event.get("type") or event.get("tool") or "event",
                    "summary": event.get("summary") or event.get("message") or event.get("action") or "",
                }
            )

    analysis_md = _build_analysis_markdown(
        recording_id=recording_id,
        output_format=output_format,
        question=question,
        manifest=manifest,
        events=events,
        keyframes=keyframes,
        keyframe_ocr=keyframe_ocr,
        likely_errors=likely_errors,
        next_suggested_step=next_suggested_step,
    )
    analysis_path = rec_dir / "analysis.md"
    analysis_path.write_text(analysis_md, encoding="utf-8")

    return {
        "recording_id": recording_id,
        "question": question,
        "output_format": output_format,
        "manifest": manifest,
        "event_count": len(events),
        "events_path": str(events_path),
        "keyframes_extracted": len(keyframes),
        "keyframes_dir": str(keyframes_dir),
        "keyframes": keyframes,
        "keyframe_notes": keyframe_notes,
        "keyframe_ocr": keyframe_ocr,
        "timeline": timeline,
        "major_visible_state_changes": major_state_changes,
        "likely_errors": likely_errors,
        "next_suggested_step": next_suggested_step,
        "analysis_path": str(analysis_path),
    }


def record_screen(
    duration: float = 3.0,
    fps: int = 5,
    left: int | None = None,
    top: int | None = None,
    right: int | None = None,
    bottom: int | None = None,
    max_width: int = 800,
) -> str:
    """Record the screen for *duration* seconds at *fps* and return a base64 GIF.

    Args:
        duration: Recording length in seconds (max 10).
        fps: Frames per second (max 10).
        left/top/right/bottom: Optional region to capture.
        max_width: Resize frames to this max width.

    Returns:
        Base64-encoded GIF data.
    """
    duration = min(max(duration, 0.5), 10.0)
    fps = min(max(fps, 1), 10)
    interval = 1.0 / fps
    total_frames = int(duration * fps)

    bbox = None
    if left is not None and top is not None and right is not None and bottom is not None:
        bbox = desktop.normalize_region(left, top, right, bottom)

    frames = []
    start = time.monotonic()
    for i in range(total_frames):
        target_time = start + i * interval
        now = time.monotonic()
        if now < target_time:
            time.sleep(target_time - now)

        if bbox is not None:
            img, _metadata = desktop.capture_image(left=bbox[0], top=bbox[1], right=bbox[2], bottom=bbox[3])
        else:
            img, _metadata = desktop.capture_image()
        # Resize if needed
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size)
        frames.append(img)

    if not frames:
        raise RuntimeError("No frames captured")

    # Create GIF
    buf = io.BytesIO()
    frame_duration_ms = int(1000 / fps)
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
        optimize=True,
    )
    return base64.b64encode(buf.getvalue()).decode()
