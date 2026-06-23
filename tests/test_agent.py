"""
tests/test_agent.py
===================
End-to-end-ish tests that exercise the real tool layer against the bundled
local form (tests/sample_form.html). They need a working Playwright Chromium
install but NO network and NO API key.

Run with:
    pytest -v

What they prove:
  * the browser launches, navigates, screenshots;
  * the element detector finds the right fields and ignores the decoy form;
  * the deterministic agent actually types the values into the DOM;
  * coordinate clicking + send_keys work end to end.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from agent import BrowserController, DeterministicAgent, get_logger
from agent.element_detector import detect_interactive_elements, find_best_match

HERE = pathlib.Path(__file__).resolve().parent
FORM_URL = (HERE / "sample_form.html").as_uri()  # file:// URL


@pytest.fixture()
def browser():
    log = get_logger("test", log_dir="logs", level="WARNING")
    b = BrowserController(headless=True, slow_mo_ms=0, logger=log)
    b.open_browser()
    b.navigate_to_url(FORM_URL)
    yield b
    b.close()


def test_detect_finds_two_fields_in_first_form(browser):
    elements = detect_interactive_elements(browser.page, use_first_form=True)
    labels = [e["label"].lower() for e in elements]
    # Should see the Bug Report form's fields, not the Profile/Username decoy.
    assert any("title" in l for l in labels), labels
    assert any("description" in l for l in labels), labels
    assert not any("username" in l for l in labels), labels


def test_find_best_match_picks_correct_fields(browser):
    elements = detect_interactive_elements(browser.page, use_first_form=True)
    name_field = find_best_match(elements, "name")
    desc_field = find_best_match(elements, "description")
    assert name_field is not None and name_field["tag"] == "input"
    assert desc_field is not None and desc_field["tag"] == "textarea"


def test_deterministic_agent_fills_form(browser):
    agent = DeterministicAgent(controller=browser)
    result = agent.fill_form("Checkout button not working", "Submit does nothing on mobile Safari at all.")
    assert result["status"] == "success"

    # Verify the values actually landed in the DOM.
    title_val = browser.page.input_value("#form-rhf-demo-title")
    desc_val = browser.page.input_value("#form-rhf-demo-description")
    assert title_val == "Checkout button not working"
    assert "Submit does nothing" in desc_val


def test_screenshot_is_written(browser):
    path = browser.take_screenshot("test_shot.png")
    assert os.path.exists(path)
    assert path.endswith(".png")


def test_to_openai_tools_conversion():
    """The Groq adapter reuses the same tools, re-wrapped in OpenAI format.

    This test needs no browser, no network and no key — it just checks the
    schema translation that lets a free Groq key drive the same agent.
    """
    from agent.tools_schema import TOOLS, to_openai_tools

    converted = to_openai_tools()
    assert len(converted) == len(TOOLS)
    for original, wrapped in zip(TOOLS, converted):
        assert wrapped["type"] == "function"
        fn = wrapped["function"]
        assert fn["name"] == original["name"]
        assert fn["description"] == original["description"]
        # The JSON schema body must be passed through unchanged.
        assert fn["parameters"] == original["input_schema"]
