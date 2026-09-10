"""LLM access for Mode C2 generation.

Any OpenAI-compatible endpoint works: a litellm proxy (keygate), OpenRouter,
or the OpenAI API itself. Configuration comes from CLI flags first, then
environment variables:

- ``WB2GDPVAL_LLM_BASE_URL`` (or ``OPENAI_BASE_URL``) — endpoint base URL
- ``WB2GDPVAL_LLM_API_KEY`` (or ``LITELLM_API_KEY`` / ``OPENROUTER_API_KEY``
  / ``OPENAI_API_KEY``) — key, first one set wins
- ``WB2GDPVAL_LLM_MODEL`` — default model id (default ``claude-opus-5``;
  on OpenRouter use the prefixed form, e.g. ``anthropic/claude-opus-5``)

The ``openai`` package is an optional dependency (``pip install
'wb2gdpval[llm]'`` / ``uv sync --extra llm``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

_API_KEY_ENVS = (
    "WB2GDPVAL_LLM_API_KEY",
    "LITELLM_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
)

DEFAULT_MODEL = "claude-opus-5"


def _default_base_url() -> str | None:
    return os.environ.get("WB2GDPVAL_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")


def _default_api_key() -> str | None:
    for env in _API_KEY_ENVS:
        if os.environ.get(env):
            return os.environ[env]
    return None


@dataclass
class LLMConfig:
    model: str = field(default_factory=lambda: os.environ.get("WB2GDPVAL_LLM_MODEL", DEFAULT_MODEL))
    base_url: str | None = field(default_factory=_default_base_url)
    api_key: str | None = field(default_factory=_default_api_key)
    temperature: float = 1.0
    max_tokens: int = 16_000


class LLMClient:
    """Thin chat wrapper. Kept minimal so tests can substitute a fake with the
    same ``complete()`` signature."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        if not self.config.api_key:
            raise RuntimeError(
                "no LLM API key found; set one of "
                + ", ".join(_API_KEY_ENVS)
                + " (and WB2GDPVAL_LLM_BASE_URL for a litellm proxy or OpenRouter)"
            )
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - import guard
            raise RuntimeError(
                "the 'openai' package is required for generation: "
                "uv sync --extra llm (or pip install 'wb2gdpval[llm]')"
            ) from e
        self._client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key)

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("LLM returned an empty completion")
        return str(content)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a model completion.

    Accepts bare JSON, a fenced ```json block, or JSON embedded in prose
    (first ``{`` to last ``}``). Raises ValueError when nothing parses.
    """
    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("completion contained no parseable JSON object")
