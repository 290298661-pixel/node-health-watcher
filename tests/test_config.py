from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from node_health_watcher.checks.conntrack import ConntrackCheck
from node_health_watcher.checks.disk import DiskCheck
from node_health_watcher.checks.kernel import KernelCheck
from node_health_watcher.checks.kubelet import KubeletCheck
from node_health_watcher.checks.memory import MemoryCheck
from node_health_watcher.config import AppConfig, load_config


def _write_yaml(dir: Path, name: str, data: dict) -> None:
    with open(dir / name, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


class TestDefaultThresholds:
    """Each check class provides its own defaults via default_thresholds()."""

    def test_disk_defaults_merged(self):
        check = DiskCheck(thresholds={})
        assert check.thresholds["mount_points"] == ["/", "/var/lib/kubelet", "/var/lib/containerd"]
        assert check.thresholds["space"]["warning_pct"] == 80
        assert check.thresholds["space"]["critical_pct"] == 90

    def test_disk_user_override(self):
        check = DiskCheck(thresholds={"space": {"warning_pct": 70}})
        assert check.thresholds["space"]["warning_pct"] == 70
        assert check.thresholds["space"]["critical_pct"] == 90  # default preserved

    def test_memory_defaults_merged(self):
        check = MemoryCheck(thresholds={})
        assert check.thresholds["available"]["warning_pct"] == 20
        assert check.thresholds["available"]["critical_pct"] == 10
        assert check.thresholds["oom_window_minutes"] == 15

    def test_conntrack_defaults_merged(self):
        check = ConntrackCheck(thresholds={})
        assert check.thresholds["table_usage"]["warning_pct"] == 85
        assert check.thresholds["table_usage"]["critical_pct"] == 95
        assert check.thresholds["time_wait_max"] == 10000

    def test_kubelet_defaults_merged(self):
        check = KubeletCheck(thresholds={})
        assert check.thresholds["pleg_latency_seconds"]["warning"] == 2.0
        assert check.thresholds["pleg_latency_seconds"]["critical"] == 5.0

    def test_kernel_defaults_merged(self):
        check = KernelCheck(thresholds={})
        assert "BUG:" in check.thresholds["dmesg_critical_patterns"]
        assert check.thresholds["hung_task_timeout"] == 120

    def test_base_check_defaults(self):
        from node_health_watcher.checks.base import BaseCheck

        class MinimalCheck(BaseCheck):
            name = "minimal"

            def probe_commands(self):
                return {}

            def parse(self, hostname, outputs):
                return []

        check = MinimalCheck(thresholds={"custom": 42})
        assert check.thresholds["custom"] == 42


class TestConfigLoading:
    def test_empty_config_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(Path(tmpdir))
            assert isinstance(config, AppConfig)
            assert config.nodes == []
            assert config.concurrency == 5
            assert config.ssh_timeout == 15

    def test_nodes_parsing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_yaml(
                Path(tmpdir),
                "nodes.yaml",
                {
                    "nodes": [
                        {
                            "hostname": "test-node",
                            "ip": "10.0.0.1",
                            "port": 2222,
                            "username": "ops",
                            "key_file": "/tmp/key",
                            "groups": ["prod", "worker"],
                        },
                    ],
                    "concurrency": 10,
                    "ssh_timeout": 30,
                },
            )
            config = load_config(Path(tmpdir))
            assert len(config.nodes) == 1
            node = config.nodes[0]
            assert node.hostname == "test-node"
            assert node.ip == "10.0.0.1"
            assert node.port == 2222
            assert node.username == "ops"
            assert node.key_file == "/tmp/key"
            assert node.groups == ["prod", "worker"]
            assert config.concurrency == 10
            assert config.ssh_timeout == 30

    def test_node_with_bastion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_yaml(
                Path(tmpdir),
                "nodes.yaml",
                {
                    "nodes": [
                        {
                            "hostname": "behind-bastion",
                            "ip": "10.0.1.1",
                            "bastion": {
                                "hostname": "jump",
                                "ip": "10.0.0.1",
                                "port": 2222,
                                "username": "ops",
                                "key_file": "/tmp/bastion_key",
                            },
                        },
                    ],
                },
            )
            config = load_config(Path(tmpdir))
            node = config.nodes[0]
            assert node.bastion is not None
            assert node.bastion.hostname == "jump"
            assert node.bastion.ip == "10.0.0.1"
            assert node.bastion.port == 2222
            assert node.bastion.username == "ops"

    def test_thresholds_minimal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_yaml(
                Path(tmpdir),
                "thresholds.yaml",
                {
                    "disk": {"space": {"warning_pct": 75}},
                },
            )
            config = load_config(Path(tmpdir))
            assert "disk" in config.thresholds
            assert config.thresholds["disk"]["space"]["warning_pct"] == 75

    def test_all_categories_have_defaults(self):
        """After load_config, all 5 categories exist even without user thresholds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(Path(tmpdir))
            for cat in ("disk", "memory", "conntrack", "kubelet", "kernel"):
                assert cat in config.thresholds, f"{cat} missing from thresholds"

    def test_alerting_parsing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_yaml(
                Path(tmpdir),
                "alerting.yaml",
                {
                    "feishu": {
                        "enabled": True,
                        "webhook_url": "https://feishu.example.com/hook",
                        "signing_key": "secret123",
                        "level_routing": {"warning": True, "critical": True},
                    },
                    "dingtalk": {
                        "enabled": False,
                        "webhook_url": "",
                        "level_routing": {"warning": False, "critical": True},
                    },
                    "group_routing": {
                        "production": {
                            "feishu": ["warning", "critical"],
                            "dingtalk": ["critical"],
                        },
                    },
                },
            )
            config = load_config(Path(tmpdir))
            assert config.alerting.feishu.enabled is True
            assert config.alerting.feishu.webhook_url == "https://feishu.example.com/hook"
            assert config.alerting.feishu.signing_key == "secret123"
            assert config.alerting.dingtalk.enabled is False
            assert "production" in config.alerting.group_routing

    def test_global_checks_parsing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_yaml(
                Path(tmpdir),
                "nodes.yaml",
                {
                    "nodes": [],
                    "global_checks": {"disk": False, "memory": True},
                },
            )
            config = load_config(Path(tmpdir))
            assert config.global_checks["disk"] is False
            assert config.global_checks["memory"] is True


class TestSchedulerHelpers:
    def test_parse_interval_seconds(self):
        from node_health_watcher.scheduler import parse_interval

        assert parse_interval("30s") == 30
        assert parse_interval("5m") == 300
        assert parse_interval("1h") == 3600
        assert parse_interval("1d") == 86400

    def test_parse_interval_numeric(self):
        from node_health_watcher.scheduler import parse_interval

        assert parse_interval("600") == 600

    def test_parse_interval_invalid_fallback(self):
        from node_health_watcher.scheduler import parse_interval

        assert parse_interval("invalid") == 300

    def test_parse_interval_int_type(self):
        from node_health_watcher.scheduler import parse_interval

        result = parse_interval("1.5h")
        assert isinstance(result, int)
        assert result == 5400
