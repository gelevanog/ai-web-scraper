"""Providers: the free-only guard, request shapes (OpenRouter, Qwen Cloud), error mapping, the budget wrapper,
the Anthropic provider with a mocked client, the offline model and JSON parsing. No network, no keys: the test key
is assembled at runtime and is not secret-shaped."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from sift.config import Settings
from sift.llm.anthropic_provider import AnthropicModel
from sift.llm.base import BudgetExceededError, Completion, LLMError, PolicyViolationError, RetryableLLMError
from sift.llm.budget import BudgetedModel, CallLedger, DiskCache, Throttle
from sift.llm.factory import build_chat_model
from sift.llm.fake import FakeModel
from sift.llm.guards import ensure_free_body, ensure_free_models, ensure_served_free
from sift.llm.jsonparse import JSONReplyError, parse_json_object
from sift.llm.openai_compat import OpenAICompatibleModel

MESSAGES: list[Any] = [
    {"role": "system", "content": "SIFT_TASK: direct_extract\nx"},
    {"role": "user", "content": "hello"},
]
TEST_KEY = "test-" + "key-" + "0" * 8  # built at runtime; never a real-looking secret


def ok_reply(model: str, text: str = '{"ok": true}') -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def capture(response: httpx.Response | dict[str, Any]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    return httpx.MockTransport(handler), seen


# ------------------------------------------------------------------ the free-only guard
@pytest.mark.parametrize(
    "model", ["openai/gpt-5", "nvidia/nemotron-3-super-120b-a12b", "x/y:free:online", "x/y:online", "openrouter/auto"]
)
def test_guard_refuses_non_free_ids(model: str) -> None:
    with pytest.raises(PolicyViolationError):
        ensure_free_models([model])


def test_guard_checks_fallbacks_plugins_and_served_model() -> None:
    ensure_free_models(["a/b:free", "c/d:free"])
    with pytest.raises(PolicyViolationError):
        ensure_free_models(["a/b:free", "c/d"])
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a/b:free", "plugins": [{"id": "web"}]})
    with pytest.raises(PolicyViolationError):
        ensure_served_free("a/b")
    ensure_served_free("a/b:free")


def test_guard_runs_before_anything_is_built() -> None:
    settings = Settings(_env_file=None, llm_provider="openrouter", llm_model="openai/gpt-5", openrouter_api_key=None)  # type: ignore[call-arg]
    with pytest.raises(PolicyViolationError):
        build_chat_model(settings)  # refused before the missing key is even noticed
    paid_fallback = Settings(
        _env_file=None, llm_provider="openrouter", llm_model="a/b:free", llm_fallback_models=["c/d"]
    )  # type: ignore[call-arg]
    with pytest.raises(PolicyViolationError):
        build_chat_model(paid_fallback)


def test_paid_served_model_is_rejected() -> None:
    transport, _ = capture(ok_reply("nvidia/nemotron-3-super-120b-a12b"))
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://openrouter.test/api/v1",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        api_key=TEST_KEY,
        transport=transport,
    )
    with pytest.raises(PolicyViolationError):
        model.complete(MESSAGES, max_tokens=100)


def test_guard_does_not_apply_to_other_providers() -> None:
    transport, _ = capture(ok_reply("qwen3.7-plus"))
    model = OpenAICompatibleModel(
        kind="qwen",
        base_url="https://dashscope.test/compatible-mode/v1",
        model="qwen3.7-plus",
        api_key=TEST_KEY,
        transport=transport,
    )
    assert model.complete(MESSAGES, max_tokens=100).model == "qwen3.7-plus"


# ------------------------------------------------------------------ request shapes
def test_openrouter_request_shape() -> None:
    transport, seen = capture(ok_reply("a/b:free"))
    model = OpenAICompatibleModel(
        kind="openrouter", base_url="https://openrouter.test/api/v1", model="a/b:free", api_key=TEST_KEY,
        fallback_models=["c/d:free"], reasoning_effort="low", transport=transport,
    )  # fmt: skip
    completion = model.complete(MESSAGES, max_tokens=321, temperature=0.0)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://openrouter.test/api/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert request.headers["x-title"] == "Sift"
    assert body["model"] == "a/b:free" and body["models"] == ["a/b:free", "c/d:free"]
    assert body["reasoning"] == {"effort": "low"} and body["max_tokens"] == 321 and "plugins" not in body
    assert completion.input_tokens == 10 and completion.output_tokens == 5


def test_qwen_request_shape() -> None:
    transport, seen = capture(ok_reply("qwen3.7-plus"))
    settings = Settings(
        _env_file=None, llm_provider="qwen", llm_model="qwen3.7-plus", qwen_api_key=TEST_KEY, qwen_enable_thinking=False
    )  # type: ignore[call-arg]
    built = build_chat_model(settings)
    assert isinstance(built, OpenAICompatibleModel)
    model = OpenAICompatibleModel(
        kind="qwen",
        base_url=settings.qwen_base_url,
        model="qwen3.7-plus",
        api_key=TEST_KEY,
        enable_thinking=False,
        transport=transport,
    )
    model.complete(MESSAGES, max_tokens=50)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert body["enable_thinking"] is False and "models" not in body and "reasoning" not in body
    assert "x-title" not in request.headers


def test_qwen_reads_the_dashscope_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", TEST_KEY)
    assert Settings(_env_file=None, llm_provider="qwen").qwen_api_key == TEST_KEY  # type: ignore[call-arg]


def test_missing_key_is_reported() -> None:
    with pytest.raises(LLMError, match="QWEN_API_KEY"):
        OpenAICompatibleModel(kind="qwen", base_url="https://x.test", model="m", api_key=None)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "7"}),
            RetryableLLMError,
        ),
        (httpx.Response(502, json={"error": {"message": "bad gateway"}}), RetryableLLMError),
        (httpx.Response(200, json={"error": {"code": 503, "message": "upstream down"}}), RetryableLLMError),
        (httpx.Response(403, json={"error": {"message": "Access denied by security policy"}}), LLMError),
        (
            httpx.Response(
                200, json={"model": "a/b:free", "choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
            ),
            LLMError,
        ),
        (
            httpx.Response(
                200, json={"model": "a/b:free", "choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
            ),
            LLMError,
        ),
    ],
)
def test_error_mapping(response: httpx.Response, error: type[Exception]) -> None:
    transport, _ = capture(response)
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://openrouter.test/api/v1",
        model="a/b:free",
        api_key=TEST_KEY,
        transport=transport,
    )
    with pytest.raises(error) as info:
        model.complete(MESSAGES, max_tokens=10)
    if response.status_code == 429:
        assert isinstance(info.value, RetryableLLMError) and info.value.retry_after == 7.0


# ------------------------------------------------------------------ budget wrapper
class Flaky:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    @property
    def label(self) -> str:
        return "openrouter/a/b:free"

    @property
    def is_local(self) -> bool:
        return False

    def complete(self, messages: Any, *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.calls += 1
        if self.calls <= self.failures:
            raise RetryableLLMError("rate limited", retry_after=0.0)
        return Completion(text='{"a": 1}', model="a/b:free", input_tokens=3, output_tokens=2)


def test_budget_wrapper_retries_records_and_caches(tmp_path: Path) -> None:
    ledger_path = tmp_path / "calls.jsonl"
    inner = Flaky(failures=2)
    model = BudgetedModel(
        inner,
        ledger=CallLedger(ledger_path, 10),
        cache=DiskCache(tmp_path / "cache"),
        throttle=Throttle(0),
        max_retries=3,
        retry_base_seconds=0.0,
        tag="t",
    )
    assert model.complete(MESSAGES, max_tokens=5).text == '{"a": 1}'
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["retryable_error", "retryable_error", "ok"]
    assert all("hello" not in json.dumps(r) for r in rows), "the ledger never stores prompts"
    assert model.complete(MESSAGES, max_tokens=5).cached and inner.calls == 3


def test_budget_is_a_hard_limit(tmp_path: Path) -> None:
    model = BudgetedModel(
        Flaky(failures=0), ledger=CallLedger(tmp_path / "c.jsonl", 1), cache=None, throttle=None, tag="t"
    )
    model.complete(MESSAGES, max_tokens=5)
    with pytest.raises(BudgetExceededError):
        model.complete([{"role": "user", "content": "other"}], max_tokens=5)


def test_policy_fallback_after_a_provider_403(tmp_path: Path) -> None:
    class Refuses(Flaky):
        def complete(self, messages: Any, *, max_tokens: int, temperature: float = 0.0) -> Completion:
            raise LLMError("upstream 403: Access denied by security policy")

    ledger = CallLedger(tmp_path / "c.jsonl", 10)
    fallback = BudgetedModel(Flaky(0), ledger=ledger, cache=None, throttle=None, tag="fb")
    model = BudgetedModel(Refuses(0), ledger=ledger, cache=None, throttle=None, tag="main", policy_fallback=fallback)
    assert model.complete(MESSAGES, max_tokens=5).text == '{"a": 1}'
    assert ledger.calls == 2


# ------------------------------------------------------------------ Anthropic, fake, JSON
def test_anthropic_provider_with_a_mocked_client() -> None:
    sent: dict[str, Any] = {}

    def create(**params: Any) -> Any:
        sent.update(params)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"x": 1}')],
            model="claude-sonnet-5",
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=12, output_tokens=4),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    model = AnthropicModel(model="claude-sonnet-5", client=client)  # type: ignore[arg-type]
    completion = model.complete(MESSAGES, max_tokens=100)
    assert completion.text == '{"x": 1}' and completion.model == "claude-sonnet-5"
    assert sent["system"].startswith("SIFT_TASK: direct_extract") and sent["messages"] == [
        {"role": "user", "content": "hello"}
    ]
    assert sent["output_config"] == {"effort": "low"} and sent["max_tokens"] >= 8000


def test_fake_model_dispatches_on_the_task_marker() -> None:
    fake = FakeModel({"direct_extract": lambda messages, gullible: '{"adjustment": 0}'})
    assert fake.complete(MESSAGES, max_tokens=10).text == '{"adjustment": 0}'
    with pytest.raises(LLMError):
        fake.complete([{"role": "system", "content": "SIFT_TASK: nope"}], max_tokens=10)
    assert build_chat_model(Settings(_env_file=None, llm_provider="fake")).is_local


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Sure!\n```json\n{"a": 1}\n```',
        '<think>{"no": 0}</think>{"a": 1}',
        '{"a": 1,}',
        'prefix {"a": 1} suffix {"b": 2}',
    ],
)
def test_json_parsing(text: str) -> None:
    assert parse_json_object(text)["a"] == 1


def test_json_parsing_fails_loudly() -> None:
    with pytest.raises(JSONReplyError):
        parse_json_object("no json here")


def test_counting_model_tracks_tasks_and_tokens() -> None:
    from sift.llm.counting import CountingModel

    counted = CountingModel(Flaky(failures=0))
    counted.complete(MESSAGES, max_tokens=5)
    assert counted.stats.by_task() == {"direct_extract": 1}
    assert counted.stats.tokens() == (3, 2)
    assert counted.stats.as_dict()["calls"] == 1


def test_default_models_and_free_fallbacks() -> None:
    from sift.config import DEFAULT_FREE_FALLBACKS, DEFAULT_MODELS

    assert DEFAULT_MODELS["openrouter"].endswith(":free")
    assert all(m.endswith(":free") for m in DEFAULT_FREE_FALLBACKS)
    assert DEFAULT_MODELS["anthropic"] == "claude-sonnet-5"
    ensure_free_models([DEFAULT_MODELS["openrouter"], *DEFAULT_FREE_FALLBACKS])
