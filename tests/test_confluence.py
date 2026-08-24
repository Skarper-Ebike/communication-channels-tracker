from datetime import datetime, timezone

import httpx

from app.config import ConfluenceConnectorConfig
from app.connectors.confluence import ConfluenceConnector

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)
ME = "acc-jakub"
SITE = "https://skarper.atlassian.net"

CURRENT_USER = {"accountId": ME, "displayName": "Jakub Szypicyn"}


def page(cid, *, title="ESC test plan", author="acc-lucy",
         author_name="Lucy", when="2026-08-21T07:00:00.000Z", version=4):
    return {
        "id": str(cid), "type": "page", "title": title,
        "space": {"key": "ENG"},
        "version": {"number": version, "when": when,
                    "by": {"accountId": author, "displayName": author_name}},
        "_links": {"webui": f"/spaces/ENG/pages/{cid}"},
    }


def comment(cid, *, body="<p>Numbers look wrong here.</p>", author="acc-lucy",
            author_name="Lucy", when="2026-08-21T07:30:00.000Z",
            container_id="100", container_title="ESC test plan"):
    return {
        "id": str(cid), "type": "comment", "title": f"Re: {container_title}",
        "space": {"key": "ENG"},
        "version": {"number": 1, "when": when,
                    "by": {"accountId": author, "displayName": author_name}},
        "container": {"id": container_id, "title": container_title},
        "body": {"storage": {"value": body}},
        "_links": {"webui": f"/spaces/ENG/pages/{container_id}?focusedCommentId={cid}"},
    }


def make_connector(pages=(), comments=(), spaces=(), requests_log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path.endswith("/user/current"):
            return httpx.Response(200, json=CURRENT_USER)
        if path.endswith("/content/search"):
            cql = request.url.params["cql"]
            results = list(comments) if "type = comment" in cql else list(pages)
            return httpx.Response(200, json={"results": results,
                                             "size": len(results)})
        return httpx.Response(404)

    client = httpx.Client(base_url=SITE,
                          transport=httpx.MockTransport(handler))
    config = ConfluenceConnectorConfig(enabled=True, base_url=SITE,
                                       spaces=list(spaces))
    return ConfluenceConnector(config, "jakub@skarper.com", "token",
                               client=client, now=NOW)


def test_page_update_by_other_normalized():
    c = make_connector(pages=[page(100)])
    result = c.fetch_changes({})
    [a] = result.activities
    assert a.source.value == "confluence"
    assert a.event_type == "page_updated"
    assert a.actor_name == "Lucy"
    assert a.title == "ESC test plan"
    assert a.url == f"{SITE}/wiki/spaces/ENG/pages/100"
    assert result.state["last_sync"] == "2026-08-21T07:00:00.000Z"


def test_own_edits_and_comments_skipped():
    c = make_connector(pages=[page(1, author=ME)],
                       comments=[comment(2, author=ME)])
    assert c.fetch_changes({}).activities == []


def test_comment_with_mention_and_stripped_body():
    body = (f'<p>Can <ac:link><ri:user ri:account-id="{ME}" /></ac:link> '
            f"check the thermals section?</p>")
    c = make_connector(comments=[comment(200, body=body)])
    [a] = c.fetch_changes({}).activities
    assert a.event_type == "comment"
    assert a.mentions_user is True
    assert "check the thermals section?" in a.text
    assert a.object_id == "100"  # anchored to the containing page
    assert a.title == "ESC test plan"


def test_spaces_scope_and_cursor_in_cql():
    log = []
    state = {"account_id": ME, "display_name": "Jakub",
             "last_sync": "2026-08-21T07:00:00.000Z"}
    c = make_connector(spaces=["ENG", "OPS"], requests_log=log)
    c.fetch_changes(state)
    searches = [r.url.params["cql"] for r in log
                if r.url.path.endswith("/content/search")]
    assert len(searches) == 2
    for cql in searches:
        assert 'lastmodified >= "2026-08-21 06:55"' in cql  # 5-min overlap
        assert 'space in ("ENG", "OPS")' in cql
        assert "order by lastmodified asc" in cql


def test_cursor_is_max_version_when():
    c = make_connector(pages=[page(1, when="2026-08-21T07:00:00.000Z")],
                       comments=[comment(2, when="2026-08-21T07:45:00.000Z")])
    result = c.fetch_changes({})
    assert result.state["last_sync"] == "2026-08-21T07:45:00.000Z"


def test_validate_reports_identity():
    c = make_connector()
    result = c.validate()
    assert result.ok and "Jakub" in result.detail
