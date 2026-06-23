#!/usr/bin/env python3
"""
main.py
=======
Entry point for the Website Automation Agent.

Usage examples
--------------
    # Run with the AI brain (needs ANTHROPIC_API_KEY in .env):
    python main.py --mode agent

    # Run the rule-based fallback (no API key, always works):
    python main.py --mode deterministic

    # Auto-pick: agent if an API key is present, else deterministic:
    python main.py

    # Try it offline against the bundled local form (great for a no-wifi viva):
    python main.py --mode deterministic --url file://$(pwd)/tests/sample_form.html

    # Override what gets typed:
    python main.py --name "Map pin drift" --description "Pins jump ~50m after zooming on iOS."

The flow is always the same:
    load config -> open browser -> navigate -> run agent -> screenshot -> close.
"""

from __future__ import annotations

import argparse
import sys

from agent import BrowserController, DeterministicAgent, GroqAgent, LLMAgent, get_logger
from config import Config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Autonomous website form-filling agent.")
    p.add_argument(
        "--mode",
        choices=["agent", "groq", "deterministic", "auto"],
        default="auto",
        help=(
            "'agent' = Claude-driven, 'groq' = Groq free-tier-driven, "
            "'deterministic' = rule-based, 'auto' = pick based on available keys."
        ),
    )
    p.add_argument("--url", help="Override the target URL.")
    p.add_argument("--name", help="Override the value typed into the name/title field.")
    p.add_argument("--description", help="Override the value typed into the description field.")
    p.add_argument("--headless", action="store_true", help="Run the browser without a window.")
    p.add_argument(
        "--keep-open",
        type=int,
        default=4,
        help="Seconds to keep the browser open at the end so you can see the result (default 4).",
    )
    return p.parse_args()


def choose_mode(requested: str, cfg: Config, log) -> str:
    """Resolve 'auto' into a concrete mode and warn on impossible combinations.

    Preference order for 'auto': Claude (best) -> Groq (free) -> deterministic.
    Explicit modes fall back gracefully if their key is missing.
    """
    if requested == "auto":
        if cfg.has_api_key():
            chosen = "agent"
        elif cfg.has_groq_key():
            chosen = "groq"
        else:
            chosen = "deterministic"
        log.info("mode 'auto' resolved to '%s'", chosen)
        return chosen

    if requested == "agent" and not cfg.has_api_key():
        fallback = "groq" if cfg.has_groq_key() else "deterministic"
        log.warning("Mode 'agent' requested but no ANTHROPIC_API_KEY found — falling back to '%s'.", fallback)
        return fallback

    if requested == "groq" and not cfg.has_groq_key():
        log.warning("Mode 'groq' requested but no GROQ_API_KEY found — falling back to deterministic.")
        return "deterministic"

    return requested


def main() -> int:
    args = parse_args()
    cfg = Config.from_env()

    # CLI flags override .env values.
    if args.url:
        cfg.target_url = args.url
    if args.name:
        cfg.name_value = args.name
    if args.description:
        cfg.description_value = args.description
    if args.headless:
        cfg.headless = True

    log = get_logger("agent", log_dir=cfg.log_dir, level=cfg.log_level)
    log.info("Target URL : %s", cfg.target_url)
    log.info("Name value : %s", cfg.name_value)
    log.info("Desc value : %s", cfg.description_value)

    mode = choose_mode(args.mode, cfg, log)

    browser = BrowserController(
        headless=cfg.headless,
        slow_mo_ms=cfg.slow_mo_ms,
        viewport_width=cfg.viewport_width,
        viewport_height=cfg.viewport_height,
        screenshot_dir=cfg.screenshot_dir,
        logger=log,
    )

    result: dict = {"status": "error"}
    try:
        browser.open_browser()
        browser.navigate_to_url(cfg.target_url)

        if mode == "agent":
            agent = LLMAgent(
                controller=browser,
                api_key=cfg.anthropic_api_key,
                model=cfg.model,
                max_steps=cfg.max_steps,
                logger=log,
            )
        elif mode == "groq":
            agent = GroqAgent(
                controller=browser,
                api_key=cfg.groq_api_key,
                model=cfg.groq_model,
                max_steps=cfg.max_steps,
                logger=log,
            )
        else:
            agent = DeterministicAgent(controller=browser, logger=log)

        result = agent.fill_form(cfg.name_value, cfg.description_value)

        log.info("-" * 64)
        log.info("RESULT: %s", result)
        log.info("-" * 64)

        if args.keep_open > 0 and not cfg.headless:
            log.info("Keeping browser open for %ds so you can inspect it…", args.keep_open)
            browser.page.wait_for_timeout(args.keep_open * 1000)

    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.exception("Agent run failed: %s", exc)
        result = {"status": "error", "error": str(exc)}
    finally:
        browser.close()

    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
