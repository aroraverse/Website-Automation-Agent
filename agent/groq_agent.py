"""
agent/groq_agent.py
===================
The autonomous "brain" — Groq (free) edition.

This is the same perceive -> think -> act loop as `llm_agent.py`, but it talks
to Groq's API instead of Anthropic's. Groq offers a genuinely free tier serving
fast open-weight models (e.g. Llama 3.3 70B) and exposes an OpenAI-compatible
chat-completions endpoint with tool calling.

Why is this a separate, small file rather than a rewrite of everything?
  * It drives the EXACT same BrowserController tools and the same element
    detector. Only the "talk to the model" part changes.
  * It proves the architecture point: the brain is swappable. Anthropic, Groq,
    or the rule-based agent all sit on top of one identical tool layer.

Key dialect differences from the Anthropic agent (worth knowing for the viva):
  * Tools are wrapped as {"type": "function", "function": {...}} (see
    tools_schema.to_openai_tools).
  * The system prompt is a message with role "system", not a separate argument.
  * A tool call's arguments arrive as a JSON *string* that we must parse.
  * Tool results are fed back as messages with role "tool" + tool_call_id.
  * The free Llama models are text-only, so we perceive/verify via
    get_interactive_elements (which reports each field's current value) rather
    than by sending screenshots.
"""

from __future__ import annotations

import json
import logging

from groq import Groq

from .browser_controller import BrowserController, BrowserError
from .element_detector import detect_interactive_elements
from .tools_schema import GROQ_SYSTEM_PROMPT, to_openai_tools


class GroqAgent:
    def __init__(
        self,
        controller: BrowserController,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        max_steps: int = 15,
        logger: logging.Logger | None = None,
    ):
        self.browser = controller
        self.model = model
        self.max_steps = max_steps
        self.log = logger or logging.getLogger("agent")
        self.client = Groq(api_key=api_key)
        self.tools = to_openai_tools()

    # ── public entry point ───────────────────────────────────────────────

    def fill_form(self, name_value: str, description_value: str) -> dict:
        self.log.info("=" * 64)
        self.log.info("GROQ AGENT — model=%s, max_steps=%d", self.model, self.max_steps)
        self.log.info("=" * 64)

        self.browser.take_screenshot("01_before.png")

        # Detect what fields exist on the page so the model can work with
        # real coordinates instead of guessing — works on ANY webpage.
        elements = detect_interactive_elements(
            self.browser.page, use_first_form=True, logger=self.log
        )
        if elements:
            lines = []
            for e in elements:
                skipped = e["tag"] == "button" or e["type"] in ("submit", "button", "reset")
                lines.append(
                    f"  [{e['index']}] <{e['tag']}> type={e['type']!r} "
                    f"label={e['label']!r} placeholder={e['placeholder']!r} "
                    f"name={e['name']!r}"
                    + ("" if skipped else
                       f" center=({e['center_x']},{e['center_y']}) in_viewport={e['in_viewport']}")
                )
            task = (
                "I'm on a page with these interactive elements detected:\n"
                + "\n".join(lines)
                + "\n\nFill the form fields with these values:\n"
                f"  - Value 1: {name_value!r}\n"
                f"  - Value 2: {description_value!r}\n"
                "\nCall get_interactive_elements if you need fresh coordinates, "
                "click_on_screen to focus a field, then send_keys to type. "
                "Verify by re-reading field values, then call report_done."
            )
        else:
            task = (
                "Find and fill the form fields on this page.\n"
                f"  - Value 1: {name_value!r}\n"
                f"  - Value 2: {description_value!r}\n"
                "Call get_interactive_elements to discover fields, "
                "click_on_screen and send_keys to fill them, "
                "then verify and call report_done."
            )
        messages: list[dict] = [
            {"role": "system", "content": GROQ_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            self.log.info("--- step %d/%d : asking the model ---", step, self.max_steps)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                max_tokens=1024,
            )

            choice = response.choices[0]
            message = choice.message

            if message.content and message.content.strip():
                self.log.info("model says: %s", message.content.strip())

            tool_calls = message.tool_calls or []
            if not tool_calls:
                # No tool call => the model believes it is finished talking.
                self.log.info("model ended its turn without a tool call — stopping.")
                return {"status": "stopped", "reason": "no_tool_call"}

            # Record the assistant turn verbatim (required for tool-call follow-up).
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            # Execute every requested tool and append a matching tool message.
            done_payload = None
            for tc in tool_calls:
                result_text, done_payload = self._dispatch(tc)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )
                if done_payload is not None:
                    break

            if done_payload is not None:
                self.log.info("GROQ AGENT — report_done received: %s", done_payload.get("summary"))
                self.browser.take_screenshot("99_groq_final.png")
                return {"status": "success", **done_payload}

        self.log.warning("GROQ AGENT — hit step limit without finishing.")
        return {"status": "incomplete", "reason": "max_steps_reached"}

    # ── tool dispatch ────────────────────────────────────────────────────

    def _dispatch(self, tool_call) -> tuple[str, dict | None]:
        """Run one tool call; return (result_text, done_payload_or_None)."""
        name = tool_call.function.name
        raw_args = tool_call.function.arguments or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            return f"ERROR: could not parse arguments as JSON: {raw_args!r}", None

        self.log.info("model -> tool: %s(%s)", name, json.dumps(args))

        try:
            if name == "get_interactive_elements":
                elements = detect_interactive_elements(
                    self.browser.page, use_first_form=True, logger=self.log
                )
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
                return json.dumps(slim, indent=2), None

            if name == "take_screenshot":
                # Text-only models can't see images; we still save the PNG for the
                # human's evidence, but tell the model to verify via field values.
                path = self.browser.take_screenshot()
                return (
                    f"Screenshot saved to {path}. You cannot see images — to verify, "
                    "call get_interactive_elements and check each field's 'value'."
                ), None

            if name == "click_on_screen":
                self.browser.click_on_screen(args["x"], args["y"])
                return f"Clicked at ({args['x']}, {args['y']}).", None

            if name == "double_click":
                self.browser.double_click(args["x"], args["y"])
                return f"Double-clicked at ({args['x']}, {args['y']}).", None

            if name == "send_keys":
                self.browser.clear_focused_field()
                self.browser.send_keys(args["text"])
                return f"Typed: {args['text']!r}", None

            if name == "scroll":
                self.browser.scroll(args.get("direction", "down"), args.get("amount", 500))
                return "Scrolled.", None

            if name == "navigate_to_url":
                self.browser.navigate_to_url(args["url"])
                return f"Navigated to {args['url']}.", None

            if name == "report_done":
                return "Acknowledged.", {
                    "summary": args.get("summary", ""),
                    "name_filled": args.get("name_filled", ""),
                    "description_filled": args.get("description_filled", ""),
                }

            return f"Unknown tool: {name}", None

        except BrowserError as exc:
            self.log.error("tool %s failed: %s", name, exc)
            return f"ERROR: {exc}", None
        except KeyError as exc:
            return f"ERROR: missing argument {exc}", None
