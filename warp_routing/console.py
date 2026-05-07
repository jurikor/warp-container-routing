"""Console output helpers."""

from __future__ import annotations

import os
import sys


def supports_color() -> bool:
    """Return whether stdout likely supports ANSI colors."""

    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def green(text: str) -> str:
    """Return text wrapped in green ANSI color when supported."""

    if not supports_color():
        return text
    return f"\033[32m{text}\033[0m"


def yellow(text: str) -> str:
    """Return text wrapped in yellow ANSI color when supported."""

    if not supports_color():
        return text
    return f"\033[33m{text}\033[0m"
