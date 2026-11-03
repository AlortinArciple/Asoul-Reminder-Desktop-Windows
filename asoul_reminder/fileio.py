from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(text, encoding=encoding)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
