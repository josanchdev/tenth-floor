"""
Shared utilities for all LLM agents.

Provides config loading, Gemini API calls with Langfuse tracing,
and JSON → Pydantic parsing with error handling.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TypeVar

import google.genai as genai
import yaml
from dotenv import load_dotenv
from langfuse import Langfuse, observe
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _PROJECT_ROOT / "config"

T = TypeVar("T", bound=BaseModel)

# Singleton Langfuse client
_langfuse: Langfuse | None = None


def get_langfuse() -> Langfuse:
    """Return a shared Langfuse client instance."""
    global _langfuse
    if _langfuse is None:
        _langfuse = Langfuse()
        logger.info("Langfuse client initialised")
    return _langfuse


def load_agent_config(agent_name: str) -> dict:
    """Load model config for a specific agent from ``config/models.yaml``.

    Parameters
    ----------
    agent_name:
        Agent key in the YAML file, e.g. ``"quant_agent"``.

    Returns
    -------
    dict
        Keys: ``model``, ``temperature``, ``max_output_tokens``.
    """
    path = _CONFIG_DIR / "models.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    agent_cfg = cfg.get(agent_name, {})
    if not agent_cfg:
        raise ValueError(f"No config for agent '{agent_name}' in {path}")
    return agent_cfg


def load_risk_profile() -> dict:
    """Load ``config/risk_profile.json``."""
    path = _CONFIG_DIR / "risk_profile.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_spot_config() -> dict:
    """Load ``config/spot_only.json``."""
    path = _CONFIG_DIR / "spot_only.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@observe(name="gemini_call")
def call_gemini(
    *,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    model: str = "gemini-2.5-flash",
    temperature: float = 0.1,
    max_output_tokens: int = 1024,
) -> str:
    """Call Gemini and return the raw text response.

    Automatically traced by Langfuse via ``@observe``.

    Parameters
    ----------
    agent_name:
        Used for logging context.
    system_prompt:
        System instruction for the model.
    user_prompt:
        User-facing prompt with data context.
    model, temperature, max_output_tokens:
        Model parameters (from ``models.yaml``).

    Returns
    -------
    str
        Raw text from the model response.
    """
    client = genai.Client()

    logger.info(
        "Calling Gemini  agent=%s  model=%s  temp=%.1f",
        agent_name, model, temperature,
    )

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        ),
    )

    raw = response.text or ""
    logger.debug("Gemini response  agent=%s  len=%d", agent_name, len(raw))
    return raw


def parse_json_response(raw: str, model_class: type[T]) -> T:
    """Parse a JSON response from Gemini into a Pydantic model.

    Handles common LLM quirks:
    - Strips markdown code fences (```json ... ```)
    - Strips leading/trailing whitespace

    Parameters
    ----------
    raw:
        Raw text from ``call_gemini()``.
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
    # Strip markdown code fences
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

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
