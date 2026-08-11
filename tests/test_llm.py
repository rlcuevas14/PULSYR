"""Coverage for app/ai/llm.py: pure helpers, no-key degradation, and mocked API calls."""
import httpx
import pytest

from app.ai import llm


def test_pure_helpers():
    assert llm._extract_json('noise {"a": 1} tail') == {"a": 1}
    assert llm._extract_json("no json here") == {}
    assert llm._extract_json("{bad json}") == {}
    assert llm._clamp_impact(9) == 5
    assert llm._clamp_impact(0) == 1
    assert llm._clamp_impact("x") is None
    assert llm._clamp_effort("m") == "M"
    assert llm._clamp_effort("ZZ") is None


@pytest.mark.asyncio
async def test_degrades_without_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "")
    monkeypatch.setattr("app.config.settings.gemini_api_key", "")
    with pytest.raises(llm.LLMUnavailable):
        await llm.enrich_item("t", None, "feature")
    with pytest.raises(llm.LLMUnavailable):
        await llm.triage_sentry("t", "ctx")
    with pytest.raises(llm.LLMUnavailable):
        await llm.generate_stage("spec", "t", None, "")
    assert await llm.embed_text("hello") is None  # no gemini key → None


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _mock_post(monkeypatch, payload):
    async def fake_post(self, url, **kw):
        return _FakeResp(payload)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


@pytest.mark.asyncio
async def test_enrich_item_with_mocked_api(monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "key")
    _mock_post(monkeypatch, {
        "content": [{"text": '{"impact": 4, "effort": "M", "rationale": "important"}'}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    out = await llm.enrich_item("Login", "summary", "feature")
    assert out["impact"] == 4 and out["effort"] == "M" and out["rationale"] == "important"


@pytest.mark.asyncio
async def test_triage_and_generate_with_mocked_api(monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "key")
    _mock_post(monkeypatch, {"content": [{"text": '{"triage": "noise"}'}]})
    assert (await llm.triage_sentry("t", "ctx"))["triage"] == "noise"
    _mock_post(monkeypatch, {"content": [{"text": '{"triage": "weird"}'}]})
    assert (await llm.triage_sentry("t", "ctx"))["triage"] == "real-bug"  # default
    _mock_post(monkeypatch, {"content": [{"text": "# Spec\nbody"}]})
    gen = await llm.generate_stage("spec", "Thread", "sum", "prev")
    assert "Spec" in gen["content"] and gen["model"]


@pytest.mark.asyncio
async def test_embed_text_with_mocked_api(monkeypatch):
    monkeypatch.setattr("app.config.settings.gemini_api_key", "key")
    _mock_post(monkeypatch, {"embedding": {"values": [0.1] * 768}})
    vec = await llm.embed_text("hello world")
    assert vec is not None and len(vec) == 768
