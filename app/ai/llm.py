"""Isolated, mockable interface to the LLMs (Anthropic Haiku + Gemini embeddings).

Isolated on purpose: handlers call it through these functions so that tests can patch
them without touching the network or spending tokens. Degrades gracefully without an API key.
"""

import json
from typing import Any

import httpx

from app.config import settings

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
)
EMBED_DIM = 768


class LLMUnavailable(RuntimeError):
    """No API key configured for the provider."""


_ENRICH_PROMPT = """You are a product analyst. Evaluate this backlog item and reply ONLY with JSON.

Title: {title}
Type: {item_type}
Summary: {summary}

Return a JSON object with exactly these keys:
- "impact": integer 1-5 (5 = very high user/business impact)
- "effort": one of "XS","S","M","L","XL" (estimated implementation effort)
- "rationale": one short sentence justifying the impact, written in the same language as the item content

JSON:"""


async def enrich_item(title: str, summary: str | None, item_type: str) -> dict[str, Any]:
    """Call Haiku to estimate impact/effort. Raises LLMUnavailable without an API key."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")

    prompt = _ENRICH_PROMPT.format(title=title, item_type=item_type, summary=summary or "(no summary)")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU_MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text = "".join(block.get("text", "") for block in data.get("content", []))
    parsed = _extract_json(text)
    usage = data.get("usage", {})
    return {
        "impact": _clamp_impact(parsed.get("impact")),
        "effort": _clamp_effort(parsed.get("effort")),
        "rationale": str(parsed.get("rationale", "")).strip() or None,
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "model": _HAIKU_MODEL,
    }


_SONNET_MODEL = "claude-sonnet-4-6"

_STAGE_PROMPT = """You are a product engineer. Generate the "{stage}" stage artifact for this
development thread, written in the same language as the thread content. Return ONLY markdown.

Thread: {title}
Summary: {summary}

Previous artifacts:
{artifacts}

Generate the "{stage}" stage content:"""


async def generate_stage(stage: str, title: str, summary: str | None, artifacts: str) -> dict[str, Any]:
    """Generate the draft for a thread stage (Sonnet for spec, Haiku for the rest)."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
    model = _SONNET_MODEL if stage == "spec" else _HAIKU_MODEL
    prompt = _STAGE_PROMPT.format(
        stage=stage, title=title, summary=summary or "(no summary)",
        artifacts=artifacts or "(none)",
    )
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
    content = "".join(block.get("text", "") for block in data.get("content", []))
    return {"content": content, "model": model}


_TRIAGE_PROMPT = """You are a reliability engineer. Classify this Sentry error and
reply ONLY with JSON.

Title: {title}
Context: {context}

Return {{"triage": "<one of: bug-real, input-malo, 3rd-party, ruido>"}}.
- bug-real: genuine bug in our code that must be fixed.
- input-malo: error caused by invalid user data/input, not a bug.
- 3rd-party: external service failure, not our code.
- ruido: transient/irrelevant (isolated timeout, bot, healthcheck).

JSON:"""


async def triage_sentry(title: str, context: str) -> dict[str, Any]:
    """Classify a Sentry error with Haiku. Raises LLMUnavailable without an API key."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
    prompt = _TRIAGE_PROMPT.format(title=title, context=context[:2000])
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={"x-api-key": settings.anthropic_api_key,
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _HAIKU_MODEL, "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []))
    triage = str(_extract_json(text).get("triage", "")).strip()
    if triage not in ("real-bug", "bad-input", "3rd-party", "noise"):
        triage = "real-bug"  # safe default: when in doubt, treat it as a real bug
    return {"triage": triage}


_SUMMARY_PROMPT = """You are a product analyst. Summarize the items closed during this development
week in 3-5 short bullets (markdown), written in {language}. Focus on impact: what got resolved,
what was discarded and why, notable trends. Return ONLY the markdown.

Closed items:
{items_text}

Summary:"""

_SUMMARY_LANGUAGES = {"en": "English", "es": "Latin American Spanish", "fr": "French"}


async def summarize_closed(
    items_with_reasons: list[dict[str, Any]],
    lang: str = "en",
) -> str:
    """Generate an AI summary of the items closed during the week, in the UI language.

    items_with_reasons: list of dicts with keys title, type, status, reason (optional).
    Raises LLMUnavailable without an API key.
    """
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
    lines = []
    for i in items_with_reasons:
        reason_text = f" — {i['reason']}" if i.get("reason") else ""
        lines.append(f"- [{i.get('status', '?')}] ({i.get('type', '?')}) {i.get('title', '')}{reason_text}")
    items_text = "\n".join(lines) or "(no items)"
    prompt = _SUMMARY_PROMPT.format(
        items_text=items_text,
        language=_SUMMARY_LANGUAGES.get(lang, "English"),
    )
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={"x-api-key": settings.anthropic_api_key,
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _HAIKU_MODEL, "max_tokens": 600,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []))


async def embed_text(content: str) -> list[float] | None:
    """Gemini embedding (768 dim). Returns None when there is no API key (degradation).

    SEC-07: the API key travels in the `x-goog-api-key` header, not in the `?key=`
    query string, so it never gets recorded in access/proxy logs (query strings are
    logged by default; headers are not).

    PERF-03: aggressive timeout (8s). The embedding is optional — if the provider is
    slow, we degrade to None instead of blocking the request/worker.
    """
    if not settings.gemini_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                _GEMINI_EMBED_URL,
                headers={"x-goog-api-key": settings.gemini_api_key},
                json={
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": content[:8000]}]},
                    "outputDimensionality": EMBED_DIM,
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        return None
    values = resp.json().get("embedding", {}).get("values")
    return values if values else None


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        result: dict[str, Any] = json.loads(text[start : end + 1])
        return result
    except json.JSONDecodeError:
        return {}


def _clamp_impact(v: Any) -> int | None:
    try:
        n = int(v)
        return max(1, min(5, n))
    except (TypeError, ValueError):
        return None


def _clamp_effort(v: Any) -> str | None:
    s = str(v).upper().strip() if v is not None else ""
    return s if s in ("XS", "S", "M", "L", "XL") else None
