"""The LLM layer, driven entirely through an injected stub client."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipelinemd.diagnose import build_user_message, diagnose
from pipelinemd.diagnose.prompt import DIAGNOSIS_SCHEMA, coerce_category, coerce_confidence
from pipelinemd.distill import distill
from pipelinemd.errors import DiagnosisError
from pipelinemd.models import Category, Confidence, JobRef
from pipelinemd.rules import match_rules

PAYLOAD = {
    "summary": "npm ci failed on a peer dependency conflict",
    "root_cause": "Line 2 shows ERESOLVE while resolving react.",
    "confidence": "high",
    "category": "dependency",
    "fixes": [
        {"title": "Regenerate the lockfile", "detail": "Run npm install.", "patch": "npm install"},
        {"title": "Upgrade the package", "detail": "Bump design-system.", "patch": ""},
    ],
}


class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 1234
    output_tokens = 567


class _Response:
    def __init__(self, blocks: list[_Block], stop_reason: str = "end_turn") -> None:
        self.content = blocks
        self.usage = _Usage()
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, response: Any, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class _Client:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.messages = _Messages(response, error)


@pytest.fixture
def distilled() -> Any:
    return distill("$ npm ci\nnpm ERR! code ERESOLVE\nERROR: Job failed: exit code 1\n")


def test_diagnose_returns_a_structured_result(distilled: Any) -> None:
    client = _Client(_Response([_Block("thinking"), _Block("text", json.dumps(PAYLOAD))]))
    result = diagnose(JobRef(name="build"), distilled, match_rules(distilled), client=client)

    assert result.summary == PAYLOAD["summary"]
    assert result.confidence is Confidence.HIGH
    assert result.category is Category.DEPENDENCY
    assert len(result.fixes) == 2
    assert result.fixes[0].patch == "npm install"
    assert result.fixes[1].patch is None, "an empty patch string means no patch"
    assert (result.input_tokens, result.output_tokens) == (1234, 567)


def test_request_uses_the_documented_api_shape(distilled: Any) -> None:
    client = _Client(_Response([_Block("text", json.dumps(PAYLOAD))]))
    diagnose(JobRef(name="build"), distilled, [], client=client, model="claude-opus-5")

    kwargs = client.messages.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["effort"] == "high"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["output_config"]["format"]["schema"] == DIAGNOSIS_SCHEMA
    assert "budget_tokens" not in json.dumps(kwargs), "removed on current models"


def test_thinking_block_before_the_payload_is_skipped(distilled: Any) -> None:
    client = _Client(_Response([_Block("thinking"), _Block("text", json.dumps(PAYLOAD))]))
    assert diagnose(JobRef(name="b"), distilled, [], client=client).summary


def test_refusal_is_reported(distilled: Any) -> None:
    client = _Client(_Response([_Block("text", "{}")], stop_reason="refusal"))
    with pytest.raises(DiagnosisError, match="declined"):
        diagnose(JobRef(name="b"), distilled, [], client=client)


def test_non_json_text_is_reported(distilled: Any) -> None:
    client = _Client(_Response([_Block("text", "I think the build broke.")]))
    with pytest.raises(DiagnosisError, match="not JSON"):
        diagnose(JobRef(name="b"), distilled, [], client=client)


def test_missing_text_block_is_reported(distilled: Any) -> None:
    client = _Client(_Response([_Block("thinking")]))
    with pytest.raises(DiagnosisError, match="no text block"):
        diagnose(JobRef(name="b"), distilled, [], client=client)


def test_transport_failure_is_wrapped(distilled: Any) -> None:
    client = _Client(error=RuntimeError("connection reset"))
    with pytest.raises(DiagnosisError, match="connection reset"):
        diagnose(JobRef(name="b"), distilled, [], client=client)


def test_unknown_enum_values_degrade_safely() -> None:
    assert coerce_confidence("HIGH ") is Confidence.HIGH
    assert coerce_confidence("wildly certain") is Confidence.LOW
    assert coerce_category("dependency") is Category.DEPENDENCY
    assert coerce_category("vibes") is Category.SCRIPT


def test_prompt_carries_evidence_not_the_raw_trace() -> None:
    raw = "noise\n" * 5000 + "npm ERR! code ERESOLVE\nERROR: Job failed: exit code 1\n"
    result = distill(raw)
    message = build_user_message(JobRef(name="build"), result, match_rules(result))

    assert "npm ERR! code ERESOLVE" in message
    assert message.count("noise") < 100, "the model must not receive the raw trace"
    assert "npm.eresolve" in message, "rule findings are given as a prior"


def test_prompt_includes_ci_config_when_supplied(distilled: Any) -> None:
    message = build_user_message(
        JobRef(name="build"), distilled, [], ci_config="build:\n  script: npm ci\n"
    )
    assert "```yaml" in message
    assert "script: npm ci" in message


def test_schema_is_strict() -> None:
    assert DIAGNOSIS_SCHEMA["additionalProperties"] is False
    items = DIAGNOSIS_SCHEMA["properties"]["fixes"]["items"]  # type: ignore[index]
    assert items["additionalProperties"] is False
    assert set(items["required"]) == {"title", "detail", "patch"}
