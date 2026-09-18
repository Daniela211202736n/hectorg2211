"""Persisted runtime state (runtime/state.json) so `cli.py status` can
report on the bot from a separate process/terminal invocation."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class BotState:
    status: str
    updated_at: str
    snapshot: dict[str, Any]


class StateStore:
    def __init__(self, state_dir: str | Path):
        self._path = Path(state_dir) / "state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: BotState) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic on POSIX and Windows

    def load(self) -> Optional[dict[str, Any]]:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @property
    def path(self) -> Path:
        return self._path
