"""
agent/browser_controller.py
============================
The "hands" of the agent.

This class wraps Playwright and exposes the *exact* set of atomic tools the
assignment requires, one method each:

    open_browser()            launch a Chromium instance
    navigate_to_url(url)      go to a page
    take_screenshot(name)     capture the viewport to a PNG
    click_on_screen(x, y)     left-click at pixel coordinates
    double_click(x, y)        double-click at pixel coordinates
    send_keys(text)           type text into whatever is focused
    scroll(direction, amount) scroll the page

Design rules followed here:
  * Each method does ONE thing and logs what it did.
  * Each method raises a clear, typed error on failure so the agent layer
    can catch it and react.
  * Coordinates are viewport CSS pixels (the same space Playwright's mouse and
    our screenshots use), so a coordinate the agent "sees" maps 1:1 to a click.

The agent layer (LLM or deterministic) never touches Playwright directly —
it only composes these primitives. That separation is what makes the agent
"modular tools that can be composed together".
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)


class BrowserError(Exception):
    """Raised when a browser action cannot be completed."""


class BrowserController:
    def __init__(
        self,
        headless: bool = False,
        slow_mo_ms: int = 300,
        viewport_width: int = 1280,
        viewport_height: int = 900,
        screenshot_dir: str = "screenshots",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.screenshot_dir = screenshot_dir
        self.log = logger or logging.getLogger("agent")

        os.makedirs(self.screenshot_dir, exist_ok=True)

        # Populated by open_browser().
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Remember the last screenshot bytes so the LLM layer can base64 it.
        self._last_screenshot_bytes: Optional[bytes] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def open_browser(self) -> None:
        """TOOL: launch Chromium and open a single blank page.

        deviceScaleFactor=1 keeps screenshot pixels == CSS pixels == mouse
        coordinates, which is essential for reliable coordinate clicking.
        """
        self.log.info("open_browser  -> launching Chromium (headless=%s)", self.headless)
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
            )
            self._context = self._browser.new_context(
                viewport={"width": self.viewport_width, "height": self.viewport_height},
                device_scale_factor=1,
            )
            self._context.set_default_timeout(15_000)
            self.page = self._context.new_page()
            self.log.info("open_browser  -> ready (%dx%d)", self.viewport_width, self.viewport_height)
        except Exception as exc:  # noqa: BLE001 — surface any launch failure clearly
            raise BrowserError(f"Failed to launch browser: {exc}") from exc

    def close(self) -> None:
        """Tear everything down. Safe to call even if open_browser failed."""
        self.log.info("close         -> shutting down browser")
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            try:
                if closer:
                    closer()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

    # ── navigation ───────────────────────────────────────────────────────

    def navigate_to_url(self, url: str) -> None:
        """TOOL: point the browser at `url` and wait for it to settle."""
        self._require_page()
        self.log.info("navigate      -> %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # 'networkidle' can hang on analytics-heavy sites, so it's best-effort.
            try:
                self.page.wait_for_load_state("networkidle", timeout=8_000)
            except PWTimeoutError:
                self.log.debug("networkidle not reached (page still chatty) — continuing")
            # Give a client-rendered (React) form a beat to mount.
            try:
                self.page.wait_for_selector("form, input, textarea", timeout=8_000)
            except PWTimeoutError:
                self.log.warning("No form/input appeared within 8s — page may differ from expected")
            self.log.info("navigate      -> loaded: %s", self.page.title())
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Navigation to {url} failed: {exc}") from exc

    # ── perception ───────────────────────────────────────────────────────

    def take_screenshot(self, name: Optional[str] = None) -> str:
        """TOOL: capture the current viewport to a PNG and return its path."""
        self._require_page()
        if not name:
            name = f"shot_{datetime.now():%H%M%S_%f}"
        if not name.endswith(".png"):
            name += ".png"
        path = os.path.join(self.screenshot_dir, name)
        try:
            self._last_screenshot_bytes = self.page.screenshot(path=path, full_page=False)
            self.log.info("screenshot    -> saved %s", path)
            return path
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Screenshot failed: {exc}") from exc

    def last_screenshot_b64(self) -> Optional[str]:
        """Return the most recent screenshot as base64 (for the vision LLM)."""
        import base64

        if self._last_screenshot_bytes is None:
            return None
        return base64.standard_b64encode(self._last_screenshot_bytes).decode("ascii")

    # ── mouse actions ────────────────────────────────────────────────────

    def click_on_screen(self, x: int, y: int) -> None:
        """TOOL: left-click at viewport pixel (x, y)."""
        self._require_page()
        self.log.info("click         -> (%s, %s)", x, y)
        try:
            self.page.mouse.click(float(x), float(y))
            self.page.wait_for_timeout(150)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Click at ({x}, {y}) failed: {exc}") from exc

    def double_click(self, x: int, y: int) -> None:
        """TOOL: double-click at viewport pixel (x, y).

        Handy for selecting a whole word in a field before retyping.
        """
        self._require_page()
        self.log.info("double_click  -> (%s, %s)", x, y)
        try:
            self.page.mouse.dblclick(float(x), float(y))
            self.page.wait_for_timeout(150)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Double-click at ({x}, {y}) failed: {exc}") from exc

    # ── keyboard actions ─────────────────────────────────────────────────

    def send_keys(self, text: str) -> None:
        """TOOL: type `text` into the currently focused element."""
        self._require_page()
        preview = (text[:40] + "…") if len(text) > 40 else text
        self.log.info("send_keys     -> '%s'", preview)
        try:
            # delay makes the typing visible during the demo and mimics a human.
            self.page.keyboard.type(text, delay=25)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Typing failed: {exc}") from exc

    def clear_focused_field(self) -> None:
        """Helper: select-all + delete so send_keys writes into an empty field.

        Uses 'ControlOrMeta' so it works on both Windows/Linux and macOS.
        """
        self._require_page()
        self.log.debug("clear_field   -> select-all + delete")
        try:
            self.page.keyboard.press("ControlOrMeta+a")
            self.page.keyboard.press("Delete")
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Clearing field failed: {exc}") from exc

    # ── scrolling ────────────────────────────────────────────────────────

    def scroll(self, direction: str = "down", amount: int = 500) -> None:
        """TOOL: scroll the page up or down by `amount` pixels."""
        self._require_page()
        dy = amount if direction.lower() == "down" else -amount
        self.log.info("scroll        -> %s %spx", direction, amount)
        try:
            self.page.mouse.wheel(0, dy)
            self.page.wait_for_timeout(250)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Scroll failed: {exc}") from exc

    # ── internal ─────────────────────────────────────────────────────────

    def _require_page(self) -> None:
        if self.page is None:
            raise BrowserError("Browser not open. Call open_browser() first.")
