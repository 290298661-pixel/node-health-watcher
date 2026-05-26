from __future__ import annotations

from node_health_watcher.checks.base import CheckLevel, CheckResult


def threshold_label(r: CheckResult) -> str:
    if not r.thresholds:
        return ""
    if r.level == CheckLevel.WARNING and "warning" in r.thresholds:
        return f" (阈值: {r.thresholds['warning']})"
    if r.level == CheckLevel.CRITICAL and "critical" in r.thresholds:
        return f" (阈值: {r.thresholds['critical']})"
    if "max" in r.thresholds:
        return f" (阈值: {r.thresholds['max']})"
    return ""
