from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse

from node_health_watcher.alert.dingtalk import _sign as sign_dingtalk
from node_health_watcher.alert.feishu import _sign as sign_feishu


def test_feishu_sign_uses_timestamp_secret_as_hmac_key_and_base64_output():
    timestamp = 1_716_540_000
    secret = "test-secret"

    expected = base64.b64encode(
        hmac.new(
            f"{timestamp}\n{secret}".encode(),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    assert sign_feishu(secret, timestamp) == expected


def test_dingtalk_sign_uses_secret_key_and_url_encoded_base64_output():
    timestamp = "1716540000000"
    secret = "test-secret"

    expected = urllib.parse.quote_plus(
        base64.b64encode(
            hmac.new(
                secret.encode(),
                f"{timestamp}\n{secret}".encode(),
                digestmod=hashlib.sha256,
            ).digest()
        )
    )

    assert sign_dingtalk(secret, timestamp) == expected
