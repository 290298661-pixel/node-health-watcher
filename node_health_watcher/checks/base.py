from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class CheckLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    hostname: str
    category: str
    sub_check: str
    level: CheckLevel
    value: str = ""
    message: str = ""
    thresholds: dict[str, float] = field(default_factory=dict)


def _deep_merge_thresholds(base: dict, override: dict) -> None:
    """Merge override into base in-place, recursing into nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge_thresholds(base[key], value)
        else:
            base[key] = value


class BaseCheck(ABC):
    """Abstract base for a health check plugin.

    Subclasses define *what commands* to run on the target node (probe_commands)
    and *how to interpret* the output (parse). The scheduler and executor handle
    SSH transport, concurrency, and alert delivery — checks only deal with logic.
    """

    name: str = ""
    description: str = ""

    @classmethod
    def default_thresholds(cls) -> dict:
        """Return the default threshold dict for this check.

        Override in subclasses to provide category-specific defaults.
        """
        return {}

    def __init__(self, thresholds: dict) -> None:
        merged = dict(self.default_thresholds())
        _deep_merge_thresholds(merged, thresholds)
        self.thresholds = merged
        self._node = None

    def set_node(self, node) -> None:
        """Receive node context before probe/parse. Override to access node config."""
        self._node = node

    @abstractmethod
    def probe_commands(self) -> dict[str, str]:
        """Return a mapping of sub_check_name → shell command.

        Each command runs independently on the target node. Keep commands
        read-only and resilient to missing optional tools (e.g. iostat).
        """

    @abstractmethod
    def parse(self, hostname: str, outputs: dict[str, str]) -> list[CheckResult]:
        """Parse raw command outputs into CheckResult list.

        Args:
            hostname: The node hostname this data came from.
            outputs: Keys match probe_commands() keys; values are stdout strings.
        """
