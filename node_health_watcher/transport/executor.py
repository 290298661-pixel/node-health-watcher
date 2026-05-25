from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from node_health_watcher.checks.base import CheckResult
from node_health_watcher.config import NodeConfig
from node_health_watcher.transport.ssh import SSHClient

logger = logging.getLogger(__name__)


@dataclass
class NodeError:
    hostname: str
    error: str


def _inspect_one_node(
    node: NodeConfig,
    check_instances: dict,
    timeout: int,
    enabled_checks: dict[str, bool],
) -> tuple[str, list[CheckResult] | str]:
    """Run all checks on a single node. Returns (hostname, results_or_error)."""
    node_enabled = dict(enabled_checks)
    node_enabled.update(node.checks)

    client = None
    try:
        client = SSHClient(timeout=timeout)
        bastion_dict = None
        if node.bastion:
            bastion_dict = {
                "hostname": node.bastion.hostname,
                "ip": node.bastion.ip,
                "port": node.bastion.port,
                "username": node.bastion.username,
                "key_file": node.bastion.key_file,
            }
        client.connect(
            hostname=node.hostname,
            ip=node.ip,
            port=node.port,
            username=node.username,
            key_file=node.key_file,
            bastion=bastion_dict,
        )

        all_results: list[CheckResult] = []
        for check_name, check in check_instances.items():
            if not node_enabled.get(check_name, True):
                continue
            try:
                check.set_node(node)
                commands = check.probe_commands()
                outputs: dict[str, str] = {}
                for sub_name, cmd in commands.items():
                    outputs[sub_name] = client.execute(cmd)
                results = check.parse(node.hostname, outputs)
                all_results.extend(results)
            except Exception as exc:
                logger.warning("[%s] check %s failed: %s", node.hostname, check_name, exc)

        return (node.hostname, all_results)
    except Exception as exc:
        logger.error("[%s] SSH or check error: %s", node.hostname, exc)
        return (node.hostname, str(exc))
    finally:
        if client is not None:
            client.close()


def run_inspection(
    nodes: list[NodeConfig],
    check_instances: dict,
    concurrency: int = 5,
    timeout: int = 15,
    enabled_checks: dict[str, bool] | None = None,
) -> tuple[list[CheckResult], list[NodeError]]:
    """Run inspection concurrently across nodes.

    Returns:
        (results, errors) — successful CheckResults and per-node failure messages.
    """
    if enabled_checks is None:
        enabled_checks = {}

    results: list[CheckResult] = []
    errors: list[NodeError] = []

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {
            executor.submit(_inspect_one_node, node, check_instances, timeout, enabled_checks): node for node in nodes
        }
        for future in as_completed(futures):
            hostname, outcome = future.result()
            if isinstance(outcome, list):
                results.extend(outcome)
            else:
                errors.append(NodeError(hostname=hostname, error=outcome))

    return results, errors
