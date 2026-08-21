import pytest
from pydantic import ValidationError

from app.ai import analyse
from app.ai.client import AIError
from app.ai.schemas import ClassificationResponse
from app.models import AttentionCategory
from tests.conftest import NOW, make_activity


class FakeAIClient:
    def __init__(self, responses=None, fail_times=0):
        self.responses = responses or []
        self.fail_times = fail_times
        self.calls = []

    def classify(self, activities):
        self.calls.append([a.id for a in activities])
        if self.fail_times > 0:
            self.fail_times -= 1
            raise AIError("simulated failure")
        return ClassificationResponse.model_validate(
            {"items": [self._item(a) for a in activities]}
        )

    def _item(self, a):
        return {
            "activity_id": a.id, "relevant": True,
            "category": "POSSIBLE_WAITING", "importance": 78,
            "confidence": 0.84, "requires_action": True,
            "reason": "James asks for approval and no later response is present.",
            "summary": "James appears to be waiting for launch approval.",
            "waiting_person": "James",
        }


def waiting_activity(**overrides):
    defaults = dict(id="slack:C1:1", actor_name="James",
                    text="Can you approve the launch date today?")
    defaults.update(overrides)
    return make_activity(**defaults)


def test_schema_rejects_unknown_category():
    with pytest.raises(ValidationError):
        ClassificationResponse.model_validate({"items": [{
            "activity_id": "x", "relevant": True, "category": "SHOUTING",
            "importance": 50, "confidence": 0.5, "requires_action": False,
            "reason": "r", "summary": "s",
        }]})


def test_schema_rejects_out_of_range_importance():
    with pytest.raises(ValidationError):
        ClassificationResponse.model_validate({"items": [{
            "activity_id": "x", "relevant": True, "category": "FYI",
            "importance": 150, "confidence": 0.5, "requires_action": False,
            "reason": "r", "summary": "s",
        }]})


def test_selection_skips_textless_and_completed():
    keep = waiting_activity()
    textless = make_activity(id="jira:1:1", text=None)
    done = make_activity(id="jira:2:1", text="done now", completed=True)
    assert analyse.select_for_ai([keep, textless, done]) == [keep]


def test_ai_pass_produces_attention_items(paths):
    client = FakeAIClient()
    cache = analyse.ClassificationCache(paths)
    result = analyse.run_ai_pass([waiting_activity()], client, cache, NOW)
    assert not result.failed
    [item] = result.items
    assert item.category is AttentionCategory.POSSIBLE_WAITING
    assert item.deterministic is False
    assert item.confidence == 0.84
    assert "waiting" in item.summary


def test_cache_prevents_second_call(paths):
    cache = analyse.ClassificationCache(paths)
    client = FakeAIClient()
    analyse.run_ai_pass([waiting_activity()], client, cache, NOW)
    cache.save()

    cache2 = analyse.ClassificationCache(paths)
    client2 = FakeAIClient()
    result = analyse.run_ai_pass([waiting_activity()], client2, cache2, NOW)
    assert client2.calls == []
    assert result.from_cache == 1
    assert len(result.items) == 1


def test_changed_text_misses_cache(paths):
    cache = analyse.ClassificationCache(paths)
    analyse.run_ai_pass([waiting_activity()], FakeAIClient(), cache, NOW)
    client2 = FakeAIClient()
    analyse.run_ai_pass(
        [waiting_activity(id="slack:C1:2", text="Different question entirely?")],
        client2, cache, NOW)
    assert len(client2.calls) == 1


def test_one_retry_then_success(paths):
    client = FakeAIClient(fail_times=1)
    cache = analyse.ClassificationCache(paths)
    result = analyse.run_ai_pass([waiting_activity()], client, cache, NOW)
    assert not result.failed
    assert len(client.calls) == 2
    assert len(result.items) == 1


def test_persistent_failure_degrades_gracefully(paths):
    client = FakeAIClient(fail_times=10)
    cache = analyse.ClassificationCache(paths)
    result = analyse.run_ai_pass([waiting_activity()], client, cache, NOW)
    assert result.failed
    assert result.items == []
    assert len(client.calls) == 2


def test_irrelevant_and_noise_produce_no_items(paths):
    class NoiseClient(FakeAIClient):
        def _item(self, a):
            return {**super()._item(a), "relevant": False, "category": "NOISE"}

    result = analyse.run_ai_pass([waiting_activity()], NoiseClient(),
                                 analyse.ClassificationCache(paths), NOW)
    assert result.items == []


def test_payload_truncates_long_text():
    from app.ai.client import MAX_TEXT_CHARS, activity_payload
    a = waiting_activity(text="x" * 10_000)
    assert len(activity_payload(a)["text"]) == MAX_TEXT_CHARS
