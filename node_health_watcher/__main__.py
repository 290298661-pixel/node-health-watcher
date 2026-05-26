"""CLI entry point for Node Health Watcher."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from node_health_watcher.alert.dedup import DedupStore
from node_health_watcher.config import load_config
from node_health_watcher.scheduler import run_once, start_scheduler


def _setup_logging(level: str, fmt: str = "plain") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    if fmt == "json":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                '{"ts":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(numeric_level)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nhw",
        description="Kubernetes 节点定时巡检与 IM 告警中心",
    )
    parser.add_argument("--dry-run", action="store_true", help="执行巡检但不发送告警")
    parser.add_argument("--once", action="store_true", help="单次巡检并发送告警后退出")
    parser.add_argument("--interval", type=str, default=None, help="定时巡检间隔 (如 5m, 1h, 30s)")
    parser.add_argument("--cron", type=str, default=None, help="cron 表达式 (如 '*/10 * * * *')")
    parser.add_argument("--config-dir", type=str, default=None, help="配置文件目录")
    parser.add_argument("--state-file", type=str, default=None, help="告警状态持久化 JSON 文件路径")
    parser.add_argument("--log-level", type=str, default="INFO", help="日志级别")
    parser.add_argument("--log-format", type=str, default="plain", choices=["plain", "json"], help="日志格式")
    parser.add_argument("--version", action="store_true", help="显示版本号")

    args = parser.parse_args()

    if args.version:
        from node_health_watcher import __version__

        print(f"node-health-watcher v{__version__}")
        return

    _setup_logging(level=args.log_level, fmt=args.log_format)
    logger = logging.getLogger(__name__)

    config_dir = Path(args.config_dir) if args.config_dir else None
    config = load_config(config_dir)

    if not config.nodes:
        logger.error("未配置任何节点，请编辑 config/nodes.yaml 后重试")
        sys.exit(1)

    dedup = DedupStore(state_file=args.state_file)

    if args.once or (not args.interval and not args.cron):
        anomaly_count = run_once(config, dedup, dry_run=args.dry_run)
        logger.info("巡检完成，异常数: %d，节点数: %d", anomaly_count, len(config.nodes))
        return

    if args.cron and args.interval:
        logger.warning("同时指定 --cron 和 --interval，--cron 优先，--interval 将被忽略")

    scheduler = start_scheduler(
        config=config,
        dedup=dedup,
        interval=args.interval,
        cron=args.cron,
        dry_run=args.dry_run,
    )

    _shutting_down = False

    def shutdown(signum, frame):
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        logger.info("收到信号 %s，正在退出...", signum)
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Node Health Watcher 已启动，按 Ctrl+C 退出")
    try:
        signal.pause()
    except AttributeError:
        import time

        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
