from datetime import datetime, timezone

import httpx

from app.config import BasecampConnectorConfig
from app.connectors.basecamp import BasecampConnector, _strip_html

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
MY_ID = 111

AUTHORIZATION = {
    "identity": {"id": 1, "email_address": "jakub@skarper.com"},
    "accounts": [{"product": "bc3", "id": 999, "name": "Skarper",
                  "href": "https://3.basecampapi.com/999"}],
}
PROFILE = {"id": MY_ID, "name": "Jakub Szypicyn"}


class FakeTokens:
    def access_token(self):
        return "tok"

    def refresh(self):
        pass

    def connected(self):
        return True


def todo(rid, *, assignee=MY_ID, due_on="2026-08-19", completed=False,
         updated="2026-08-21T07:00:00.000Z"):
    return {
        "id": rid, "type": "Todo", "updated_at": updated,
        "title": "Approve homepage design",
        "content": "Approve homepage design",
        "description": "<p>Blocked on your sign-off</p>",
        "assignees": [{"id": assignee, "name": "X"}] if assignee else [],
        "due_on": due_on, "completed": completed,
        "app_url": f"https://3.basecamp.com/999/buckets/1/todos/{rid}",
        "creator": {"id": 222, "name": "James"},
        "bucket": {"id": 1, "name": "Atlas"},
        "parent": {"id": 5, "title": "Launch list"},
    }


def comment(rid, *, creator_id=222, creator="James",
            content="<div>Can Jakub Szypicyn approve this?</div>",
            updated="2026-08-21T07:30:00.000Z"):
    return {
        "id": rid, "type": "Comment", "updated_at": updated,
        "title": "Re: Homepage design", "content": content,
        "app_url": f"https://3.basecamp.com/999/buckets/1/comments/{rid}",
        "creator": {"id": creator_id, "name": creator},
        "parent": {"id": 5, "title": "Homepage design"},
    }


def make_connector(recordings_by_type, requests_log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path.endswith("/authorization.json"):
            return httpx.Response(200, json=AUTHORIZATION)
        if path.endswith("/my/profile.json"):
            return httpx.Response(200, json=PROFILE)
        if path.endswith("/projects/recordings.json"):
            rtype = request.url.params["type"]
            return httpx.Response(200, json=recordings_by_type.get(rtype, []))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BasecampConnector(BasecampConnectorConfig(enabled=True),
                             token_store=FakeTokens(), client=client, now=NOW)


def test_discovers_account_and_person_then_normalizes_todo():
    c = make_connector({"Todo": [todo(9912)]})
    result = c.fetch_changes({})
    assert result.state["account_id"] == "999"
    assert result.state["person_id"] == MY_ID

    [a] = result.activities
    assert a.source.value == "basecamp"
    assert a.assigned_to_user is True
    assert a.due_at.date().isoformat() == "2026-08-19"
    assert a.completed is False
    assert "sign-off" in a.text
    assert a.url.endswith("/todos/9912")


def test_unassigned_todo_not_flagged():
    c = make_connector({"Todo": [todo(1, assignee=333)]})
    [a] = c.fetch_changes({}).activities
    assert a.assigned_to_user is False


def test_comment_by_other_with_name_mention():
    c = make_connector({"Comment": [comment(50)]})
    [a] = c.fetch_changes({}).activities
    assert a.event_type == "comment"
    assert a.actor_name == "James"
    assert a.mentions_user is True
    assert "approve this?" in a.text
    assert a.title == "Re: Homepage design"


def test_own_comment_skipped():
    c = make_connector({"Comment": [comment(51, creator_id=MY_ID,
                                            creator="Jakub Szypicyn")]})
    assert c.fetch_changes({}).activities == []


def test_recordings_older_than_floor_stop_the_page():
    old = todo(1, updated="2026-08-01T00:00:00.000Z")  # beyond 7d lookback
    fresh = todo(2, updated="2026-08-21T07:00:00.000Z")
    c = make_connector({"Todo": [fresh, old]})
    result = c.fetch_changes({})
    assert [a.object_id for a in result.activities] == ["2"]


def test_cursor_is_max_updated_at():
    c = make_connector({
        "Todo": [todo(1, updated="2026-08-21T07:00:00.000Z")],
        "Comment": [comment(2, updated="2026-08-21T07:45:00.000Z")],
    })
    result = c.fetch_changes({})
    assert result.state["last_sync"] == "2026-08-21T07:45:00.000Z"


def test_incremental_uses_overlapped_floor():
    state = {"account_id": "999", "person_id": MY_ID,
             "person_name": "Jakub Szypicyn",
             "last_sync": "2026-08-21T07:00:00.000Z"}
    boundary = comment(3, updated="2026-08-21T06:54:00.000Z")  # before overlap
    c = make_connector({"Comment": [boundary]})
    assert c.fetch_changes(state).activities == []


def test_validate_reports_identity():
    c = make_connector({})
    result = c.validate()
    assert result.ok and "jakub@skarper.com" in result.detail


def test_strip_html():
    assert _strip_html("<div>Hello <b>world</b></div><p>bye</p>") == \
        "Hello world\nbye"
