"""
agent/tools_schema.py
=====================
The tool definitions the LLM ("the brain") is allowed to call.

This is the contract between Claude and our browser. Claude never touches
Playwright; it can only emit one of these tool calls, which our agent loop
then executes against the BrowserController. The JSON schema for each tool is
what teaches the model exactly what arguments to provide.

These map directly onto the assignment's required capabilities:
  take_screenshot, navigate_to_url, click_on_screen, double_click,
  send_keys, scroll  (open_browser is performed by the harness before the
  agent starts thinking).
"""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "get_interactive_elements",
        "description": (
            "Return a JSON list of the visible form fields and buttons in the "
            "primary form, each with its label, placeholder, current value, and "
            "the pixel coordinates of its centre (center_x, center_y). "
            "ALWAYS call this before clicking so you click real coordinates."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture the current browser viewport and return it as an image so "
            "you can visually verify the page state (e.g. confirm text landed "
            "in a field)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click_on_screen",
        "description": "Left-click at the given viewport pixel coordinates to focus a field or press a button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal pixel position."},
                "y": {"type": "integer", "description": "Vertical pixel position."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "double_click",
        "description": "Double-click at viewport pixel coordinates (e.g. to select a word before retyping).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "send_keys",
        "description": (
            "Type text into the currently focused field. Click the field first. "
            "The field is auto-cleared before typing so you replace any existing text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page to reveal elements that are off-screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "description": "Pixels to scroll (default 500)."},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "navigate_to_url",
        "description": "Navigate the browser to a different URL if needed.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "report_done",
        "description": (
            "Call this once both fields are filled and you have visually verified "
            "the result. This ends the task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One sentence on what you did."},
                "name_filled": {"type": "string", "description": "Value you put in the title/name field."},
                "description_filled": {"type": "string", "description": "Value you put in the description field."},
            },
            "required": ["summary"],
        },
    },
]


def to_openai_tools(tools: list[dict] | None = None) -> list[dict]:
    """Convert our Anthropic-style tool list into OpenAI/Groq function format.

    Anthropic shape:  {"name", "description", "input_schema": {...}}
    OpenAI/Groq shape: {"type": "function",
                        "function": {"name", "description", "parameters": {...}}}

    The JSON schema body is identical between the two; only the wrapper differs.
    This is the whole reason a Groq (or any OpenAI-compatible) provider is a
    small adapter and not a rewrite — same tools, different envelope.
    """
    src = tools if tools is not None else TOOLS
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in src
    ]


# Groq's free tier serves open-weight models (e.g. Llama 3.3 70B) that are
# text-only — they cannot see screenshots. So this prompt tells the model to
# perceive and VERIFY purely through get_interactive_elements (which returns
# each field's current `value`), never relying on vision.
GROQ_SYSTEM_PROMPT = """You are a precise web-automation agent controlling a real Chromium browser through a small set of tools.

You do NOT have vision — you cannot see screenshots. You perceive the page only through get_interactive_elements, which returns each field's label, type, current value, and the pixel coordinates of its centre.

The browser is ALREADY open and has ALREADY navigated to the target page. Your job:

  Find the interactive fields on the page and fill them with the provided values. The task message below lists the values to fill — match each one to the appropriate field (first value → first/title-type field, second value → second/description-type field).

Work like this, step by step:
  - Call get_interactive_elements to see the fields and their exact centre coordinates (or use the list already provided in the task).
  - click_on_screen on the appropriate field's centre to focus it, then send_keys the value.
  - Repeat for each remaining field.
  - Call get_interactive_elements again and CHECK that each field's `value` now matches what you typed (this is how you verify without a screenshot).
  - Call report_done with a short summary.

Rules:
  - Only ever click coordinates that came from get_interactive_elements.
  - Focus a field with a click BEFORE typing into it.
  - Do not submit the form; only fill the fields.
  - Be efficient — you have a limited number of steps.
"""


SYSTEM_PROMPT = """You are a precise web-automation agent controlling a real Chromium browser through a small set of tools.

The browser is ALREADY open and has ALREADY navigated to the target page. Your job:

  Fill in the primary form at the TOP of the page (a "Bug Report" form).
  1. The first single-line text field (its visible label is "Bug Title") is the NAME / title field — put the provided name value there.
  2. The multi-line text area (labelled "Description") is the description field — put the provided description value there.

Work like this, step by step:
  - Call get_interactive_elements to see the fields and their exact centre coordinates.
  - click_on_screen on the title field's centre, then send_keys the name value.
  - click_on_screen on the description field's centre, then send_keys the description value.
  - take_screenshot to confirm both values are visible in the fields.
  - Call report_done with a short summary.

Rules:
  - Only ever click coordinates that came from get_interactive_elements.
  - Focus a field with a click BEFORE typing into it.
  - Do not submit the form; only fill it.
  - Be efficient — you have a limited number of steps.
"""
