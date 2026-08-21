from datetime import datetime, timezone

import httpx
import pytest

from app.config import GoogleDriveConnectorConfig
from app.connectors.google_drive import GoogleDriveConnector

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
ME = "jakub@skarper.com"

ABOUT = {"user": {"emailAddress": ME, "displayName": "Jakub"}}

DECK = {"id": "deck1", "name": "Q3 Board Deck",
        "mimeType": "application/vnd.google-apps.presentation",
        "modifiedTime": "2026-08-21T06:00:00.000Z",
        "webViewLink": "https://docs.google.com/presentation/d/deck1",
        "lastModifyingUser": {"displayName": "Lucy",
                              "emailAddress": "lucy@skarper.com"}}


class FakeTokens:
    def __init__(self):
        self.current = "tok1"
        self.refreshes = 0

    def access_token(self):
        return self.current

    def refresh(self):
        self.refreshes += 1
        self.current = "tok2"

    def connected(self):
        return True


def make_connector(changes=(), comments=(), start_token="99",
                   requests_log=None, fail_first_with_401=False):
    state401 = {"failed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        if fail_first_with_401 and not state401["failed"]:
            state401["failed"] = True
            return httpx.Response(401)
        path = request.url.path
        if path.endswith("/about"):
            return httpx.Response(200, json=ABOUT)
        if path.endswith("/changes/startPageToken"):
            return httpx.Response(200, json={"startPageToken": start_token})
        if path.endswith("/changes"):
            return httpx.Response(200, json={
                "newStartPageToken": "100", "changes": list(changes)})
        if "/comments" in path:
            return httpx.Response(200, json={"comments": list(comments)})
        return httpx.Response(404)

    client = httpx.Client(base_url="https://www.googleapis.com",
                          transport=httpx.MockTransport(handler))
    tokens = FakeTokens()
    connector = GoogleDriveConnector(GoogleDriveConnectorConfig(enabled=True),
                                     token_store=tokens, client=client, now=NOW)
    return connector, tokens


def synced_state():
    return {"version": 1, "email": ME, "name": "Jakub",
            "start_page_token": "99", "last_sync": "2026-08-20T08:30:00+00:00"}


def test_first_run_initialises_cursor_without_activities():
    c, _ = make_connector()
    result = c.fetch_changes({})
    assert result.activities == []
    assert result.state["start_page_token"] == "99"
    assert result.state["email"] == ME


def test_change_by_someone_else_surfaces():
    c, _ = make_connector(changes=[{"fileId": "deck1", "file": DECK}])
    result = c.fetch_changes(synced_state())
    changed = [a for a in result.activities if a.event_type == "file_changed"]
    [a] = changed
    assert a.title == "Q3 Board Deck"
    assert a.actor_name == "Lucy"
    assert result.state["start_page_token"] == "100"


def test_own_change_skipped():
    mine = {**DECK, "lastModifyingUser": {"displayName": "Jakub",
                                          "emailAddress": ME}}
    c, _ = make_connector(changes=[{"fileId": "deck1", "file": mine}])
    result = c.fetch_changes(synced_state())
    assert [a for a in result.activities if a.event_type == "file_changed"] == []


def test_comments_and_directed_replies():
    comments = [{
        "id": "c1",
        "author": {"displayName": "Jakub", "emailAddress": ME},
        "content": "Is this number final?",
        "modifiedTime": "2026-08-21T06:10:00.000Z",
        "quotedFileContent": {"value": "revenue slide"},
        "replies": [
            {"id": "r1", "author": {"displayName": "Lucy",
                                    "emailAddress": "lucy@skarper.com"},
             "content": "Confirming with finance today.",
             "modifiedTime": "2026-08-21T07:00:00.000Z"},
        ],
    }]
    c, _ = make_connector(changes=[{"fileId": "deck1", "file": DECK}],
                          comments=comments)
    result = c.fetch_changes(synced_state())
    replies = [a for a in result.activities if a.event_type == "reply"]
    [reply] = replies
    assert reply.actor_name == "Lucy"
    assert reply.directed_at_user is True  # reply to the user's own comment
    # The user's own comment itself is not surfaced.
    assert [a for a in result.activities if a.event_type == "comment"] == []


def test_old_comments_below_floor_skipped():
    comments = [{
        "id": "c1", "author": {"displayName": "Lucy",
                               "emailAddress": "lucy@skarper.com"},
        "content": "old note",
        "modifiedTime": "2026-08-19T00:00:00+00:00",
        "replies": [],
    }]
    c, _ = make_connector(changes=[{"fileId": "deck1", "file": DECK}],
                          comments=comments)
    result = c.fetch_changes(synced_state())
    assert [a for a in result.activities if a.event_type == "comment"] == []


def test_401_triggers_single_refresh_and_retry():
    c, tokens = make_connector(fail_first_with_401=True)
    result = c.fetch_changes({})
    assert tokens.refreshes == 1
    assert result.state["start_page_token"] == "99"


def test_validate_reports_email():
    c, _ = make_connector()
    result = c.validate()
    assert result.ok and ME in result.detail
