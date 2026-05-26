from __future__ import annotations

import tempfile
from pathlib import Path

from node_health_watcher.alert.dedup import DedupStore


class TestDedupStore:
    def test_first_alert_fires(self):
        store = DedupStore()
        result = store.should_alert("node-1", "disk", "space:/", "warning", "85%", "2026-05-24T10:00:00Z")
        assert result is True

    def test_duplicate_suppressed(self):
        store = DedupStore()
        store.should_alert("node-1", "disk", "space:/", "warning", "85%", "2026-05-24T10:00:00Z")
        result = store.should_alert("node-1", "disk", "space:/", "warning", "86%", "2026-05-24T10:05:00Z")
        assert result is False

    def test_escalation_fires(self):
        store = DedupStore()
        store.should_alert("node-1", "disk", "space:/", "warning", "85%", "2026-05-24T10:00:00Z")
        result = store.should_alert("node-1", "disk", "space:/", "critical", "95%", "2026-05-24T10:05:00Z")
        assert result is True

    def test_ok_is_always_silent(self):
        """should_alert returns False for OK results — recovery goes through get_recoveries."""
        store = DedupStore()
        store.should_alert("node-1", "disk", "space:/", "warning", "85%", "2026-05-24T10:00:00Z")
        result = store.should_alert("node-1", "disk", "space:/", "ok", "62%", "2026-05-24T10:10:00Z")
        assert result is False  # OK never triggers alert

    def test_ok_without_prior_alert_is_silent(self):
        store = DedupStore()
        result = store.should_alert("node-1", "disk", "space:/", "ok", "62%", "2026-05-24T10:10:00Z")
        assert result is False

    def test_different_nodes_independent(self):
        store = DedupStore()
        assert store.should_alert("node-1", "disk", "space:/", "warning", "85%", "ts") is True
        assert store.should_alert("node-2", "disk", "space:/", "warning", "85%", "ts") is True

    def test_different_checks_independent(self):
        store = DedupStore()
        assert store.should_alert("node-1", "disk", "space:/", "warning", "85%", "ts") is True
        assert store.should_alert("node-1", "memory", "oom", "critical", "3", "ts") is True

    def test_get_recoveries(self):
        store = DedupStore()
        store.should_alert("node-1", "disk", "space:/", "warning", "85%", "ts")
        store.should_alert("node-2", "memory", "oom", "critical", "3", "ts")

        current = {"node-1:disk:space:/"}
        recovered = store.get_recoveries(current)
        assert "node-2:memory:oom" in recovered
        assert "node-1:disk:space:/" not in recovered

    def test_state_file_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            store1 = DedupStore(state_file=str(state_file))
            store1.should_alert("node-1", "disk", "space:/", "warning", "85%", "ts")

            store2 = DedupStore(state_file=str(state_file))
            result = store2.should_alert("node-1", "disk", "space:/", "warning", "86%", "ts")
            assert result is False  # suppressed because state persisted
