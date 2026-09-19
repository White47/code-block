"""Bounded, global UI preferences separate from analyzed project state."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_LANGUAGE = "zh-CN"
SUPPORTED_LANGUAGES = ("zh-CN", "en")
_MAX_BYTES = 4096


class PreferencesStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "preferences.json"
        self.issue: str | None = None

    def load(self) -> str:
        self.issue = None
        if not self.path.exists():
            return DEFAULT_LANGUAGE
        try:
            if self.path.is_symlink() or self.path.stat().st_size > _MAX_BYTES:
                raise ValueError("preferences file is unsafe or too large")
            with self.path.open("rb") as source:
                content = source.read(_MAX_BYTES + 1)
            if len(content) > _MAX_BYTES:
                raise ValueError("preferences file is too large")
            data = json.loads(content)
            if (not isinstance(data, dict) or set(data) != {"version", "language"} or
                    data["version"] != 1 or data["language"] not in SUPPORTED_LANGUAGES):
                raise ValueError("preferences contain an unsupported language")
            return str(data["language"])
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            self.issue = str(error)
            return DEFAULT_LANGUAGE

    def save(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported language")
        content = json.dumps({"version": 1, "language": language},
                             separators=(",", ":")).encode("utf-8")
        self.data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(mode="wb", dir=self.data_dir, prefix=".preferences-",
                                    suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
