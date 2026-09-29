"""The Claude briefing only rewords computed facts; anything unverifiable falls back to the template."""
import json
from types import SimpleNamespace

import anthropic
import httpx
import httpx2

from app.briefing import SYSTEM_PROMPT, Briefing, facts, unverified_numbers
from app.main import create_app


def grounded_text(data):
    return (f"Service level is {data['service_level'] * 100:.1f}% with {data['unmet_demand_liters']:,} L unmet. "
            f"{data['proposals_awaiting_review']} proposals await review and {data['open_alerts']} alerts are open.")


class FakeGenerate:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply or grounded_text, error, []

    async def __call__(self, data):
        self.calls.append(data)
        if self.error:
            raise self.error
        return self.reply(data), "claude-opus-5"


async def login(client):
    token = (await client.post("/api/auth/login", json={"username": "operator", "password": "demo-operator"})).json()
    return {"Authorization": "Bearer " + token["access_token"]}


async def test_without_a_key_the_template_is_used_and_health_says_so(service, config):
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        summary = (await client.get("/api/summary", headers=await login(client))).json()
        health = (await client.get("/api/health")).json()
    assert summary["source"] == "template" and "service level is" in summary["text"]
    assert health["components"]["llm"]["status"] == "disabled"


async def test_grounded_claude_text_is_shown_and_cached(service, config):
    fake = FakeGenerate()
    service.briefing = Briefing(config, generate=fake)
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = await login(client)
        first = (await client.get("/api/summary", headers=headers)).json()
        second = (await client.get("/api/summary", headers=headers)).json()
        health = (await client.get("/api/health")).json()
    assert first["source"] == "llm" and first["model"] == "claude-opus-5"
    assert first["text"] == grounded_text(fake.calls[0])
    assert second == first and len(fake.calls) == 1
    assert health["components"]["llm"]["status"] == "healthy"
    # Claude sees computed facts only: names and numbers, never raw simulator payloads.
    sent = fake.calls[0]
    assert sent["tick"] == service.snapshot.instance.tick
    assert {"most_at_risk", "top_proposals", "open_alert_messages"} <= set(sent)


async def test_an_invented_number_rejects_the_text(service):
    service.briefing = Briefing(service.config, generate=FakeGenerate(lambda data: "About 12,345 L will run out by tick 999."))
    result = await service.briefing.summarize(service.network(), service.recommendations, service.alerts)
    assert result["source"] == "template"
    assert "12,345" in result["reason"] and "999" in result["reason"]
    assert service.briefing.status()["status"] == "degraded"


async def test_an_unreachable_api_falls_back_to_the_template(service):
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    service.briefing = Briefing(service.config, generate=FakeGenerate(error=error))
    result = await service.briefing.summarize(service.network(), service.recommendations, service.alerts)
    assert result["source"] == "template" and "unreachable" in result["reason"]


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def with_fake_client(config, response):
    briefing = Briefing(config.model_copy(update={"llm_api_key": "test-key"}))
    messages = FakeMessages(response)
    briefing.client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return briefing, messages


async def test_claude_request_shape_and_text_extraction(service, config):
    data = facts(service.network(), service.recommendations, service.alerts, config.horizon_hours)
    response = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5", content=[
        SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=grounded_text(data))])
    briefing, messages = with_fake_client(config, response)
    assert briefing.enabled
    result = await briefing.summarize(service.network(), service.recommendations, service.alerts)
    assert result["source"] == "llm" and result["text"] == grounded_text(data)
    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5" and sent["system"] == SYSTEM_PROMPT
    assert sent["output_config"] == {"effort": "low"}
    assert sent["betas"] == ["server-side-fallback-2026-07-01"] and sent["fallbacks"] == "default"
    assert json.loads(sent["messages"][0]["content"]) == json.loads(json.dumps(data))


async def test_a_refusal_falls_back_to_the_template(service, config):
    response = SimpleNamespace(stop_reason="refusal", model="claude-opus-5", content=[])
    briefing, _ = with_fake_client(config, response)
    result = await briefing.summarize(service.network(), service.recommendations, service.alerts)
    assert result["source"] == "template" and "refusal" in result["reason"]


def test_an_empty_model_setting_uses_the_default(config):
    briefing = Briefing(config.model_copy(update={"llm_api_key": "test-key", "llm_model": ""}))
    assert briefing.model == "claude-opus-5"


def test_extra_ca_file_is_loaded_and_an_empty_one_ignored(config, tmp_path):
    import certifi
    empty = tmp_path / "none.pem"
    empty.write_text("")
    for path in (str(empty), certifi.where()):
        briefing = Briefing(config.model_copy(update={"llm_api_key": "test-key", "llm_ca_file": path}))
        assert briefing.enabled and briefing.client is not None


def test_number_check_allows_rounding_and_percentages_only():
    data = {"service_level": 0.9731, "unmet_demand_liters": 6987, "risk": 0.873, "when": "2026-01-01T10:30:00"}
    assert unverified_numbers("97.3% served, about 7,000 L unmet, 87% risk at 10:30.", data) == []
    assert unverified_numbers("97.3% served and 4 stations are empty.", data) == ["4"]
