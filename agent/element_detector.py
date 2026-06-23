"""
agent/element_detector.py
==========================
The agent's "eyes" for understanding *structure*.

A screenshot tells you what a page looks like; this module tells you what the
page actually *is* — every visible input, textarea, select and button, each
with its label, placeholder, and (crucially) the pixel coordinates of its
centre so the agent can click it precisely.

Why DOM-based detection instead of pure pixel guessing?
  * Reliability. Asking a vision model to guess "the input is around x=640,
    y=300" is fragile. Reading the real bounding box from the browser is exact.
  * It still feeds coordinate clicking — we read the true (x, y) and then click
    there, satisfying the click_on_screen(x, y) requirement *and* being robust.

`find_best_match` adds a light layer of intelligence used by the deterministic
agent: given what we're looking for (e.g. "name"/"title"), it scores every
field by how well its label/placeholder/name matches and returns the best one.
That is how the agent maps a human intention onto a concrete element without
hard-coding a brittle CSS selector.
"""

from __future__ import annotations

import logging
from typing import Optional

from playwright.sync_api import Page

# This JS runs *inside the page*. It collects every interactive element and,
# for each, works out the best human-readable label using the same strategy a
# screen reader would (aria-label -> <label for=id> -> aria-labelledby ->
# wrapping <label> -> placeholder -> name).
_COLLECT_JS = r"""
(opts) => {
  function labelFor(el) {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();

    if (el.id) {
      const l = document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) + '"]');
      if (l && l.innerText && l.innerText.trim()) return l.innerText.trim();
    }

    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const txt = labelledby.split(/\s+/)
        .map(id => { const n = document.getElementById(id); return n ? n.innerText.trim() : ''; })
        .join(' ').trim();
      if (txt) return txt;
    }

    const wrap = el.closest('label');
    if (wrap && wrap.innerText && wrap.innerText.trim()) return wrap.innerText.trim();

    if (el.placeholder && el.placeholder.trim()) return el.placeholder.trim();
    if (el.name) return el.name.trim();
    return '';
  }

  // Prefer the FIRST form that actually contains fields. The target page has
  // many demo forms; scoping to the first keeps the agent focused on the
  // primary "Bug Report" form at the top.
  let root = document;
  if (opts && opts.useFirstForm) {
    const forms = Array.from(document.querySelectorAll('form'))
      .filter(f => f.querySelector('input, textarea, select'));
    if (forms.length) root = forms[0];
  }

  const selector = 'input, textarea, select, button, [role="textbox"], [contenteditable="true"]';
  const nodes = Array.from(root.querySelectorAll(selector));
  const out = [];
  let idx = 0;
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const hidden =
      rect.width <= 1 || rect.height <= 1 ||
      style.visibility === 'hidden' || style.display === 'none' ||
      el.type === 'hidden';
    if (hidden) continue;

    out.push({
      index: idx++,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      label: labelFor(el),
      placeholder: el.placeholder || '',
      value: el.value || '',
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      center_x: Math.round(rect.x + rect.width / 2),
      center_y: Math.round(rect.y + rect.height / 2),
      in_viewport: rect.top >= 0 && rect.bottom <= window.innerHeight,
    });
  }
  return out;
}
"""


def detect_interactive_elements(
    page: Page,
    use_first_form: bool = True,
    logger: Optional[logging.Logger] = None,
) -> list[dict]:
    """Return a list of visible interactive elements with coordinates + labels."""
    log = logger or logging.getLogger("agent")
    elements = page.evaluate(_COLLECT_JS, {"useFirstForm": use_first_form})
    log.info("detect        -> found %d interactive element(s)", len(elements))
    for el in elements:
        log.debug(
            "   [%d] <%s%s> label=%r placeholder=%r center=(%d,%d)",
            el["index"], el["tag"],
            f" type={el['type']}" if el["type"] else "",
            el["label"], el["placeholder"], el["center_x"], el["center_y"],
        )
    return elements


# Words that hint a field is the "title/name" field vs the "description" field.
# Scoring keywords lets us tolerate the target page calling its title field
# "Bug Title" even though the assignment refers to it as "Name".
_FIELD_KEYWORDS = {
    "name": ["name", "title", "bug title", "subject", "summary"],
    "description": ["description", "desc", "details", "about", "message", "comment", "body"],
}


def find_best_match(elements: list[dict], field_kind: str) -> Optional[dict]:
    """Pick the element that best matches a field kind ('name' or 'description').

    Scoring (higher = better):
      +5  a keyword appears in the label
      +3  a keyword appears in the placeholder/name/id
      +2  field kind 'description' and the element is a <textarea>
      +1  field kind 'name' and the element is a single-line <input type=text>
    Buttons are never matched as fillable fields.
    """
    keywords = _FIELD_KEYWORDS.get(field_kind, [field_kind])
    best, best_score = None, 0

    for el in elements:
        if el["tag"] == "button" or el["type"] in ("submit", "button", "reset"):
            continue

        haystack_label = el["label"].lower()
        haystack_attrs = f"{el['placeholder']} {el['name']} {el['id']}".lower()
        score = 0

        for kw in keywords:
            if kw in haystack_label:
                score += 5
            if kw in haystack_attrs:
                score += 3

        if field_kind == "description" and el["tag"] == "textarea":
            score += 2
        if field_kind == "name" and el["tag"] == "input" and el["type"] in ("", "text"):
            score += 1

        if score > best_score:
            best, best_score = el, score

    return best
