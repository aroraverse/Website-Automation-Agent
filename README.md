# Website Automation Agent

An intelligent browser-automation agent — a mini version of tools like
[Browser Use](https://github.com/browser-use/browser-use). It opens a real
Chromium browser, navigates to a page, **understands the form on it**, and
fills in the fields autonomously, using a small set of composable tools.

Built with **Python + Playwright**, with an optional LLM brain for fully
autonomous, AI-driven control — either **Claude** (Anthropic) or **Llama via
Groq's free tier**.

---

## What it does (the target task)

It automates this page: <https://ui.shadcn.com/docs/forms/react-hook-form>

The agent finds the form at the top of the page and fills in its two fields:

- the **title** field (the assignment calls this "Name")
- the **Description** field

> **Important real-world detail:** the assignment refers to a "Name" field, but
> the actual demo form on that page labels its first field **"Bug Title"**, and
> its second field is a **Description** textarea. There is no field literally
> called "Name". A dumb script that hunts for the text "Name" would fail. This
> agent handles it the smart way — it scores every field by how well its label
> matches the *intent* ("name / title"), so it correctly picks **"Bug Title"**.
> This mismatch is a great thing to mention in your viva: it shows why
> *intelligent element detection* beats brittle hard-coded selectors.

---

## Three ways to run it ("swappable brains")

| Mode | Brain | Needs API key? | When to use |
|------|-------|----------------|-------------|
| `deterministic` | Rule-based scoring | No | Guaranteed to work, free, perfect viva fallback |
| `agent` | Claude (LLM, vision + tool use) | Yes (Anthropic) | Shows true autonomous AI-driven control |
| `groq` | Llama via Groq (LLM, tool use) | Yes (Groq, **free tier**) | AI-driven control with no Anthropic cost |
| `auto` (default) | Claude if its key is set, else Groq if its key is set, else `deterministic` | — | Just works |

All three drive the **same** seven browser tools — only the decision-maker
changes. The Groq models are text-only, so that brain perceives and verifies
through the element detector (which reports each field's value) instead of
screenshots.

Both modes drive the **same** seven tools — only the decision-making differs.
The LLM agent runs a real *perceive → think → act* loop: it looks at the page,
decides which tool to call, we execute it, and we feed the result (including
screenshots) back to the model until it reports the task done.

---

## The seven required tools

All implemented in `agent/browser_controller.py`:

| Tool | What it does |
|------|--------------|
| `open_browser()` | Launch a Chromium instance |
| `navigate_to_url(url)` | Go to a page and wait for it to settle |
| `take_screenshot(name)` | Capture the viewport to a PNG |
| `click_on_screen(x, y)` | Left-click at pixel coordinates |
| `double_click(x, y)` | Double-click at pixel coordinates |
| `send_keys(text)` | Type text into the focused field |
| `scroll(direction, amount)` | Scroll the page |

---

## Prerequisites

- **Python 3.10+**
- **Node.js** is *not* required to run the agent (Playwright bundles its own
  browser driver), but you do need to let Playwright download Chromium once.
- (Optional) An **Anthropic API key** for `agent` mode — get one at
  <https://console.anthropic.com/>.

---

## Setup

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. download the browser Playwright drives (one-time, ~150 MB)
python -m playwright install chromium

# 4. create your config from the template
cp .env.example .env
#   then open .env and paste your ANTHROPIC_API_KEY (only needed for agent mode)
```

---

## Run it

```bash
# Auto mode — uses Claude if you set a key, otherwise the rule-based agent
python main.py

# Force the rule-based agent (no API key, no network beyond the target site)
python main.py --mode deterministic

# Force the AI agent (Anthropic Claude)
python main.py --mode agent

# Force the AI agent on Groq's FREE tier (set GROQ_API_KEY in .env first)
python main.py --mode groq

# Watch it work, slowed down (these are the defaults in .env)
#   HEADLESS=false, SLOW_MO_MS=400

# Change what gets typed
python main.py --name "Map pin drift" \
               --description "Pins jump about 50 metres after zooming on iOS."
```

### Offline / no-wifi demo

If the live site is unreachable (or you want a guaranteed viva demo), point the
agent at the bundled local form that mirrors the real one:

```bash
python main.py --mode deterministic --url "file://$(pwd)/tests/sample_form.html"
```

After any run, look in `screenshots/` for before/after images and `logs/` for a
full transcript of every decision and action.

---

## Configuration

Everything is set in `.env` (see `.env.example` for the annotated template).
CLI flags (`--url`, `--name`, `--description`, `--headless`) override `.env`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | _(empty)_ | Key for `agent` mode |
| `MODEL` | `claude-sonnet-4-6` | Which Claude model thinks (`claude-opus-4-8` is best for browser agents; `claude-haiku-4-5` is cheapest) |
| `TARGET_URL` | the shadcn page | Page to automate |
| `NAME_VALUE` | `Checkout button not working` | Text for the title field (5–32 chars) |
| `DESCRIPTION_VALUE` | _(a sample bug)_ | Text for the description (20–100 chars) |
| `HEADLESS` | `false` | `true` hides the browser window |
| `SLOW_MO_MS` | `400` | Delay per action so the demo is watchable |
| `MAX_STEPS` | `15` | Safety cap on the agent's think-act loop |
| `LOG_LEVEL` | `INFO` | `DEBUG` for very verbose logs |

> The target form validates length. Keep the title 5–32 characters and the
> description 20–100 characters, or the form will show a validation error.

---

## Run the tests

```bash
pytest -v
```

These launch a headless browser against the bundled local form and verify the
detector finds the right fields, ignores the decoy form, and that the agent
actually types the values into the DOM. They need Chromium installed (step 3)
but **no API key and no network**.

---

## Project structure

```
website-automation-agent/
├── main.py                      # CLI entry point: config -> browser -> agent
├── config.py                    # all settings, loaded from .env with defaults
├── requirements.txt
├── .env.example                 # annotated config template
├── README.md
├── ARCHITECTURE.md              # design decisions & agent workflow
├── agent/
│   ├── browser_controller.py    # the 7 atomic Playwright tools ("hands")
│   ├── element_detector.py      # DOM-based field detection ("eyes") + scoring
│   ├── deterministic_agent.py   # rule-based runner (no LLM)
│   ├── llm_agent.py             # Claude-driven perceive-think-act loop ("brain")
│   ├── groq_agent.py            # Groq (free) perceive-think-act loop ("brain")
│   ├── tools_schema.py          # tool definitions (+ OpenAI/Groq converter) + prompts
│   └── logger.py                # console + file logging
├── tests/
│   ├── sample_form.html         # offline mirror of the target form
│   └── test_agent.py            # end-to-end tests against the local form
├── screenshots/                 # PNGs produced at runtime
└── logs/                        # run transcripts produced at runtime
```

---

## Troubleshooting

- **`Executable doesn't exist … playwright install`** — you skipped step 3. Run
  `python -m playwright install chromium`.
- **Browser window doesn't appear** — `HEADLESS` is `true`. Set it to `false`.
- **`agent` mode says it fell back to deterministic/groq** — no
  `ANTHROPIC_API_KEY` in `.env` (it then tries `GROQ_API_KEY`, then the
  rule-based agent).
- **`groq` mode says it fell back to deterministic** — no `GROQ_API_KEY` in
  `.env`. Get a free one at https://console.groq.com/ .
- **Validation error on the real form** — your `NAME_VALUE` / `DESCRIPTION_VALUE`
  is too short/long (see the length limits above).
- **Corporate network blocks the Chromium download** — your network egress
  settings need to allow Playwright's CDN; ask whoever manages the network, or
  use a personal machine.

---

## Publishing to GitHub

This repository's commit history is authored by **Sarthak**
(`sarthak7591@gmail.com`). To push it to a new GitHub repo:

```bash
# 1. Create an empty repo on github.com (no README/license — keep it bare)
# 2. Then, from this folder:
git remote add origin https://github.com/<your-username>/website-automation-agent.git
git push -u origin main
```

If you ever move this to another machine, set your identity there too:

```bash
git config user.name  "Sarthak"
git config user.email "sarthak7591@gmail.com"
```

---
