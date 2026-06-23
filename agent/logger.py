"""
agent/logger.py
===============
A tiny wrapper around Python's `logging` so the whole project logs the same
way: timestamped lines to the console AND to a file under `logs/`.

Good logging is one of the grading criteria, and during a viva it lets you
narrate exactly what the agent decided and when.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime


def get_logger(name: str = "agent", log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """Return a configured logger. Safe to call multiple times (no dup handlers)."""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:  # already configured — reuse it
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler — what you see live during the demo.
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler — a permanent record of the run.
    logfile = os.path.join(log_dir, f"run_{datetime.now():%Y%m%d_%H%M%S}.log")
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.debug("Logger initialised. Writing to %s", logfile)
    return logger
