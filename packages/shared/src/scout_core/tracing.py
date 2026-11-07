"""Langfuse tracing for Gemini calls (spec §6.4 "All LLM calls go through Langfuse tracing").

Sends one OpenTelemetry span per call to Langfuse's OTLP/HTTP endpoint (JSON), using the attribute
names Langfuse maps (docs: langfuse.com/integrations/native/opentelemetry, checked 2026-11-07; the
older /api/public/ingestion API is deprecated and shuts down on 2026-11-16, so it isn't used).
Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set. Tracing never breaks a call: a
failure to send is logged and ignored.
"""

import base64
import json
import secrets
import time

import httpx
import structlog

from scout_core.config import get_settings

log = structlog.get_logger()
MAX_TEXT = 4000  # characters of prompt / output kept per span


def enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_public_key and s.langfuse_secret_key)


def _attr(key: str, value) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def build_span(*, purpose: str, model: str | None, prompt: str, output: str | None, input_tokens: int,
               output_tokens: int, start_ns: int, end_ns: int, error: str | None = None) -> dict:
    attrs = [
        _attr("langfuse.trace.name", purpose),
        _attr("langfuse.observation.type", "generation"),
        _attr("langfuse.observation.input", prompt[:MAX_TEXT]),
        _attr("langfuse.observation.usage_details", json.dumps({"input": input_tokens, "output": output_tokens})),
        _attr("langfuse.observation.metadata.purpose", purpose),
    ]
    if model:
        attrs.append(_attr("langfuse.observation.model.name", model))
    if output is not None:
        attrs.append(_attr("langfuse.observation.output", output[:MAX_TEXT]))
    if error:
        attrs += [_attr("langfuse.observation.level", "ERROR"), _attr("langfuse.observation.status_message", error[:500])]
    return {"resourceSpans": [{
        "resource": {"attributes": [_attr("service.name", "scout")]},
        "scopeSpans": [{"scope": {"name": "scout.llm"}, "spans": [{
            "traceId": secrets.token_hex(16), "spanId": secrets.token_hex(8), "name": purpose, "kind": 3,
            "startTimeUnixNano": str(start_ns), "endTimeUnixNano": str(end_ns), "attributes": attrs,
            "status": {"code": 2, "message": error[:500]} if error else {"code": 1},
        }]}],
    }]}


async def send(span: dict, client: httpx.AsyncClient | None = None) -> bool:
    s = get_settings()
    if not enabled():
        return False
    auth = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key.get_secret_value()}".encode()).decode()
    url = s.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json", "x-langfuse-ingestion-version": "4"}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(url, json=span, headers=headers)
        else:
            r = await client.post(url, json=span, headers=headers)
        if r.status_code >= 300:
            log.warning("langfuse.rejected", status=r.status_code, body=r.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("langfuse.unreachable", error=type(exc).__name__)
        return False


def now_ns() -> int:
    return time.time_ns()
