import json

import httpx
import pytest

from app.config import AppConfig
from app.output import slack


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("WORK_AGENT_SLACK_WEBHOOK",
                       "https://hooks.slack.com/services/T/B/X")
    return AppConfig()


def test_posts_text_payload(config):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, text="ok")

    slack.post_message("hello briefing", config, client=make_client(handler))
    assert seen["url"] == "https://hooks.slack.com/services/T/B/X"
    assert seen["body"] == {"text": "hello briefing"}


def test_missing_webhook_raises(monkeypatch):
    monkeypatch.delenv("WORK_AGENT_SLACK_WEBHOOK", raising=False)
    with pytest.raises(slack.SlackDeliveryError, match="no webhook"):
        slack.post_message("x", AppConfig())


def test_retries_on_500_then_succeeds(config, monkeypatch):
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500) if len(calls) < 3 else httpx.Response(200)

    slack.post_message("x", config, client=make_client(handler))
    assert len(calls) == 3


def test_gives_up_after_three_attempts(config, monkeypatch):
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(slack.SlackDeliveryError, match="HTTP 500"):
        slack.post_message("x", config, client=make_client(handler))
    assert len(calls) == 3


def test_non_retryable_4xx_fails_immediately(config, monkeypatch):
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, text="no_service")

    with pytest.raises(slack.SlackDeliveryError, match="HTTP 404"):
        slack.post_message("x", config, client=make_client(handler))
    assert len(calls) == 1


def test_respects_retry_after(config, monkeypatch):
    sleeps = []
    monkeypatch.setattr(slack.time, "sleep", lambda s: sleeps.append(s))
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200)

    slack.post_message("x", config, client=make_client(handler))
    assert sleeps == [7.0]
