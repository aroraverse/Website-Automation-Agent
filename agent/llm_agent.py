"""
agent/llm_agent.py
==================
The autonomous "brain".

This is the part that makes the project an *agent* rather than a script. It
runs a classic perceive -> think -> act loop powered by Claude's tool-use API:

    1. We hand Claude the task + the list of tools it may call.
    2. Claude decides which tool to call and with what arguments.
    3. We execute that tool against the real browser.
    4. We feed the result (text, or a screenshot image) back to Claude.
    5. Repeat until Claude calls `report_done` (or we hit the step limit).

Claude is genuinely doing the intelligent work: mapping the human instruction
("fill the name and description") onto concrete page elements, choosing
coordinates, and verifying its own result from a screenshot. Note how it
resolves the wording mismatch — the page labels the field "Bug Title", and the
model still recognises it as the requested "name" field.

This file talks to the Anthropic Messages API (POST /v1/messages) via the
official `anthropic` SDK. Every current Claude model supports both vision
(image input) and tool use, which is exactly what this loop relies on.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from .browser_controller import BrowserController, BrowserError
from .element_detector import detect_interactive_elements
from .tools_schema import SYSTEM_PROMPT, TOOLS


class LLMAgent:
    def __init__(
        self,
        controller: BrowserController,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_steps: int = 15,
        logger: logging.Logger | None = None,
    ):
        self.browser = controller
        self.model = model
        self.max_steps = max_steps
        self.log = logger or logging.getLogger("agent")
        self.client = Anthropic(api_key=api_key)

    # ── public entry point ───────────────────────────────────────────────

    def fill_form(self, name_value: str, description_value: str) -> dict:
        self.log.info("=" * 64)
        self.log.info("LLM AGENT — model=%s, max_steps=%d", self.model, self.max_steps)
        self.log.info("=" * 64)

        task = (
            "Fill the form on this page.\n"
            f"  - Name / Bug Title field: {name_value!r}\n"
            f"  - Description field: {description_value!r}\n"
            "Then verify with a screenshot and call report_done."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

        for step in range(1, self.max_steps + 1):
            self.log.info("--- step %d/%d : asking the model ---", step, self.max_steps)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            # Record the assistant turn verbatim (required for multi-turn tool use).
            messages.append({"role": "assistant", "content": response.content})

            # Log any reasoning text the model included alongside its tool calls.
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self.log.info("model says: %s", block.text.strip())

            if response.stop_reason != "tool_use":
                self.log.info("model ended its turn without a tool call — stopping.")
                return {"status": "stopped", "reason": "no_tool_call"}

            # Execute every tool the model asked for, collecting results.
            tool_results = []
            done_payload = None
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_block, done_payload = self._dispatch(block)
                tool_results.append(result_block)
                if done_payload is not None:
                    break

            if done_payload is not None:
                self.log.info("LLM AGENT — report_done received: %s", done_payload.get("summary"))
                self.browser.take_screenshot("99_llm_final.png")
                return {"status": "success", **done_payload}

            # Feed all tool results back as the next user turn.
            messages.append({"role": "user", "content": tool_results})

        self.log.warning("LLM AGENT — hit step limit without finishing.")
        return {"status": "incomplete", "reason": "max_steps_reached"}

    # ── tool dispatch ────────────────────────────────────────────────────

    def _dispatch(self, block) -> tuple[dict, dict | None]:
        """Run one tool call; return (tool_result_block, done_payload_or_None)."""
        name = block.name
        args = block.input or {}
        self.log.info("model -> tool: %s(%s)", name, json.dumps(args))

        try:
            if name == "get_interactive_elements":
                elements = detect_interactive_elements(
                    self.browser.page, use_first_form=True, logger=self.log
                )
                # Trim to the fields the model needs to reason about.
                slim = [
                    {
                        "label": e["label"],
                        "tag": e["tag"],
                        "type": e["type"],
                        "placeholder": e["placeholder"],
                        "value": e["value"],
                        "center_x": e["center_x"],
                        "center_y": e["center_y"],
                        "in_viewport": e["in_viewport"],
                    }
                    for e in elements
                ]
                return self._text_result(block.id, json.dumps(slim, indent=2)), None

            if name == "take_screenshot":
                self.browser.take_screenshot()
                return self._image_result(block.id), None

            if name == "click_on_screen":
                self.browser.click_on_screen(args["x"], args["y"])
                return self._text_result(block.id, f"Clicked at ({args['x']}, {args['y']})."), None

            if name == "double_click":
                self.browser.double_click(args["x"], args["y"])
                return self._text_result(block.id, f"Double-clicked at ({args['x']}, {args['y']})."), None

            if name == "send_keys":
                # Clear first so typed text replaces whatever was there.
                self.browser.clear_focused_field()
                self.browser.send_keys(args["text"])
                return self._text_result(block.id, f"Typed: {args['text']!r}"), None

            if name == "scroll":
                self.browser.scroll(args.get("direction", "down"), args.get("amount", 500))
                return self._text_result(block.id, "Scrolled."), None

            if name == "navigate_to_url":
                self.browser.navigate_to_url(args["url"])
                return self._text_result(block.id, f"Navigated to {args['url']}."), None

            if name == "report_done":
                return self._text_result(block.id, "Acknowledged."), {
                    "summary": args.get("summary", ""),
                    "name_filled": args.get("name_filled", ""),
                    "description_filled": args.get("description_filled", ""),
                }

            return self._text_result(block.id, f"Unknown tool: {name}", is_error=True), None

        except BrowserError as exc:
            self.log.error("tool %s failed: %s", name, exc)
            return self._text_result(block.id, f"ERROR: {exc}", is_error=True), None
        except KeyError as exc:
            return self._text_result(block.id, f"ERROR: missing argument {exc}", is_error=True), None

    # ── result-block builders (Anthropic tool_result format) ─────────────

    def _text_result(self, tool_use_id: str, text: str, is_error: bool = False) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": text}],
            "is_error": is_error,
        }

    def _image_result(self, tool_use_id: str) -> dict:
        b64 = self.browser.last_screenshot_b64()
        if not b64:
            return self._text_result(tool_use_id, "Screenshot unavailable.", is_error=True)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [
                {"type": "text", "text": "Current screenshot:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
            ],
        }
