"""Text and image redaction helpers for WinRemote artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageFilter


@dataclass
class RedactionStats:
    redacted_count: int = 0


def redact_text(text: str, patterns: list[str]) -> str:
    """Redact sensitive values in text based on regex patterns."""
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted


def redact_event(event: dict[str, Any], patterns: list[str], *, redact_text_fields: bool = True) -> dict[str, Any]:
    """Redact string fields in a structured event payload."""
    out = dict(event)
    if not redact_text_fields:
        return out

    for key, value in list(out.items()):
        if isinstance(value, str):
            out[key] = redact_text(value, patterns)
    return out


def blur_screenshot_regions(image: Image.Image, boxes: list[tuple[int, int, int, int]], *, radius: int = 14) -> Image.Image:
    """Blur a list of rectangular regions in an image."""
    if not boxes:
        return image

    redacted = image.copy()
    for x1, y1, x2, y2 in boxes:
        if x2 <= x1 or y2 <= y1:
            continue
        crop = redacted.crop((x1, y1, x2, y2))
        crop = crop.filter(ImageFilter.GaussianBlur(radius=radius))
        redacted.paste(crop, (x1, y1, x2, y2))
    return redacted
