"""Local OAuth: a one-shot localhost callback plus a refreshing token store.

The callback server lives only for the seconds between opening the browser
and receiving the authorization code — there is no persistent server (§17).
Tokens are stored under .local/secrets/ with owner-only permissions and
refreshed with the user's own client credentials.
"""

from __future__ import annotations

import json
import logging
import os
import secrets as pysecrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

log = logging.getLogger("work-agent")

CALLBACK_TIMEOUT_SECONDS = 300


class OAuthError(Exception):
    pass


@dataclass
class OAuthApp:
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=list)
    extra_auth_params: dict = field(default_factory=dict)


def run_local_flow(app: OAuthApp) -> dict:
    """Open the browser, catch the redirect once, exchange the code.

    Returns the token response dict with an added `expires_at` epoch.
    """
    state = pysecrets.token_urlsafe(24)
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            params = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query)
            result["code"] = params.get("code", [None])[0]
            result["state"] = params.get("state", [None])[0]
            result["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "Personal Work Agent: authentication received. "
                "You can close this tab.".encode())

        def log_message(self, *args):  # quiet
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = CALLBACK_TIMEOUT_SECONDS
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"

    query = {
        "client_id": app.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        **app.extra_auth_params,
    }
    if app.scopes:
        query["scope"] = " ".join(app.scopes)
    url = f"{app.auth_url}?{urllib.parse.urlencode(query)}"

    print("Opening your browser to authenticate…")
    print(f"If it does not open, visit:\n  {url}")
    webbrowser.open(url)
    try:
        server.handle_request()  # exactly one request, then done
    finally:
        server.server_close()

    if result.get("error"):
        raise OAuthError(f"authorization denied: {result['error']}")
    if not result.get("code"):
        raise OAuthError("no authorization code received (timed out?)")
    if result.get("state") != state:
        raise OAuthError("state mismatch — possible interception; aborting")

    return exchange_code(app, result["code"], redirect_uri)


def exchange_code(app: OAuthApp, code: str, redirect_uri: str) -> dict:
    response = httpx.post(app.token_url, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": app.client_id,
        "client_secret": app.client_secret,
    }, timeout=30)
    if response.status_code != 200:
        raise OAuthError(f"token exchange failed: HTTP {response.status_code}")
    tokens = response.json()
    tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
    return tokens


class TokenStore:
    """Loads, refreshes, and persists one OAuth token file."""

    EXPIRY_SKEW_SECONDS = 60

    def __init__(self, path: Path, token_url: str,
                 client_id: str, client_secret: str):
        self.path = path
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._tokens: dict | None = None

    def connected(self) -> bool:
        return self._load() is not None

    def access_token(self) -> str:
        tokens = self._load()
        if tokens is None:
            raise OAuthError(
                f"not authenticated ({self.path.name} missing) — run: "
                "uv run python -m app.cli reconnect "
                f"{self.path.stem.removesuffix('-token')}")
        if time.time() > tokens.get("expires_at", 0) - self.EXPIRY_SKEW_SECONDS:
            self.refresh()
            tokens = self._tokens
        return tokens["access_token"]

    def refresh(self) -> None:
        tokens = self._load()
        if tokens is None or not tokens.get("refresh_token"):
            raise OAuthError("no refresh token — re-run reconnect")
        response = httpx.post(self.token_url, data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=30)
        if response.status_code != 200:
            raise OAuthError(
                f"token refresh failed (HTTP {response.status_code}) — "
                "re-run reconnect")
        fresh = response.json()
        fresh["expires_at"] = time.time() + float(fresh.get("expires_in", 3600))
        # Providers may omit the refresh token on refresh; keep the old one.
        fresh.setdefault("refresh_token", tokens["refresh_token"])
        self.save(fresh)

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        os.chmod(self.path, 0o600)
        self._tokens = tokens

    def _load(self) -> dict | None:
        if self._tokens is None:
            try:
                self._tokens = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return None
        return self._tokens
