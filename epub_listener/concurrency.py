"""Shared concurrency option names."""

from typing import Literal

ConcurrencyStrategy = Literal["auto", "sequential", "async", "parallel"]
CONCURRENCY_CHOICES: tuple[ConcurrencyStrategy, ...] = ("auto", "sequential", "async", "parallel")
