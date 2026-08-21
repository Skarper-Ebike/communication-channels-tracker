"""Shared HTTP retry helper for connector APIs.

Max 3 attempts, exponential backoff, Retry-After honoured. Raises the last
error so callers (the runner) can mark the connector degraded without
advancing its cursor.
"""

from __future__ import annotations

import time

import httpx

MAX_ATTEMPTS = 3


class TransientAPIError(Exception):
    pass


def get_with_retry(client: httpx.Client, url: str,
                   params: dict | None = None) -> httpx.Response:
    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            last_error = f"network error: {type(exc).__name__}"
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code} for {url}"
            retryable = response.status_code in (408, 429) or \
                response.status_code >= 500
            if not retryable:
                response.raise_for_status()
        if attempt < MAX_ATTEMPTS:
            time.sleep(_backoff_seconds(response, attempt))
    raise TransientAPIError(last_error)


def _backoff_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
    return 2.0 ** attempt
