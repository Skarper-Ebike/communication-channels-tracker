import json
from datetime import datetime, timezone

import httpx

from app.config import JiraConnectorConfig
from app.connectors.jira import JiraConnector, adf_mentions, adf_to_text

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
ME = "acc-jakub"

MYSELF = {"accountId": ME, "displayName": "Jakub"}


def adf(text, mention_id=None):
    content = [{"type": "text", "text": text}]
    if mention_id:
        content.append({"type": "mention",
                        "attrs": {"id": mention_id, "text": "@Jakub"}})
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": content}]}


def issue(key, *, summary="Some issue", assignee=ME, updated="2026-08-21T07:12:21.000+0000",
          duedate=None, priority="High", status_category="indeterminate",
          comments=()):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "assignee": {"accountId": assignee, "displayName": "X"} if assignee else None,
            "updated": updated,
            "duedate": duedate,
            "priority": {"name": priority} if priority else None,
            "status": {"name": "In Progress",
                       "statusCategory": {"key": status_category}},
            "comment": {"comments": list(comments)},
        },
    }


def connector(pages, config=None, requests_log=None):
    """pages: list of issue-lists, served in order to search calls."""
    search_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        if request.url.path.endswith("/myself"):
            return httpx.Response(200, json=MYSELF)
        if request.url.path.endswith("/search/jql"):
            i = min(search_calls["n"], len(pages) - 1)
            search_calls["n"] += 1
            body = {"issues": pages[i]}
            if i < len(pages) - 1:
                body["nextPageToken"] = f"page{i + 1}"
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    client = httpx.Client(base_url="https://example.atlassian.net",
                          transport=httpx.MockTransport(handler))
    cfg = config or JiraConnectorConfig(enabled=True,
                                        base_url="https://example.atlassian.net")
    return JiraConnector(cfg, "jakub@example.com", "token",
                         client=client, now=NOW)


def test_initial_sync_normalizes_assigned_issue():
    c = connector([[issue("ACME-1", duedate="2026-08-19")]])
    result = c.fetch_changes({})
    [a] = result.activities
    assert a.source.value == "jira"
    assert a.object_id == "ACME-1"
    assert a.assigned_to_user is True
    assert a.priority == "high"
    assert a.due_at.date().isoformat() == "2026-08-19"
    assert a.url == "https://example.atlassian.net/browse/ACME-1"
    assert result.state["last_updated_at"] == "2026-08-21T07:12:21.000+0000"
    assert result.state["assigned_keys"] == ["ACME-1"]


def test_unchanged_issue_version_skipped():
    c = connector([[issue("ACME-1")]])
    first = c.fetch_changes({})
    second = c.fetch_changes(first.state)
    assert second.activities == []


def test_new_assignment_detected_incrementally():
    state = {"account_id": ME, "display_name": "Jakub",
             "last_updated_at": "2026-08-20T00:00:00.000+0000",
             "assigned_keys": [], "seen_issue_versions": {}}
    c = connector([[issue("ACME-9")]])
    result = c.fetch_changes(state)
    events = {a.event_type for a in result.activities}
    assert "assigned" in events
    assert "ACME-9" in result.state["assigned_keys"]


def test_initial_sync_never_emits_assignment_events():
    c = connector([[issue("ACME-1")]])
    events = {a.event_type for a in c.fetch_changes({}).activities}
    assert "assigned" not in events


def test_done_issue_marks_completed():
    c = connector([[issue("ACME-1", status_category="done")]])
    [a] = c.fetch_changes({}).activities
    assert a.completed is True


def test_comments_extracted_with_mention_and_own_skipped():
    comments = [
        {"id": "1", "author": {"accountId": "acc-other", "displayName": "Priya"},
         "created": "2026-08-21T07:00:00.000+0000",
         "body": adf("Can you check this?", mention_id=ME)},
        {"id": "2", "author": {"accountId": ME, "displayName": "Jakub"},
         "created": "2026-08-21T07:05:00.000+0000",
         "body": adf("Looking now")},
        {"id": "3", "author": {"accountId": "acc-other", "displayName": "Priya"},
         "created": "2026-06-01T00:00:00.000+0000",  # before lookback window
         "body": adf("Ancient history")},
    ]
    c = connector([[issue("ACME-1", comments=comments)]])
    activities = c.fetch_changes({}).activities
    comment_activities = [a for a in activities if a.event_type == "comment"]
    [priya] = comment_activities
    assert priya.actor_name == "Priya"
    assert priya.mentions_user is True
    assert "Can you check this?" in priya.text


def test_pagination_follows_next_page_token():
    c = connector([[issue("ACME-1")], [issue("ACME-2")]])
    result = c.fetch_changes({})
    assert {a.object_id for a in result.activities} == {"ACME-1", "ACME-2"}


def test_incremental_jql_uses_cursor_with_overlap():
    log = []
    state = {"account_id": ME, "display_name": "Jakub",
             "last_updated_at": "2026-08-21T07:00:00.000+0000",
             "assigned_keys": [], "seen_issue_versions": {}}
    c = connector([[]], requests_log=log)
    c.fetch_changes(state)
    [search] = [r for r in log if r.url.path.endswith("/search/jql")]
    jql = search.url.params["jql"]
    assert 'updated >= "2026-08-21 06:55"' in jql
    assert "assignee = currentUser()" in jql


def test_projects_widen_scope():
    log = []
    cfg = JiraConnectorConfig(enabled=True,
                              base_url="https://example.atlassian.net",
                              projects=["ACME", "CRX Phase 1"])
    c = connector([[]], config=cfg, requests_log=log)
    c.fetch_changes({})
    [search] = [r for r in log if r.url.path.endswith("/search/jql")]
    # Quoted, so full project names with spaces are valid JQL too.
    assert 'project in ("ACME", "CRX Phase 1")' in search.url.params["jql"]


def test_validate_reports_identity():
    c = connector([[]])
    result = c.validate()
    assert result.ok
    assert "Jakub" in result.detail


def test_adf_flattening():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Hello "},
            {"type": "mention", "attrs": {"id": "x", "text": "@Sam"}},
        ]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Bye"}]},
    ]}
    assert adf_to_text(doc) == "Hello @Sam\nBye\n"
    assert adf_mentions(doc, "x") is True
    assert adf_mentions(doc, "y") is False


# -- re-reading issues behind open date items --------------------------------

CURSOR_STATE = {"account_id": ME, "display_name": "Jakub",
                "last_updated_at": "2026-08-21T07:00:00.000+0000",
                "assigned_keys": ["CRX-281", "CRX-333"],
                "seen_issue_versions": {
                    "CRX-281": "2026-08-12T10:00:00.000+0000",
                    "CRX-333": "2026-08-01T10:00:00.000+0000"}}


def routed_connector(*, incremental=(), by_key=None, missing=(),
                     reject_batches=False, refresh_keys=(), requests_log=None):
    """Incremental JQL serves `incremental`; `key in (...)` serves by_key."""
    by_key = by_key or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path.endswith("/myself"):
            return httpx.Response(200, json=MYSELF)
        if path.endswith("/search/jql"):
            jql = request.url.params["jql"]
            if not jql.startswith("key in"):
                return httpx.Response(200, json={"issues": list(incremental)})
            if reject_batches and missing:
                error = (f"An issue with key '{missing[0]}' does not exist "
                         "for field 'key'.")
                return httpx.Response(400, json={"errorMessages": [error]})
            wanted = [k for k in by_key if f'"{k}"' in jql]
            return httpx.Response(200, json={
                "issues": [by_key[k] for k in wanted]})
        if "/rest/api/3/issue/" in path:
            key = path.rsplit("/", 1)[-1]
            if key in by_key:
                return httpx.Response(200, json=by_key[key])
            return httpx.Response(404)
        return httpx.Response(404)

    client = httpx.Client(base_url="https://example.atlassian.net",
                          transport=httpx.MockTransport(handler))
    cfg = JiraConnectorConfig(enabled=True,
                              base_url="https://example.atlassian.net")
    return JiraConnector(cfg, "jakub@example.com", "token", client=client,
                         now=NOW, refresh_keys=refresh_keys)


def test_open_date_keys_refetched_even_when_not_updated_since_cursor():
    log = []
    moved = issue("CRX-281", summary="QA (no-lock) EVT Design",
                  updated="2026-08-12T10:00:00.000+0000",
                  duedate="2026-12-05")
    c = routed_connector(by_key={"CRX-281": moved},
                         refresh_keys=["CRX-281"], requests_log=log)
    result = c.fetch_changes(CURSOR_STATE)

    [a] = result.activities
    assert a.object_id == "CRX-281"
    assert a.snapshot is True
    assert a.assigned_to_user is True
    assert a.title == "QA (no-lock) EVT Design"
    assert a.due_at.date().isoformat() == "2026-12-05"
    # A current-state read only: cursor and bookkeeping are untouched.
    assert result.state["last_updated_at"] == CURSOR_STATE["last_updated_at"]
    assert result.state["seen_issue_versions"] == \
        CURSOR_STATE["seen_issue_versions"]
    assert result.state["assigned_keys"] == CURSOR_STATE["assigned_keys"]

    refresh = [r for r in log if r.url.path.endswith("/search/jql")
               and r.url.params["jql"].startswith("key in")]
    assert [r.url.params["jql"] for r in refresh] == ['key in ("CRX-281")']
    assert refresh[0].url.params["maxResults"] == "100"
    assert refresh[0].url.params["fields"] == \
        "summary,assignee,duedate,priority,status,issuetype,created,updated,comment"


def test_refetched_issue_never_emits_assignment_event():
    c = routed_connector(by_key={"CRX-9": issue("CRX-9", duedate="2026-08-01")},
                         refresh_keys=["CRX-9"])
    state = dict(CURSOR_STATE, assigned_keys=[])
    events = {a.event_type for a in c.fetch_changes(state).activities}
    assert events == {"issue_updated"}


def test_key_already_in_incremental_pass_is_not_refetched():
    log = []
    fresh = issue("CRX-333", updated="2026-08-21T07:30:00.000+0000",
                  duedate="2026-08-11")
    c = routed_connector(incremental=[fresh], refresh_keys=["CRX-333"],
                         requests_log=log)
    result = c.fetch_changes(CURSOR_STATE)
    assert [a.object_id for a in result.activities] == ["CRX-333"]
    assert not any(r.url.params.get("jql", "").startswith("key in")
                   for r in log)


def test_refresh_keys_batched_at_100():
    log = []
    keys = [f"CRX-{n}" for n in range(1, 151)]
    c = routed_connector(refresh_keys=keys, requests_log=log)
    c.fetch_changes(CURSOR_STATE)
    batches = [r.url.params["jql"] for r in log
               if r.url.params.get("jql", "").startswith("key in")]
    assert len(batches) == 2
    assert batches[0].count('"CRX-') == 100
    assert batches[1].count('"CRX-') == 50


def test_deleted_key_falls_back_to_per_issue_reads():
    # JQL rejects a whole `key in` batch over one deleted key; the others
    # must still refresh rather than the connector failing every run.
    alive = issue("CRX-333", duedate="2026-12-05")
    c = routed_connector(by_key={"CRX-333": alive}, missing=["CRX-404"],
                         reject_batches=True,
                         refresh_keys=["CRX-333", "CRX-404"])
    result = c.fetch_changes(CURSOR_STATE)
    assert [a.object_id for a in result.activities] == ["CRX-333"]
