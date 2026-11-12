"""Atomic local storage for the single confirmed base resume.

API contract (see ``web/routes.py``): GET/PUT/DELETE /api/resume; POST /api/reset.
Only schema-validated resume objects are accepted by the HTTP layer.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def data_directory() -> Path:
    """Return a fixed per-user application data directory."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif __import__("sys").platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "ai-cv-builder"


class ResumeStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.path = self.directory / "base-resume.json"

    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def save(self, resume: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(resume, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    def reset(self) -> None:
        self.delete()

