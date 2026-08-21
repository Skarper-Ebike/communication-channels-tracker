from datetime import datetime, timezone

import httpx

from app.config import SlackConnectorConfig
from app.connectors.slack import SlackConnector

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
ME = "U_ME"

AUTH_TEST = {"ok": True, "user_id": ME, "user": "jakub",
             "url": "https://skarper.slack.com/"}
USERS = {
    "U_JAMES": {"ok": True, "user": {"id": "U_JAMES", "real_name": "James",
                                     "profile": {"display_name": "James"}}},
}


def make_connector(history_by_channel, replies=None, channels=("C_PROD",),
                   output_channel=None, requests_log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path.endswith("auth.test"):
            return httpx.Response(200, json=AUTH_TEST)
        if path.endswith("users.info"):
            user = request.url.params["user"]
            return httpx.Response(200, json=USERS.get(
                user, {"ok": True, "user": {"id": user, "real_name": user}}))
        if path.endswith("conversations.history"):
            channel = request.url.params["channel"]
            return httpx.Response(200, json={
                "ok": True, "messages": history_by_channel.get(channel, [])})
        if path.endswith("conversations.replies"):
            return httpx.Response(200, json={
                "ok": True, "messages": replies or []})
        return httpx.Response(404)

    client = httpx.Client(base_url="https://slack.com/api",
                          transport=httpx.MockTransport(handler))
    config = SlackConnectorConfig(enabled=True, channels=list(channels))
    return SlackConnector(config, "xoxp-test", output_channel_id=output_channel,
                          client=client, now=NOW)


def msg(ts, user="U_JAMES", text="hello", **extra):
    return {"ts": ts, "user": user, "text": text, **extra}


def test_message_normalized_with_mention_and_permalink():
    c = make_connector({"C_PROD": [
        msg("1787280000.000100", text=f"<@{ME}> can you approve the launch?"),
    ]})
    result = c.fetch_changes({})
    [a] = result.activities
    assert a.actor_name == "James"
    assert a.mentions_user is True
    assert a.directed_at_user is True
    # Raw <@U…> mention resolved to a readable @name
    assert a.text == "@U_ME can you approve the launch?"
    assert a.url == ("https://skarper.slack.com/archives/C_PROD/"
                     "p1787280000000100")


def test_own_bot_and_subtype_messages_skipped():
    c = make_connector({"C_PROD": [
        msg("1787280000.1", user=ME, text="my own message"),
        msg("1787280000.2", user="U_JAMES", bot_id="B123", text="bot noise"),
        {"ts": "1787280000.3", "user": "U_JAMES", "subtype": "channel_join",
         "text": "joined"},
        msg("1787280000.4", text="real message"),
    ]})
    result = c.fetch_changes({})
    assert [a.text for a in result.activities] == ["real message"]


def test_output_channel_never_polled():
    log = []
    c = make_connector({"C_AGENT": [msg("1.0", text="briefing echo")]},
                       channels=("C_AGENT",), output_channel="C_AGENT",
                       requests_log=log)
    result = c.fetch_changes({})
    assert result.activities == []
    assert not any(r.url.path.endswith("conversations.history") for r in log)


def test_cursor_advances_and_is_used_next_run():
    log = []
    c = make_connector({"C_PROD": [msg("1787280000.000200")]},
                       requests_log=log)
    result = c.fetch_changes({})
    assert result.state["channels"]["C_PROD"]["last_timestamp"] == \
        "1787280000.000200"

    log.clear()
    c.fetch_changes(result.state)
    [history] = [r for r in log
                 if r.url.path.endswith("conversations.history")]
    assert history.url.params["oldest"] == "1787280000.000200"


def test_reply_after_user_contribution_is_directed():
    parent = msg("100.0", text="thread start",
                 reply_count=2, latest_reply="300.0")
    thread = [
        parent,
        {"ts": "200.0", "user": ME, "text": "I'll check tomorrow"},
        msg("300.0", text="any update?"),
    ]
    c = make_connector({"C_PROD": [parent]}, replies=thread)
    result = c.fetch_changes({"user_id": ME, "team_domain": "skarper",
                              "channels": {"C_PROD": {"last_timestamp": "150.0"}}})
    reply = [a for a in result.activities if a.event_type == "reply"]
    assert len(reply) == 1
    assert reply[0].directed_at_user is True
    assert reply[0].thread_id == "100.0"


def test_reply_in_foreign_thread_not_directed():
    parent = msg("100.0", text="thread start",
                 reply_count=1, latest_reply="300.0")
    thread = [parent, msg("300.0", user="U_OTHER", text="update")]
    c = make_connector({"C_PROD": [parent]}, replies=thread)
    result = c.fetch_changes({"user_id": ME, "team_domain": "skarper",
                              "channels": {"C_PROD": {"last_timestamp": "150.0"}}})
    [reply] = [a for a in result.activities if a.event_type == "reply"]
    assert reply.directed_at_user is False


def test_validate_reports_identity():
    c = make_connector({})
    result = c.validate()
    assert result.ok and "jakub" in result.detail
