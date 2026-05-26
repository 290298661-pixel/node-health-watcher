from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Import check modules to trigger @register_check decorators
import node_health_watcher.checks.conntrack  # noqa: F401
import node_health_watcher.checks.disk  # noqa: F401
import node_health_watcher.checks.kernel  # noqa: F401
import node_health_watcher.checks.kubelet  # noqa: F401
import node_health_watcher.checks.memory  # noqa: F401
from node_health_watcher.alert.dedup import DedupStore
from node_health_watcher.alert.dingtalk import send_dingtalk, send_dingtalk_recovery
from node_health_watcher.alert.feishu import send_feishu, send_feishu_recovery
from node_health_watcher.checks.base import CheckLevel
from node_health_watcher.config import AppConfig, get_check_classes
from node_health_watcher.transport.executor import run_inspection

logger = logging.getLogger(__name__)


def _build_check_instances(config: AppConfig) -> dict:
    instances = {}
    for name, cls in get_check_classes().items():
        if config.global_checks.get(name, True):
            instances[name] = cls(thresholds=config.thresholds.get(name, {}))
    return instances


def _should_route(level: str, channel: str, node_groups: list[str], group_routing: dict, channel_config) -> bool:
    """Determine whether an alert should be sent to a specific IM channel.

    Checks per-node-group routing rules first. If any group the node belongs to
    has an explicit allow-list for this channel, that rule is used.  If no group
    has explicit rules for the channel, falls back to the channel-level default.
    """
    has_explicit = False
    for group in node_groups:
        if group in group_routing:
            routing = group_routing[group]
            allowed = getattr(routing, channel, None)
            if allowed is not None:
                has_explicit = True
                if level in allowed:
                    return True
    if has_explicit:
        return False
    if level == "warning":
        return channel_config.level_routing.warning
    return channel_config.level_routing.critical


def _partition_alerts(alerts, errors, node_map, group_routing, feishu_cfg, dingtalk_cfg):
    """Split alerts and errors into per-channel batches based on group routing."""
    feishu_alerts: list = []
    dingtalk_alerts: list = []
    feishu_errors: list = []
    dingtalk_errors: list = []

    for r in alerts:
        node = node_map.get(r.hostname)
        groups = node.groups if node else []
        if _should_route(r.level.value, "feishu", groups, group_routing, feishu_cfg):
            feishu_alerts.append(r)
        if _should_route(r.level.value, "dingtalk", groups, group_routing, dingtalk_cfg):
            dingtalk_alerts.append(r)

    for e in errors:
        node = node_map.get(e.hostname)
        groups = node.groups if node else []
        if _should_route("warning", "feishu", groups, group_routing, feishu_cfg):
            feishu_errors.append(e)
        if _should_route("warning", "dingtalk", groups, group_routing, dingtalk_cfg):
            dingtalk_errors.append(e)

    return feishu_alerts, dingtalk_alerts, feishu_errors, dingtalk_errors


def run_once(config: AppConfig, dedup: DedupStore, dry_run: bool = False) -> int:
    """Execute a single inspection cycle. Returns number of anomalies."""
    check_instances = _build_check_instances(config)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    start = time.perf_counter()
    results, errors = run_inspection(
        nodes=config.nodes,
        check_instances=check_instances,
        concurrency=config.concurrency,
        timeout=config.ssh_timeout,
        enabled_checks=config.global_checks,
    )
    elapsed = time.perf_counter() - start

    criticals = [r for r in results if r.level == CheckLevel.CRITICAL]
    warnings = [r for r in results if r.level == CheckLevel.WARNING]
    anomalies = criticals + warnings

    for r in results:
        level_str = r.level.value.upper() if r.level != CheckLevel.OK else "OK"
        logger.info("[%s] [%s] %s: %s", r.hostname, level_str, r.category, r.message)

    for e in errors:
        logger.error("[%s] 连接失败: %s", e.hostname, e.error)

    if not dry_run:
        new_alerts: list = []
        for r in anomalies:
            if dedup.should_alert(r.hostname, r.category, r.sub_check, r.level.value, r.value, timestamp):
                new_alerts.append(r)

        if new_alerts:
            suppressed = len(anomalies) - len(new_alerts)
            logger.info("Sending %d alerts (suppressed %d duplicates)", len(new_alerts), suppressed)

            node_map = {n.hostname: n for n in config.nodes}
            gr = config.alerting.group_routing
            fc = config.alerting.feishu
            dc = config.alerting.dingtalk

            fa, da, fe, de = _partition_alerts(new_alerts, errors, node_map, gr, fc, dc)

            if fa or fe:
                send_feishu(fc, fa, fe, elapsed, len(config.nodes))
            if da or de:
                send_dingtalk(dc, da, de, elapsed, len(config.nodes))

        current_keys = {f"{r.hostname}:{r.category}:{r.sub_check}" for r in results if r.level != CheckLevel.OK}
        recovered_keys = dedup.get_recoveries(current_keys)
        if recovered_keys:
            recoveries = []
            for key in recovered_keys:
                parts = key.split(":", 2)
                recoveries.append(
                    {
                        "hostname": parts[0],
                        "category": parts[1],
                        "message": f"{parts[2]} 已恢复",
                    }
                )
            logger.info("Recovered: %s", recovered_keys)

            node_map = {n.hostname: n for n in config.nodes}
            gr = config.alerting.group_routing
            fc = config.alerting.feishu
            dc = config.alerting.dingtalk

            feishu_recs = []
            dingtalk_recs = []
            for rec in recoveries:
                node = node_map.get(rec["hostname"])
                groups = node.groups if node else []
                if _should_route("warning", "feishu", groups, gr, fc):
                    feishu_recs.append(rec)
                if _should_route("warning", "dingtalk", groups, gr, dc):
                    dingtalk_recs.append(rec)

            if feishu_recs:
                send_feishu_recovery(fc, feishu_recs)
            if dingtalk_recs:
                send_dingtalk_recovery(dc, dingtalk_recs)
    else:
        logger.info("[DRY-RUN] 跳过告警推送，共 %d 个异常，%d 个连接失败", len(anomalies), len(errors))

    return len(anomalies) + len(errors)


def parse_interval(interval_str: str) -> int:
    """Parse a human-readable interval like '5m', '1h', '30s' into seconds."""
    interval_str = interval_str.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    for suffix, mult in multipliers.items():
        if interval_str.endswith(suffix):
            value = interval_str[: -len(suffix)].strip()
            try:
                return int(float(value) * mult)
            except (ValueError, OverflowError):
                pass
    try:
        return int(interval_str)
    except ValueError:
        logger.warning("Unrecognized interval '%s', falling back to 300s", interval_str)
        return 300


def start_scheduler(
    config: AppConfig,
    dedup: DedupStore,
    interval: str | None = None,
    cron: str | None = None,
    dry_run: bool = False,
) -> BackgroundScheduler:
    """Start APScheduler with the configured trigger. Returns the scheduler instance."""
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def job():
        try:
            run_once(config, dedup, dry_run=dry_run)
        except Exception as exc:
            logger.error("Inspection job failed: %s", exc, exc_info=True)

    if cron:
        scheduler.add_job(job, CronTrigger.from_crontab(cron), id="inspection")
        logger.info("Scheduler started with cron: %s", cron)
    else:
        seconds = parse_interval(interval or "5m")
        scheduler.add_job(job, IntervalTrigger(seconds=seconds), id="inspection")
        logger.info("Scheduler started with interval: %ds", seconds)

    scheduler.start()
    return scheduler
