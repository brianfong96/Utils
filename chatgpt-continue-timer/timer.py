"""One-shot wall-clock deadline, including time spent asleep."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


def duration_seconds(hours: str, minutes: str, seconds: str) -> int:
    values = []
    for label, raw in zip(("Hours", "Minutes", "Seconds"), (hours, minutes, seconds)):
        if not raw.strip().isascii() or not raw.strip().isdigit():
            raise ValueError(f"{label} must be a whole number, zero or greater.")
        values.append(int(raw))
    total = values[0] * 3600 + values[1] * 60 + values[2]
    if not 1 <= total <= 7 * 24 * 3600:
        raise ValueError("Choose a delay between 1 second and 7 days.")
    return total


def countdown(seconds: float) -> str:
    remaining = max(0, math.ceil(seconds))
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass
class Timer:
    deadline: float | None = None

    def start(self, seconds: int) -> None:
        if not 1 <= seconds <= 604800:
            raise ValueError("Delay must be between 1 second and 7 days.")
        self.deadline = time.time() + seconds

    def cancel(self) -> None:
        self.deadline = None

    def remaining(self) -> float:
        return max(0, self.deadline - time.time()) if self.deadline is not None else 0

    def take_due(self) -> bool:
        if self.deadline is None or time.time() < self.deadline:
            return False
        self.deadline = None
        return True
