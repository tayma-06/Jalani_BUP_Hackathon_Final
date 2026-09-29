import json

import httpx
import pytest

from app.assistant import AssistantRequest, OperationsAssistant
from app.main import create_app


def completion(ids=None, text="Network briefing [network]", finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({
        "explanation": text, "source_ids": ids or ["network"], "uncertainty": "Forecasts are estimates."})}}]}


async def test_groq_contract_and_no_execution(service, config, fake):
    config.groq_api_key = "test-secret"
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in body
        assert "test-secret" not in request.content.decode()
        return httpx.Response(200, json=completion())
    assistant = OperationsAssistant(config, httpx.MockTransport(handler))
    result = await assistant.explain(service, AssistantRequest())
    assert result["source"] == "ai" and result["tick"] == service.snapshot.instance.tick
    assert len(calls) == 1 and not fake.posts
    assert (await assistant.explain(service, AssistantRequest()))["source"] == "template"
    assert len(calls) == 1


async def test_uncited_answer_is_retried_once_then_accepted(service, config):
    config.groq_api_key = "test-secret"
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=completion(text="Briefing with no tags at all"))
        return httpx.Response(200, json=completion(text="Briefing [network]"))
    result = await OperationsAssistant(config, httpx.MockTransport(handler)).explain(service, AssistantRequest())
    assert result["source"] == "ai" and len(calls) == 2
    assert "no tags at all" not in result["explanation"]
    assert calls[1]["messages"][-1]["role"] == "user" and "square brackets" in calls[1]["messages"][-1]["content"]


async def test_persistently_uncited_answer_falls_back_after_one_retry(service, config):
    config.groq_api_key = "test-secret"
    calls = []
    def handler(_):
        calls.append(1)
        return httpx.Response(200, json=completion(text="Still no tags"))
    result = await OperationsAssistant(config, httpx.MockTransport(handler)).explain(service, AssistantRequest())
    assert result["source"] == "template" and len(calls) == 2
    assert "did not cite its sources" in result["fallback_reason"]


@pytest.mark.parametrize("payload,status", [(completion(["invented"]), 200),
    (completion(text="No citation"), 200), (completion(finish="length"), 200),
    ({"choices": []}, 200), ({}, 429), ({}, 503)])
async def test_invalid_and_unavailable_provider_falls_back(service, config, payload, status):
    config.groq_api_key = "test-secret"
    assistant = OperationsAssistant(config, httpx.MockTransport(lambda _: httpx.Response(status, json=payload)))
    result = await assistant.explain(service, AssistantRequest())
    assert result["source"] == "template" and result["fallback_reason"]


async def test_missing_key_stale_and_record_fallback(service, config):
    config.groq_api_key = ""
    assistant = OperationsAssistant(config)
    rec = service.recommendations[0]
    result = await assistant.explain(service, AssistantRequest(task="recommendation", record_id=rec["id"]))
    assert result["source"] == "template" and result["sources"][1]["data"]["id"] == rec["id"]
    service.stale = True
    assert (await assistant.explain(service, AssistantRequest()))["stale"]


async def test_timeout_and_snapshot_change(service, config):
    config.groq_api_key = "test-secret"
    def timeout(request):
        raise httpx.ReadTimeout("private upstream detail", request=request)
    result = await OperationsAssistant(config, httpx.MockTransport(timeout)).explain(service, AssistantRequest())
    assert result["source"] == "template" and "private" not in json.dumps(result)
    def changed(_):
        service.run_id = "new-run"
        return httpx.Response(200, json=completion())
    result = await OperationsAssistant(config, httpx.MockTransport(changed)).explain(service, AssistantRequest())
    assert result["stale"]


async def test_authenticated_read_only_endpoint(service, config, fake):
    config.groq_api_key = ""
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/assistant", json={})).status_code == 401
        token = (await client.post("/api/auth/login", json={"username": "viewer", "password": "demo-viewer"})).json()
        headers = {"Authorization": "Bearer " + token["access_token"]}
        assert (await client.post("/api/assistant", json={}, headers=headers)).status_code == 200
        assert (await client.post("/api/assistant", json={"task": "allocate"}, headers=headers)).status_code == 422
        assert (await client.post("/api/assistant", json={"task": "incident", "record_id": "missing"}, headers=headers)).status_code == 404
    assert not fake.posts
