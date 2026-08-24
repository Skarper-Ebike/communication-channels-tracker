"""Slack delivery via the user's own incoming webhook.

The webhook URL is a secret: read from the environment (loaded from
.local/.env), sent nowhere else, never logged. Transient failures retry up
to 3 times with backoff, honouring Retry-After.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from app.config import AppConfig

log = logging.getLogger("work-agent")

MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 15


class SlackDeliveryError(Exception):
    pass


def webhook_url(config: AppConfig) -> str | None:
    return os.environ.get(config.slack_output.webhook_env) or None


def post_message(text: str, config: AppConfig,
                 client: httpx.Client | None = None,
                 blocks: list[dict] | None = None) -> None:
    """Post to the user's private channel. Raises SlackDeliveryError.

    With `blocks`, Slack renders the Block Kit layout and `text` becomes the
    notification fallback line.
    """
    url = webhook_url(config)
    if not url:
        raise SlackDeliveryError(
            f"no webhook configured (set {config.slack_output.webhook_env} "
            f"in .local/.env)")

    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        last_error = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = None
            try:
                response = client.post(url, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"network error: {type(exc).__name__}"
            else:
                if response.status_code == 200:
                    log.info("Slack delivery ok (%d chars)", len(text))
                    return
                last_error = f"HTTP {response.status_code}"
                retryable = response.status_code in (408, 429) or \
                    response.status_code >= 500
                if not retryable:
                    break
            if attempt < MAX_ATTEMPTS:
                time.sleep(_backoff_seconds(response, attempt))
        raise SlackDeliveryError(f"Slack delivery failed: {last_error}")
    finally:
        if own_client:
            client.close()


def _backoff_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
    return 2.0 ** attempt
