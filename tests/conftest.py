from datetime import datetime, timezone

import pytest

from app.models import Activity, AttentionCategory, AttentionItem, Source, attention_key
from app.storage import LocalPaths

NOW = datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path) -> LocalPaths:
    p = LocalPaths(root=tmp_path / ".local")
    p.ensure()
    return p


def make_activity(**overrides) -> Activity:
    base = dict(
        id="jira:ACME-1:updated:1",
        source=Source.JIRA,
        object_type="issue",
        object_id="ACME-1",
        event_type="issue_updated",
        title="Test issue",
        url="https://example.atlassian.net/browse/ACME-1",
        created_at=NOW,
    )
    base.update(overrides)
    return Activity.model_validate(base)


def make_item(category=AttentionCategory.OVERDUE, **overrides) -> AttentionItem:
    source = overrides.pop("source", Source.JIRA)
    object_id = overrides.pop("object_id", "ACME-1")
    base = dict(
        key=attention_key(source, object_id, category),
        source=source,
        object_id=object_id,
        category=category,
        title="Test issue",
        reason="Assigned to you and 2 days overdue.",
        url="https://example.atlassian.net/browse/ACME-1",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    base.update(overrides)
    return AttentionItem.model_validate(base)
