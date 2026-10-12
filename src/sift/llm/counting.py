"""Per-job accounting of model calls: how many, for which task, how long, how many tokens, from cache or not."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sift.llm.base import ChatModel, Completion, Message
from sift.llm.fake import task_of


@dataclass
class CallRecord:
    task: str
    model: str
    seconds: float
    input_tokens: int
    output_tokens: int
    cached: bool
    ok: bool


@dataclass
class CallStats:
    calls: list[CallRecord] = field(default_factory=list)

    def count(self, *, task: str | None = None, include_cached: bool = True) -> int:
        return sum(1 for c in self.calls if (task is None or c.task == task) and (include_cached or not c.cached))

    def by_task(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.calls:
            out[c.task] = out.get(c.task, 0) + 1
        return out

    def tokens(self) -> tuple[int, int]:
        return sum(c.input_tokens for c in self.calls), sum(c.output_tokens for c in self.calls)

    def as_dict(self) -> dict[str, object]:
        tin, tout = self.tokens()
        return {
            "calls": len(self.calls),
            "cached": sum(1 for c in self.calls if c.cached),
            "failed": sum(1 for c in self.calls if not c.ok),
            "by_task": self.by_task(),
            "input_tokens": tin,
            "output_tokens": tout,
            "seconds": round(sum(c.seconds for c in self.calls), 2),
        }


class CountingModel:
    def __init__(self, inner: ChatModel, stats: CallStats | None = None) -> None:
        self.inner = inner
        self.stats = stats or CallStats()
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return self.inner.label

    @property
    def is_local(self) -> bool:
        return self.inner.is_local

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        started = time.monotonic()
        task = task_of(messages) or "unknown"
        try:
            completion = self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            with self._lock:
                self.stats.calls.append(
                    CallRecord(task, self.inner.label, time.monotonic() - started, 0, 0, False, False)
                )
            raise
        with self._lock:
            self.stats.calls.append(
                CallRecord(
                    task,
                    completion.model,
                    time.monotonic() - started,
                    completion.input_tokens,
                    completion.output_tokens,
                    completion.cached,
                    True,
                )
            )
        return completion
