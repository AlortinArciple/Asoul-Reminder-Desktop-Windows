from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from .paths import emoji_cache_dir

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def normalize_emoji_url(url: str) -> str:
    text = str(url or "").strip()
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return text


def emoji_cache_path(url: str) -> Path:
    normalized = normalize_emoji_url(url)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]
    suffix = Path(urlparse(normalized).path).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".png"
    return emoji_cache_dir() / f"{digest}{suffix}"
