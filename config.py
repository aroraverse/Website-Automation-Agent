"""
config.py
=========
One place that knows every setting the agent uses.

Settings are read from environment variables (loaded from a `.env` file via
python-dotenv). Anything not set falls back to a sensible default, so the
project runs out-of-the-box for the assignment's target task.

Why a dataclass instead of reading os.getenv() everywhere?
  * One source of truth — no magic strings scattered across files.
  * Defaults live next to the field they configure.
  * Easy to print/log the whole config for debugging a viva demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Read .env (if present) into the process environment exactly once on import.
load_dotenv()


def _get_bool(key: str, default: bool) -> bool:
    """Parse a truthy string env var ('true', '1', 'yes'...) into a bool."""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    """Typed, validated view of every runtime setting."""

    # --- LLM / agent brain ---
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-6"

    # --- Groq (free) brain ---
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Task definition ---
    target_url: str = "https://ui.shadcn.com/docs/forms/react-hook-form"
    name_value: str = "Checkout button not working"
    description_value: str = (
        "Clicking the submit button on mobile does nothing and shows no error at all."
    )

    # --- Browser behaviour ---
    headless: bool = False
    slow_mo_ms: int = 400
    viewport_width: int = 1280
    viewport_height: int = 900

    # --- Agent guardrails ---
    max_steps: int = 15

    # --- Filesystem / logging ---
    screenshot_dir: str = "screenshots"
    log_dir: str = "logs"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, applying defaults."""
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("MODEL", cls.model),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_model=os.getenv("GROQ_MODEL", cls.groq_model),
            target_url=os.getenv("TARGET_URL", cls.target_url),
            name_value=os.getenv("NAME_VALUE", cls.name_value),
            description_value=os.getenv("DESCRIPTION_VALUE", cls.description_value),
            headless=_get_bool("HEADLESS", cls.headless),
            slow_mo_ms=_get_int("SLOW_MO_MS", cls.slow_mo_ms),
            max_steps=_get_int("MAX_STEPS", cls.max_steps),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
        )

    def has_api_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    def has_groq_key(self) -> bool:
        return bool(self.groq_api_key.strip())
