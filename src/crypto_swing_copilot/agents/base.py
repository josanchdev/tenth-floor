"""
Shared utilities for all LLM agents.

Provides config loading, provider-agnostic LLM calls with Langfuse tracing,
and JSON → Pydantic parsing with error handling.

The LLM backend is configurable via ``config/models.yaml``:
  • ``openai`` provider — any OpenAI-compatible API (vLLM, Ollama, llama.cpp, OpenAI)

To switch models or providers, edit ``models.yaml`` only — no code changes required.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TypeVar

import yaml
from dotenv import load_dotenv
from langfuse import Langfuse
from langfuse.openai import OpenAI
from pydantic import BaseModel

from crypto_swing_copilot.config import CONFIG_DIR

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Singleton clients
_langfuse: Langfuse | None = None
_openai_clients: dict[str, OpenAI] = {}


def get_langfuse() -> Langfuse:
    """Return a shared Langfuse client instance."""
    global _langfuse
    if _langfuse is None:
        _langfuse = Langfuse()
        logger.info("Langfuse client initialised")
    return _langfuse


def _get_openai_client(base_url: str) -> OpenAI:
    """Return a cached OpenAI client for the given base URL."""
    if base_url not in _openai_clients:
        _openai_clients[base_url] = OpenAI(
            base_url=base_url,
            api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
        )
    return _openai_clients[base_url]


def _load_models_config() -> dict:
    """Load and return the full ``config/models.yaml``."""
    path = CONFIG_DIR / "models.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_agent_config(agent_name: str) -> dict:
    """Load model config for a specific agent from ``config/models.yaml``.

    Merges global ``defaults`` with per-agent overrides.  The agent
    section can override any default key (provider, base_url, model,
    temperature, max_output_tokens).

    Parameters
    ----------
    agent_name:
        Agent key in the YAML file, e.g. ``"quant_agent"``.

    Returns
    -------
    dict
        Merged config with keys: ``provider``, ``base_url``, ``model``,
        ``temperature``, ``max_output_tokens``.
    """
    cfg = _load_models_config()
    defaults = cfg.get("defaults", {})
    agent_cfg = cfg.get(agent_name, {})
    if not agent_cfg:
        raise ValueError(f"No config for agent '{agent_name}' in models.yaml")
    return {**defaults, **agent_cfg}


def load_risk_profile() -> dict:
    """Load ``config/risk_profile.json``."""
    path = CONFIG_DIR / "risk_profile.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def call_llm(
    *,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    model: str = "qwen3-32b",
    temperature: float = 0.1,
    max_output_tokens: int = 1024,
    provider: str = "openai",
    base_url: str = "http://localhost:8000/v1",
    json_mode: bool = True,
) -> str:
    """Call an LLM and return the raw text response.

    Provider-agnostic — routes to the backend specified by ``provider``.
    Automatically traced by Langfuse via ``langfuse.openai.OpenAI`` wrapper
    (captures tokens, model, messages, latency as a generation event).

    Parameters
    ----------
    agent_name:
        Used for logging context.
    system_prompt:
        System instruction for the model.
    user_prompt:
        User-facing prompt with data context.
    model:
        Model identifier (e.g. ``"qwen3-32b"``).
    temperature:
        Sampling temperature.
    max_output_tokens:
        Maximum tokens in the response.
    provider:
        LLM provider.  ``"openai"`` covers any OpenAI-compatible API.
    base_url:
        API base URL.  Overridden by ``LLM_BASE_URL`` env var if set.
    json_mode:
        If True, request JSON output format from the provider.

    Returns
    -------
    str
        Raw text from the model response.
    """
    base_url = os.environ.get("LLM_BASE_URL", base_url)

    if provider == "openai":
        return _call_openai_compat(
            agent_name=agent_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            base_url=base_url,
            json_mode=json_mode,
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}. Supported: 'openai'.")


def _call_openai_compat(
    *,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_output_tokens: int,
    base_url: str,
    json_mode: bool,
) -> str:
    """Call an OpenAI-compatible API (vLLM, Ollama, OpenAI, etc.)."""
    client = _get_openai_client(base_url)

    logger.info(
        "Calling LLM  agent=%s  model=%s  base_url=%s  temp=%.2f",
        agent_name, model, base_url, temperature,
    )

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)

    raw = response.choices[0].message.content or ""
    logger.debug(
        "LLM response  agent=%s  len=%d  tokens=%s",
        agent_name, len(raw),
        getattr(response.usage, "total_tokens", "n/a") if response.usage else "n/a",
    )
    return raw


def clean_json_response(raw: str) -> str:
    """Strip common LLM output artifacts before JSON parsing.

    Handles:
    - ``<think>...</think>`` reasoning blocks (Qwen3 thinking mode)
    - Markdown code fences (````json ... ``` ``)
    - Leading/trailing whitespace
    """
    cleaned = raw.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def parse_json_response(raw: str, model_class: type[T]) -> T:
    """Parse an LLM JSON response into a Pydantic model.

    Handles common LLM quirks via :func:`clean_json_response`.

    Parameters
    ----------
    raw:
        Raw text from ``call_llm()``.
    model_class:
        Pydantic model class to validate against.

    Returns
    -------
    T
        Validated Pydantic model instance.

    Raises
    ------
    ValueError
        If the response cannot be parsed as valid JSON or doesn't
        match the Pydantic schema.
    """
    cleaned = clean_json_response(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON from LLM response: %s", exc)
        logger.debug("Raw response: %s", raw[:500])
        raise ValueError(f"Invalid JSON from LLM: {exc}") from exc

    try:
        return model_class.model_validate(data)
    except Exception as exc:
        logger.error("Pydantic validation failed for %s: %s", model_class.__name__, exc)
        raise ValueError(f"Schema validation failed: {exc}") from exc
