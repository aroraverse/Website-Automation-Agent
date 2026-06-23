"""Website Automation Agent — a mini browser-automation agent.

Public surface:
    BrowserController   the 7 atomic Playwright tools
    DeterministicAgent  rule-based runner (no API key needed)
    LLMAgent            autonomous Claude-driven runner
    GroqAgent           autonomous Groq-driven runner (free tier)
    get_logger          shared logging setup
"""

from .browser_controller import BrowserController, BrowserError
from .deterministic_agent import DeterministicAgent
from .groq_agent import GroqAgent
from .llm_agent import LLMAgent
from .logger import get_logger

__all__ = [
    "BrowserController",
    "BrowserError",
    "DeterministicAgent",
    "LLMAgent",
    "GroqAgent",
    "get_logger",
]
