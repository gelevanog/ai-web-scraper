"""The free-only guard (on by default): refuses any OpenRouter model id that does not end in ``:free``, checks the
fallback list too, refuses paid features (``:online`` web search, the ``web`` plugin) and rejects an answer that
OpenRouter served from a non-free model. A client who wants paid models turns it off deliberately with
SIFT_REQUIRE_FREE_MODELS=false."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sift.llm.base import PolicyViolationError


def ensure_free_models(model_ids: Iterable[str]) -> None:
    ids = [str(model) for model in model_ids]
    paid = [model for model in ids if not model.endswith(":free") or ":online" in model]
    if paid:
        raise PolicyViolationError(
            f"free-only guard: refusing non-free OpenRouter model id(s): {', '.join(paid)} "
            "(set SIFT_REQUIRE_FREE_MODELS=false to allow paid models)"
        )


def ensure_free_body(body: Mapping[str, Any]) -> None:
    """The last check before a request leaves the process: every model id in the body is free, no paid plugin."""
    ensure_free_models([body.get("model", ""), *body.get("models", [])])
    if body.get("plugins") or body.get("web_search_options"):
        raise PolicyViolationError("free-only guard: refusing a request with plugins or web search (paid features)")


def ensure_served_free(served_model: str | None) -> None:
    if served_model and not served_model.endswith(":free"):
        raise PolicyViolationError(
            f"free-only guard: OpenRouter served non-free model {served_model!r}; answer rejected"
        )
