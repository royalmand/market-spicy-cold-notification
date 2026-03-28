#!/usr/bin/env python3
"""
JSONL snapshot persistence for the market notifier.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SnapshotStorage:
    def __init__(self, data_dir: Path, history_limit: int = 5000):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_file = self.data_dir / "history.jsonl"
        self.state_file = self.data_dir / "state.json"
        self.history_limit = history_limit

    def append_snapshot(self, snapshot: dict[str, Any]) -> None:
        with open(self.snapshot_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, separators=(",", ":")) + "\n")
        self._trim_if_needed()

    def load_snapshots(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.snapshot_file.exists():
            return []
        with open(self.snapshot_file, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if limit is None:
            return rows
        return rows[-limit:]

    def load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        with open(self.state_file, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def save_state(self, state: dict[str, Any]) -> None:
        with open(self.state_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)

    def _trim_if_needed(self) -> None:
        snapshots = self.load_snapshots()
        if len(snapshots) <= self.history_limit:
            return
        trimmed = snapshots[-self.history_limit:]
        with open(self.snapshot_file, "w", encoding="utf-8") as handle:
            for snapshot in trimmed:
                handle.write(json.dumps(snapshot, separators=(",", ":")) + "\n")
