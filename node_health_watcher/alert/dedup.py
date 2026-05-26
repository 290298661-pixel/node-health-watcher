from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AlertState:
    level: str
    value: str
    since: str


class DedupStore:
    """In-memory alert deduplication with optional JSON file persistence.

    Key: "hostname:category:sub_check"
    Value: AlertState (level, value, since_timestamp)
    """

    def __init__(self, state_file: str | None = None) -> None:
        self._state: dict[str, AlertState] = {}
        self._state_file = Path(state_file) if state_file else None
        if self._state_file and self._state_file.exists():
            self._load()

    def _load(self) -> None:
        try:
            with open(self._state_file, encoding="utf-8") as f:
                raw = json.load(f)
            self._state = {k: AlertState(**v) for k, v in raw.items()}
            logger.debug("Loaded %d alert states from %s", len(self._state), self._state_file)
        except Exception as exc:
            logger.warning("Failed to load state file: %s", exc)

    def _save(self) -> None:
        if not self._state_file:
            return
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                data = {k: {"level": v.level, "value": v.value, "since": v.since} for k, v in self._state.items()}
                json.dump(data, f)
        except Exception as exc:
            logger.warning("Failed to save state file: %s", exc)

    def _key(self, hostname: str, category: str, sub_check: str) -> str:
        return f"{hostname}:{category}:{sub_check}"

    def should_alert(
        self, hostname: str, category: str, sub_check: str, level: str, value: str, timestamp: str
    ) -> bool:
        """Return True if this alert is new or escalated (not a suppressed duplicate).

        Only WARNING / CRITICAL levels are tracked. Recovery detection is handled
        separately via :meth:`get_recoveries`.
        """
        if level == "ok":
            return False

        key = self._key(hostname, category, sub_check)
        existing = self._state.get(key)

        if existing is None:
            self._state[key] = AlertState(level=level, value=value, since=timestamp)
            self._save()
            return True

        if level != existing.level:
            existing.level = level
            existing.value = value
            existing.since = timestamp
            self._save()
            return True

        existing.value = value
        self._save()
        return False

    def get_recoveries(self, current_keys: set[str]) -> list[str]:
        """Find keys that were previously alerting but are no longer in the current set."""
        recovered = []
        for key in list(self._state.keys()):
            if key not in current_keys:
                recovered.append(key)
                del self._state[key]
        if recovered:
            self._save()
        return recovered

    def clear(self) -> None:
        self._state.clear()
        self._save()
