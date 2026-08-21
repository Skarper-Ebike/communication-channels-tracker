from datetime import datetime, timezone

import httpx

from app.config import BasecampConnectorConfig
from app.connectors.basecamp import BasecampConnector, _strip_html

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
MY_ID = 111
BASE = "https://basecamp.com/999/api/v1"

AUTHORIZATION = {
    "identity": {"id": 1, "email_address": "jakub@skarper.com"},
    "accounts": [
        {"product": "bcx", "id": 999, "name": "Skarper", "href": BASE},
        {"product": "campfire", "id": 5, "href": "https://x.campfirenow.com"},
    ],
}
PROFILE = {"id": MY_ID, "name": "Jakub Szypicyn",
           "email_address": "jakub@skarper.com"}


class FakeTokens:
    def access_token(self):
        return "tok"

    def refresh(self):
        pass

    def connected(self):
        return True


def todo(rid, *, due_on="2026-08-19", completed=False,
         updated="2026-08-21T07:00:00+00:00"):
    return {"id": rid, "content": "Approve homepage design",
            "due_on": due_on, "due_at": None, "completed": completed,
            "updated_at": updated,
            "app_url": f"https://basecamp.com/999/projects/1/todos/{rid}"}


def event(eid, *, action="commented on", etype="Message", creator_id=222,
          creator="James", excerpt="<div>Can Jakub Szypicyn approve this?</div>",
          created="2026-08-21T07:30:00+00:00", target="Homepage design"):
    return {"id": eid, "created_at": created, "action": action,
            "target": target, "summary": f"{action} {target}",
            "excerpt": excerpt, "raw_excerpt": excerpt,
            "creator": {"id": creator_id, "name": creator},
            "eventable": {"id": eid * 10, "type": etype},
            "html_url": f"https://basecamp.com/999/projects/1/messages/{eid}"}


def make_connector(assigned=(), events=(), requests_log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path.endswith("/authorization.json"):
            return httpx.Response(200, json=AUTHORIZATION)
        if path.endswith("/people/me.json"):
            return httpx.Response(200, json=PROFILE)
        if path.endswith(f"/people/{MY_ID}/assigned_todos.json"):
            return httpx.Response(200, json=[
                {"id": 1, "name": "Launch list",
                 "assigned_todos": list(assigned)}])
        if path.endswith("/events.json"):
            return httpx.Response(200, json=list(events))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BasecampConnector(BasecampConnectorConfig(enabled=True),
                             token_store=FakeTokens(), client=client, now=NOW)


def test_discovers_bcx_account_and_person():
    c = make_connector()
    result = c.fetch_changes({})
    assert result.state["base_url"] == BASE
    assert result.state["person_id"] == MY_ID


def test_assigned_todo_is_deterministic_signal():
    c = make_connector(assigned=[todo(9912)])
    result = c.fetch_changes({})
    [a] = [x for x in result.activities if x.object_type == "todo"]
    assert a.assigned_to_user is True
    assert a.due_at.date().isoformat() == "2026-08-19"
    assert a.completed is False
    assert a.title == "Approve homepage design"
    assert a.url.endswith("/todos/9912")


def test_comment_event_by_other_with_name_mention():
    c = make_connector(events=[event(50)])
    [a] = c.fetch_changes({}).activities
    assert a.event_type == "comment"
    assert a.actor_name == "James"
    assert a.mentions_user is True
    assert "approve this?" in a.text
    assert a.title == "Homepage design"
    assert a.url.endswith("/messages/50")


def test_own_events_skipped():
    c = make_connector(events=[event(51, creator_id=MY_ID,
                                     creator="Jakub Szypicyn")])
    assert c.fetch_changes({}).activities == []


def test_todo_completed_event_resolves_even_when_own():
    done = event(60, action="completed", etype="Todo", creator_id=MY_ID,
                 creator="Jakub Szypicyn", excerpt="", target="Old task")
    c = make_connector(events=[done])
    [a] = c.fetch_changes({}).activities
    assert a.event_type == "todo_completed"
    assert a.completed is True
    assert a.object_id == "600"  # the eventable todo id, not the event id


def test_cursor_is_max_event_created_at_and_since_has_overlap():
    log = []
    c = make_connector(events=[event(1, created="2026-08-21T07:45:00+00:00")],
                       requests_log=log)
    state = {"base_url": BASE, "person_id": MY_ID,
             "person_name": "Jakub Szypicyn",
             "last_sync": "2026-08-21T07:00:00+00:00"}
    result = c.fetch_changes(state)
    assert result.state["last_sync"] == "2026-08-21T07:45:00+00:00"

    [events_req] = [r for r in log if r.url.path.endswith("/events.json")]
    assert events_req.url.params["since"] == "2026-08-21T06:55:00+00:00"


def test_validate_requires_bcx_account():
    c = make_connector()
    result = c.validate()
    assert result.ok and "jakub@skarper.com" in result.detail


def test_strip_html():
    assert _strip_html("<div>Hello <b>world</b></div><p>bye</p>") == \
        "Hello world\nbye"
