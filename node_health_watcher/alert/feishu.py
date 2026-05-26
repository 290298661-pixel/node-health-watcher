from __future__ import annotations

import hashlib
import hmac
import logging
import time

import requests

from node_health_watcher.alert.common import threshold_label
from node_health_watcher.checks.base import CheckLevel, CheckResult
from node_health_watcher.config import ChannelConfig

logger = logging.getLogger(__name__)


def _build_card(
    results: list[CheckResult],
    errors: list,
    duration: float,
    node_count: int,
) -> dict:
    criticals = [r for r in results if r.level == CheckLevel.CRITICAL]
    warnings = [r for r in results if r.level == CheckLevel.WARNING]
    healthy_count = node_count - len({r.hostname for r in criticals + warnings})

    elements: list[dict] = []
    if criticals:
        lines = [
            f"🔴 CRITICAL ({len(criticals)})",
        ]
        for r in criticals:
            label = threshold_label(r)
            lines.append(f"├─ [{r.hostname}] {r.category}: {r.message}{label}")
        elements.append({"tag": "markdown", "content": "\n".join(lines)})

    if warnings:
        lines = [
            f"⚠️ WARNING ({len(warnings)})",
        ]
        for r in warnings:
            label = threshold_label(r)
            lines.append(f"├─ [{r.hostname}] {r.category}: {r.message}{label}")
        elements.append({"tag": "markdown", "content": "\n".join(lines)})

    footer_lines = []
    if errors:
        footer_lines.append(f"❌ 连接失败: {len(errors)} 个节点")
    footer_lines.append(f"✅ 正常: {healthy_count} 个节点")
    footer_lines.append(f"📊 巡检耗时: {duration:.1f}s")
    elements.append({"tag": "markdown", "content": "\n".join(footer_lines)})

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "🏥 K8s 节点健康巡检"},
                "template": "red" if criticals else ("yellow" if warnings else "green"),
            },
            "elements": elements,
        },
    }


def _build_recovery_card(recoveries: list[dict]) -> dict:
    lines = ["✅ 节点健康恢复通知", ""]
    for rec in recoveries:
        lines.append(f"[{rec['hostname']}] {rec['category']}: {rec['message']}")

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "✅ 节点健康恢复"},
                "template": "green",
            },
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
            ],
        },
    }


def _sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        key=secret.encode("utf-8"),
        msg=string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return hmac_code.digest().hex()


def send_feishu(
    channel: ChannelConfig,
    results: list[CheckResult],
    errors: list,
    duration: float,
    node_count: int,
) -> bool:
    """Send inspection results to Feishu bot. Returns True on success."""
    if not channel.enabled or not channel.webhook_url:
        return False

    has_critical = any(r.level == CheckLevel.CRITICAL for r in results)
    has_warning = any(r.level == CheckLevel.WARNING for r in results)

    if not channel.level_routing.warning and has_warning and not has_critical:
        return False
    if not channel.level_routing.critical and has_critical:
        return False

    card = _build_card(results, errors, duration, node_count)

    try:
        payload = {"msg_type": "interactive", "card": card["card"]}
        if channel.signing_key:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = _sign(channel.signing_key, ts)

        resp = requests.post(channel.webhook_url, json=payload, timeout=10)
        data = resp.json()
        if data.get("code") != 0 and data.get("StatusCode") != 0:
            logger.warning("Feishu webhook returned error: %s", data)
            return False
        return True
    except Exception as exc:
        logger.error("Feishu webhook failed: %s", exc)
        return False


def send_feishu_recovery(channel: ChannelConfig, recoveries: list[dict]) -> bool:
    """Send recovery notifications to Feishu."""
    if not channel.enabled or not channel.webhook_url or not recoveries:
        return False

    card = _build_recovery_card(recoveries)
    try:
        payload = {"msg_type": "interactive", "card": card["card"]}
        if channel.signing_key:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = _sign(channel.signing_key, ts)

        resp = requests.post(channel.webhook_url, json=payload, timeout=10)
        data = resp.json()
        return data.get("code") == 0 or data.get("StatusCode") == 0
    except Exception as exc:
        logger.error("Feishu recovery webhook failed: %s", exc)
        return False
