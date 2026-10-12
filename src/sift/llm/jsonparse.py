"""Robust JSON extraction from model replies: code fences, leading prose, <think> blocks and trailing commas."""

from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


class JSONReplyError(ValueError):
    pass


def _balanced(text: str, start: int) -> str | None:
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a model reply. Raises JSONReplyError when there is none."""
    cleaned = _THINK.sub("", text).strip()
    candidates = [m.group(1) for m in _FENCE.finditer(cleaned)] + [cleaned]
    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            chunk = _balanced(candidate, match.start())
            if chunk is None:
                continue
            for attempt in (chunk, _TRAILING_COMMA.sub(r"\1", chunk)):
                try:
                    value = json.loads(attempt)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
            break
    raise JSONReplyError(f"no JSON object in the reply: {cleaned[:160]!r}")
