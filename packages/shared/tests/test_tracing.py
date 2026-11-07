"""Langfuse tracing: one OTLP span per Gemini call, only when keys are set, never breaking the call."""

import base64
import json

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from scout_core import tracing
from scout_core.config import get_settings
from scout_core.llm import Gemini, LLMError

URL = "https://cloud.langfuse.com/api/public/otel/v1/traces"


class Out(BaseModel):
    answer: str


def _attrs(body):
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    return span, {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "langfuse_public_key", "pk-lf-test")
    monkeypatch.setattr(s, "langfuse_secret_key", SecretStr("sk-lf-test"))


def _gemini(monkeypatch, behaviour):
    llm = Gemini("dummy-key", "gemini-test", None)

    async def fake(schema, prompt, *, purpose, temperature=0.1):
        if behaviour == "fail":
            raise LLMError("score_match failed: 429 RESOURCE_EXHAUSTED")
        llm.usage.by_call.append({"purpose": purpose, "model": "gemini-test", "in": 120, "out": 30})
        return schema(answer="ok")

    monkeypatch.setattr(llm, "_json", fake)
    return llm


def test_span_uses_langfuse_attribute_names():
    span, a = _attrs(tracing.build_span(purpose="tailor_resume", model="gemini-x", prompt="p" * 9000, output="{}",
                                        input_tokens=10, output_tokens=5, start_ns=1, end_ns=2))
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16 and span["status"] == {"code": 1}
    assert a["langfuse.observation.type"] == "generation" and a["langfuse.observation.model.name"] == "gemini-x"
    assert json.loads(a["langfuse.observation.usage_details"]) == {"input": 10, "output": 5}
    assert len(a["langfuse.observation.input"]) == tracing.MAX_TEXT and a["langfuse.trace.name"] == "tailor_resume"


@respx.mock
async def test_each_call_is_traced_with_auth(keys, monkeypatch):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    llm = _gemini(monkeypatch, "ok")
    assert (await llm.json(Out, "Score this job", purpose="score_match")).answer == "ok"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert req.headers["x-langfuse-ingestion-version"] == "4"
    _, a = _attrs(json.loads(req.content))
    assert a["langfuse.observation.input"] == "Score this job" and '"ok"' in a["langfuse.observation.output"]


@respx.mock
async def test_failed_call_traced_as_error_and_still_raised(keys, monkeypatch):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(LLMError):
        await _gemini(monkeypatch, "fail").json(Out, "x", purpose="score_match")
    span, a = _attrs(json.loads(route.calls[0].request.content))
    assert span["status"]["code"] == 2 and a["langfuse.observation.level"] == "ERROR"


@respx.mock
async def test_langfuse_down_never_breaks_the_call(keys, monkeypatch):
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    assert (await _gemini(monkeypatch, "ok").json(Out, "x", purpose="p")).answer == "ok"
    respx.post(URL).mock(return_value=httpx.Response(401, json={"message": "bad key"}))
    assert (await _gemini(monkeypatch, "ok").json(Out, "x", purpose="p")).answer == "ok"


@respx.mock
async def test_off_without_keys(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "langfuse_public_key", None)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    await _gemini(monkeypatch, "ok").json(Out, "x", purpose="p")
    assert route.call_count == 0
