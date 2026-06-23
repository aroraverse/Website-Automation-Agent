# Architecture & Design

This document explains *how* the agent is built and *why* it's built that way —
the design decisions, the agent workflow, and the trade-offs. It's the
companion to the code comments.

---

## 1. Design philosophy

The guiding idea is **separation of concerns into three layers**, so that
"deciding what to do" is completely independent from "doing it":

```
        ┌─────────────────────────────────────────────┐
        │  BRAIN  (decides what to do)                 │
        │  • LLMAgent          — Claude, autonomous    │
        │  • GroqAgent         — Llama via Groq (free) │
        │  • DeterministicAgent — rules, scripted      │
        └───────────────────────┬─────────────────────┘
                                 │ composes (only ever calls tools)
        ┌───────────────────────▼─────────────────────┐
        │  TOOLS  (do one concrete thing each)         │
        │  open_browser · navigate · screenshot ·      │
        │  click(x,y) · double_click(x,y) · send_keys ·│
        │  scroll        — BrowserController            │
        └───────────────────────┬─────────────────────┘
                                 │ drives
        ┌───────────────────────▼─────────────────────┐
        │  ENGINE                                       │
        │  Playwright  ──►  real Chromium browser       │
        └───────────────────────────────────────────────┘

        EYES (perception, shared by all brains):
        element_detector  —  reads the DOM for fields + coordinates
```

Because the brain only ever *composes tools*, you can swap the rule-based brain
for either AI brain (or add a fourth) without touching the browser code. That is
exactly the "modular tools that can be composed together" the brief asks for.

---

## 2. The two-brain design

A single deliverable gives you both reliability **and** a real demonstration of
AI control:

- **`DeterministicAgent`** — needs no API key, no LLM, no extra network. It
  detects the fields, scores them, and fills them. It *always* completes the
  task, which de-risks the "Functionality (40%)" grade and any live viva where
  you'd rather not depend on credits or wifi.

- **`LLMAgent`** — hands the task and the tools to Claude and lets the model
  drive. This is the "AI-driven browser control" the brief is really about.

- **`GroqAgent`** — the same autonomous loop, but driven by an open-weight model
  (e.g. Llama 3.3 70B) through Groq's **free**, OpenAI-compatible API. It exists
  so the AI mode can be demonstrated with no Anthropic cost. The only real
  differences are dialect (tools are wrapped in OpenAI "function" format by
  `tools_schema.to_openai_tools`, and tool results come back as `role: "tool"`
  messages) and that the free models are text-only — so this brain verifies its
  work by re-reading field values via the detector instead of using screenshots.

All three call the identical `BrowserController` tools. If the deterministic
agent works, the tool layer is proven correct, so both LLM agents build on solid
ground.

---

## 3. The agent loop (LLM mode)

This is the classic agent cycle, implemented in `llm_agent.py` on top of the
Anthropic Messages API's tool-use feature:

```
  navigate (done by harness)
        │
        ▼
  ┌──────────────────────────────────────────────┐
  │  1. Send Claude: task + conversation so far    │
  │     + the list of tools it may call            │
  └───────────────────┬────────────────────────────┘
                      ▼
  ┌──────────────────────────────────────────────┐
  │  2. Claude replies with a tool_use block       │
  │     e.g. click_on_screen(x=640, y=300)         │
  └───────────────────┬────────────────────────────┘
                      ▼
  ┌──────────────────────────────────────────────┐
  │  3. We execute it on the real browser          │
  └───────────────────┬────────────────────────────┘
                      ▼
  ┌──────────────────────────────────────────────┐
  │  4. We send the result back as a tool_result   │
  │     (text, or a screenshot IMAGE for vision)   │
  └───────────────────┬────────────────────────────┘
                      ▼
        loop ◄────────┘  until Claude calls report_done
                         (or MAX_STEPS is hit — a safety rail)
```

A typical successful trajectory is only ~6 steps:
`get_interactive_elements → click title → send_keys → click description →
send_keys → take_screenshot → report_done`.

---

## 4. The key idea: DOM-grounded coordinate clicking

The brief requires a `click_on_screen(x, y)` tool. The naïve approach is to
show a vision model a screenshot and ask it to *guess* pixel coordinates. That
is fragile — guesses are off by tens of pixels and break when the layout shifts.

Instead, the `element_detector` runs JavaScript **inside the page** to read each
field's real `getBoundingClientRect()`, and returns the exact centre `(x, y)` of
every input. The agent then clicks *those* coordinates.

This gives us the best of both worlds:

- it satisfies the coordinate-clicking requirement literally (we click `(x, y)`);
- it is **reliable**, because the coordinates are ground-truth from the browser;
- it is **intelligent**, because the *choice* of which element to click is made
  by reasoning over each field's label/role — not by a hard-coded selector.

The viewport is fixed at 1280×900 with `deviceScaleFactor = 1`, so one CSS pixel
equals one screenshot pixel equals one mouse coordinate. Everything lines up.

---

## 5. Intelligent element detection

`element_detector.py` does two jobs:

1. **Perception** (`detect_interactive_elements`): collect every visible
   `input / textarea / select / button`, and for each compute a human-readable
   label using the same priority a screen reader uses:
   `aria-label → <label for=id> → aria-labelledby → wrapping <label> →
   placeholder → name`. It scopes to the **first form with fields**, because the
   target page contains many demo forms and we want the primary one at the top.

2. **Matching** (`find_best_match`): given a target intent (`"name"` or
   `"description"`), score each field and return the winner. Scoring rewards a
   keyword match in the label most heavily, then in other attributes, then the
   element *type* (a `<textarea>` is a strong signal for "description"). This is
   what lets the agent map the requested **"Name"** onto the form's actual
   **"Bug Title"** field, and never mistake the **Submit** button for an input.

The LLM agent uses (1) for grounding and does (2) itself by reasoning; the
deterministic agent uses both.

---

## 6. Tool reference

| Tool (`BrowserController`) | Playwright primitive | Notes |
|----------------------------|----------------------|-------|
| `open_browser` | `chromium.launch` + `new_context` | fixed viewport, scale 1 |
| `navigate_to_url` | `page.goto` | waits for DOM + (best-effort) network idle + a form to mount |
| `take_screenshot` | `page.screenshot` | viewport-only so pixels map to coordinates; also kept as base64 for the vision model |
| `click_on_screen` | `page.mouse.click` | coordinate-based |
| `double_click` | `page.mouse.dblclick` | e.g. select-a-word before retyping |
| `send_keys` | `page.keyboard.type` | small per-key delay; visible + human-like |
| `clear_focused_field` | `Ctrl/Cmd+A`, `Delete` | helper so typing replaces existing text |
| `scroll` | `page.mouse.wheel` | reveal off-screen fields |

The LLM additionally sees `get_interactive_elements`, `take_screenshot`, and a
`report_done` tool that ends the task.

---

## 7. Error handling strategy

- Every tool wraps its Playwright call and raises a typed `BrowserError` with a
  clear message on failure.
- The LLM agent **catches** tool errors and feeds them back to the model as a
  `tool_result` flagged `is_error=True`, so the model can *see* the failure and
  retry a different action instead of the whole program crashing.
- `navigate_to_url` treats `networkidle` and "wait for a form" as best-effort
  (they time out gracefully) because analytics-heavy pages never truly go idle.
- A `MAX_STEPS` cap guarantees the agent loop always terminates.
- `main.py` has a top-level `try/finally` so the browser is **always** closed,
  even on an unexpected error.

---

## 8. Key decisions & trade-offs

| Decision | Why | Trade-off accepted |
|----------|-----|--------------------|
| Python + Playwright | Most readable for a viva; Playwright's auto-waiting is more robust than Puppeteer's | — |
| Synchronous Playwright API | Simpler, linear code that's easy to explain | Not built for many parallel pages (not needed here) |
| DOM-grounded coordinates over pure-vision guessing | Reliability | Won't work on a `<canvas>`-only app with no DOM (out of scope) |
| Two brains (rule-based + LLM) | Guarantees functionality *and* shows AI control | A little extra code |
| Claude tool-use loop | Canonical, transparent agent pattern; vision built in | Costs API tokens in `agent` mode |
| Scope detection to the first form | The target page has ~10 demo forms | Assumes the wanted form is first (true here; configurable via `--url`/code) |

---

## 9. Limitations & possible extensions

- **No CAPTCHA / login handling** — out of scope for this task.
- **Single page, single form** — the architecture would extend naturally to
  multi-page flows by letting the agent call `navigate_to_url` and loop.
- **Canvas/WebGL apps** — DOM detection can't see non-DOM pixels; you'd fall
  back to pure-vision (Claude's computer-use) for those.
- **Set-of-marks prompting** — a nice future upgrade: draw numbered boxes on the
  screenshot so the model picks an element by number rather than coordinate.
- **Self-healing** — on a failed click, automatically re-detect and retry; the
  hooks for this already exist in the error-handling path.
