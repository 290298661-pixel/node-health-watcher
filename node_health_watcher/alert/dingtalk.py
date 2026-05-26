from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import requests

from node_health_watcher.alert.common import threshold_label
from node_health_watcher.checks.base import CheckLevel, CheckResult
from node_health_watcher.config import ChannelConfig

logger = logging.getLogger(__name__)


def _build_markdown(results: list[CheckResult], errors: list, duration: float, node_count: int) -> str:
    criticals = [r for r in results if r.level == CheckLevel.CRITICAL]
    warnings = [r for r in results if r.level == CheckLevel.WARNING]
    healthy_count = node_count - len({r.hostname for r in criticals + warnings})

    lines = [
        "## 🏥 K8s 节点健康巡检",
        "",
    ]

    if criticals:
        lines.append(f"### 🔴 CRITICAL ({len(criticals)})")
        for r in criticals:
            label = threshold_label(r)
            lines.append(f"- **[{r.hostname}]** {r.category}: {r.message}{label}")
        lines.append("")

    if warnings:
        lines.append(f"### ⚠️ WARNING ({len(warnings)})")
        for r in warnings:
            label = threshold_label(r)
            lines.append(f"- **[{r.hostname}]** {r.category}: {r.message}{label}")
        lines.append("")

    footer = [f"✅ 正常: {healthy_count} 个节点"]
    if errors:
        footer.append(f"❌ 连接失败: {len(errors)} 个节点")
    footer.append(f"📊 巡检耗时: {duration:.1f}s")
    lines.append("  \n".join(footer))

    return "\n".join(lines)


def _build_recovery_markdown(recoveries: list[dict]) -> str:
    lines = [
        "## ✅ 节点健康恢复通知",
        "",
    ]
    for rec in recoveries:
        lines.append(f"- **[{rec['hostname']}]** {rec['category']}: {rec['message']}")
    return "\n".join(lines)


def _sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        key=secret.encode("utf-8"),
        msg=string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return urllib.parse.quote_plus(base64.b64encode(hmac_code.digest()))


def send_dingtalk(
    channel: ChannelConfig,
    results: list[CheckResult],
    errors: list,
    duration: float,
    node_count: int,
) -> bool:
    """Send inspection results to DingTalk bot. Returns True on success."""
    if not channel.enabled or not channel.webhook_url:
        return False

    has_critical = any(r.level == CheckLevel.CRITICAL for r in results)
    has_warning = any(r.level == CheckLevel.WARNING for r in results)

    if not channel.level_routing.warning and has_warning and not has_critical:
        return False
    if not channel.level_routing.critical and has_critical:
        return False

    markdown_text = _build_markdown(results, errors, duration, node_count)

    try:
        payload: dict = {
            "msgtype": "markdown",
            "markdown": {
                "title": "K8s 节点健康巡检",
                "text": markdown_text,
            },
        }
        if channel.signing_key:
            ts = str(round(time.time() * 1000))
            sign = _sign(channel.signing_key, ts)
            webhook_url = channel.webhook_url
            if "?" in webhook_url:
                webhook_url += f"&timestamp={ts}&sign={sign}"
            else:
                webhook_url += f"?timestamp={ts}&sign={sign}"
        else:
            webhook_url = channel.webhook_url

        resp = requests.post(webhook_url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.warning("DingTalk webhook returned error: %s", data)
            return False
        return True
    except Exception as exc:
        logger.error("DingTalk webhook failed: %s", exc)
        return False


def send_dingtalk_recovery(channel: ChannelConfig, recoveries: list[dict]) -> bool:
    """Send recovery notifications to DingTalk."""
    if not channel.enabled or not channel.webhook_url or not recoveries:
        return False

    markdown_text = _build_recovery_markdown(recoveries)
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "节点健康恢复",
                "text": markdown_text,
            },
        }
        webhook_url = channel.webhook_url
        if channel.signing_key:
            ts = str(round(time.time() * 1000))
            sign = _sign(channel.signing_key, ts)
            if "?" in webhook_url:
                webhook_url += f"&timestamp={ts}&sign={sign}"
            else:
                webhook_url += f"?timestamp={ts}&sign={sign}"

        resp = requests.post(webhook_url, json=payload, timeout=10)
        data = resp.json()
        return data.get("errcode") == 0
    except Exception as exc:
        logger.error("DingTalk recovery webhook failed: %s", exc)
        return False
