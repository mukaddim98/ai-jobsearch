import pytest
from google.genai import errors

from jobsearch import llm as llm_mod
from jobsearch.llm import Gemini, QuotaExhausted
from jobsearch.scrape import ApifyUsage, run_cap_usd


def test_run_cap_is_per_run_ceiling_or_what_is_left():
    assert run_cap_usd(ApifyUsage(1.00, 5.0, "2026-10-01"), 4.50, 0.50) == 0.50
    assert run_cap_usd(ApifyUsage(4.30, 5.0, "2026-10-01"), 4.50, 0.50) == 0.20
    assert run_cap_usd(ApifyUsage(4.80, 5.0, "2026-10-01"), 4.50, 0.50) == 0.0
    # A plan limit below our budget wins.
    assert run_cap_usd(ApifyUsage(1.90, 2.0, "2026-10-01"), 4.50, 0.50) == 0.10


def _quota_error(quota_id, retry="30s"):
    return errors.ClientError(429, {"error": {
        "code": 429, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
        ]}})


@pytest.fixture
def gemini(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_mod.time, "sleep", sleeps.append)

    def make(budget, *responses):
        g = Gemini("fake-key", requests_per_minute=6000, budget=budget)
        queue = list(responses)

        def fake_generate(**_):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return type("Resp", (), {"text": item, "parsed": None})()

        monkeypatch.setattr(g.client.models, "generate_content", fake_generate)
        return g, sleeps
    return make


def test_budget_stops_before_sending(gemini):
    g, _ = gemini(1, "first")
    assert g.generate("m", "sys", "hi") == "first"
    with pytest.raises(QuotaExhausted, match="daily_request_cap"):
        g.generate("m", "sys", "hi")
    assert g.calls == 1


def test_daily_quota_stops_immediately_without_retrying(gemini):
    g, sleeps = gemini(10, _quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    with pytest.raises(QuotaExhausted, match="free daily quota"):
        g.generate("m", "sys", "hi")
    assert g.calls == 1 and not [s for s in sleeps if s > 1]


def test_per_minute_limit_waits_as_asked_then_retries(gemini):
    g, sleeps = gemini(10, _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "12s"), "ok")
    assert g.generate("m", "sys", "hi") == "ok"
    assert 13.0 in sleeps and g.calls == 2
