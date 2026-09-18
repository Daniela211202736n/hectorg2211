"""Cross-platform pause/resume/stop signalling via plain text files.

Deliberately not using Unix signals (SIGUSR1/SIGUSR2 don't exist on
Windows, and this bot needs to run on whatever OS the operator has). The
CLI process writes a command file; the running engine polls for it once
per loop tick and deletes it after consuming it, so there is up to one
`loop_interval_seconds` of latency on pause/resume/stop -- documented in
the README.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path


class ControlCommand(str, Enum):
    NONE = "NONE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


class ControlChannel:
    def __init__(self, state_dir: str | Path):
        base = Path(state_dir)
        base.mkdir(parents=True, exist_ok=True)
        self._control_path = base / "control.cmd"
        self._status_path = base / "status.txt"
        if not self._status_path.exists():
            self._status_path.write_text("STOPPED", encoding="utf-8")

    def send(self, command: ControlCommand) -> None:
        self._control_path.write_text(command.value, encoding="utf-8")

    def poll(self) -> ControlCommand:
        if not self._control_path.exists():
            return ControlCommand.NONE
        try:
            raw = self._control_path.read_text(encoding="utf-8").strip()
            self._control_path.unlink(missing_ok=True)
        except OSError:
            return ControlCommand.NONE
        try:
            return ControlCommand(raw)
        except ValueError:
            return ControlCommand.NONE

    def write_status(self, status: str) -> None:
        self._status_path.write_text(status, encoding="utf-8")

    @property
    def current_status(self) -> str:
        try:
            return self._status_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "UNKNOWN"

    @property
    def status_path(self) -> Path:
        return self._status_path
