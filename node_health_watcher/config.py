from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _default_config_dir() -> Path:
    return Path(os.environ.get("NHW_CONFIG_DIR", Path(__file__).resolve().parent.parent / "config"))


@dataclass
class BastionConfig:
    hostname: str = ""
    ip: str = ""
    port: int = 22
    username: str = "root"
    key_file: str = "~/.ssh/id_rsa"


@dataclass
class NodeConfig:
    hostname: str = ""
    ip: str = ""
    port: int = 22
    username: str = "root"
    key_file: str = "~/.ssh/id_rsa"
    groups: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    bastion: BastionConfig | None = None
    k8s_node_name: str = ""  # K8s Node 名称，空则回退到 hostname


@dataclass
class ThresholdConfig:
    warning: float = 0
    critical: float = 0


@dataclass
class DiskThresholds:
    mount_points: list[str] = field(default_factory=lambda: ["/", "/var/lib/kubelet", "/var/lib/containerd"])
    space: ThresholdConfig = field(default_factory=ThresholdConfig)
    inode: ThresholdConfig = field(default_factory=ThresholdConfig)
    io_latency_ms: ThresholdConfig = field(default_factory=ThresholdConfig)


@dataclass
class MemoryThresholds:
    available: ThresholdConfig = field(default_factory=ThresholdConfig)
    swap: ThresholdConfig = field(default_factory=ThresholdConfig)
    oom_window_minutes: int = 15


@dataclass
class ConntrackThresholds:
    table_usage: ThresholdConfig = field(default_factory=ThresholdConfig)
    time_wait_max: int = 10000


@dataclass
class KubeletThresholds:
    pleg_latency_seconds: ThresholdConfig = field(default_factory=ThresholdConfig)
    log_scan_window_minutes: int = 15
    log_error_patterns: list[str] = field(
        default_factory=lambda: ["error", "timeout", "deadline", "backoff", "eviction"]
    )


@dataclass
class KernelThresholds:
    dmesg_critical_patterns: list[str] = field(
        default_factory=lambda: ["BUG:", "Kernel panic", "segfault", "Hardware Error", "WARNING:"]
    )
    hung_task_timeout: int = 120


@dataclass
class LevelRouting:
    warning: bool = True
    critical: bool = True


@dataclass
class ChannelConfig:
    enabled: bool = True
    webhook_url: str = ""
    signing_key: str = ""
    level_routing: LevelRouting = field(default_factory=LevelRouting)


@dataclass
class GroupRouting:
    feishu: list[str] = field(default_factory=list)
    dingtalk: list[str] = field(default_factory=list)


@dataclass
class AlertingConfig:
    feishu: ChannelConfig = field(default_factory=ChannelConfig)
    dingtalk: ChannelConfig = field(default_factory=ChannelConfig)
    group_routing: dict[str, GroupRouting] = field(default_factory=dict)


@dataclass
class AppConfig:
    nodes: list[NodeConfig] = field(default_factory=list)
    concurrency: int = 5
    ssh_timeout: int = 15
    thresholds: dict[str, Any] = field(default_factory=dict)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    global_checks: dict[str, bool] = field(
        default_factory=lambda: {"disk": True, "memory": True, "conntrack": True, "kubelet": True, "kernel": True}
    )


_CHECK_CLASSES: dict[str, type] = {}


def register_check(name: str):
    """Decorator to register a check class in the global registry."""

    def decorator(cls: type) -> type:
        _CHECK_CLASSES[name] = cls
        return cls

    return decorator


def get_check_classes() -> dict[str, type]:
    return dict(_CHECK_CLASSES)


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _parse_threshold(raw: dict | None, key: str) -> ThresholdConfig:
    if raw is None:
        raw = {}
    entry = raw.get(key, {})
    return ThresholdConfig(
        warning=float(entry.get("warning_pct", entry.get("warning", 0))),
        critical=float(entry.get("critical_pct", entry.get("critical", 0))),
    )


def _parse_level_routing(raw: dict) -> LevelRouting:
    return LevelRouting(
        warning=bool(raw.get("warning", True)),
        critical=bool(raw.get("critical", True)),
    )


def _parse_channel(raw: dict) -> ChannelConfig:
    return ChannelConfig(
        enabled=bool(raw.get("enabled", True)),
        webhook_url=str(raw.get("webhook_url", "")),
        signing_key=str(raw.get("signing_key", "")),
        level_routing=_parse_level_routing(raw.get("level_routing", {})),
    )


def _parse_group_routing(raw: dict) -> dict[str, GroupRouting]:
    result: dict[str, GroupRouting] = {}
    for group_name, routing in (raw or {}).items():
        result[group_name] = GroupRouting(
            feishu=[str(x) for x in routing.get("feishu", [])],
            dingtalk=[str(x) for x in routing.get("dingtalk", [])],
        )
    return result


def _apply_defaults(config: AppConfig) -> AppConfig:
    """Ensure each known check category has a thresholds entry.

    Individual default values are provided by each check class's
    ``default_thresholds()`` at construction time; this method only
    guarantees the top-level keys exist so user config can safely
    override individual settings.
    """
    if not config.thresholds:
        config.thresholds = {}

    known_categories = [
        "disk",
        "memory",
        "conntrack",
        "kubelet",
        "kernel",
    ]
    for category in known_categories:
        config.thresholds.setdefault(category, {})

    return config


def load_config(config_dir: Path | None = None) -> AppConfig:
    if config_dir is None:
        config_dir = _default_config_dir()

    config = AppConfig()

    nodes_path = config_dir / "nodes.yaml"
    if nodes_path.exists():
        with open(nodes_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        config.concurrency = int(raw.get("concurrency", config.concurrency))
        config.ssh_timeout = int(raw.get("ssh_timeout", config.ssh_timeout))
        config.global_checks = raw.get("global_checks", config.global_checks)
        for node_raw in raw.get("nodes", []):
            node = NodeConfig(
                hostname=str(node_raw.get("hostname", "")),
                ip=str(node_raw.get("ip", "")),
                port=int(node_raw.get("port", 22)),
                username=str(node_raw.get("username", "root")),
                key_file=str(node_raw.get("key_file", "~/.ssh/id_rsa")),
                groups=[str(g) for g in node_raw.get("groups", [])],
                checks={str(k): bool(v) for k, v in node_raw.get("checks", {}).items()},
                k8s_node_name=str(node_raw.get("k8s_node_name", "")),
            )
            if "bastion" in node_raw:
                b = node_raw["bastion"]
                node.bastion = BastionConfig(
                    hostname=str(b.get("hostname", "")),
                    ip=str(b.get("ip", "")),
                    port=int(b.get("port", 22)),
                    username=str(b.get("username", "root")),
                    key_file=str(b.get("key_file", "~/.ssh/id_rsa")),
                )
            config.nodes.append(node)

    thresholds_path = config_dir / "thresholds.yaml"
    if thresholds_path.exists():
        with open(thresholds_path, encoding="utf-8") as f:
            config.thresholds = yaml.safe_load(f) or {}

    _apply_defaults(config)

    alerting_path = config_dir / "alerting.yaml"
    if alerting_path.exists():
        with open(alerting_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        config.alerting = AlertingConfig(
            feishu=_parse_channel(raw.get("feishu", {})),
            dingtalk=_parse_channel(raw.get("dingtalk", {})),
            group_routing=_parse_group_routing(raw.get("group_routing", {})),
        )

    return config
