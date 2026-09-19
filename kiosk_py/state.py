"""The idle/active state machine.

The *browser* page decides visual layer based on real input events, but the
*transition geometry* — when idle starts, how long, fade, whether input
cancels it — lives here so it is unit-testable without a browser. The frame
page's JS mirrors this logic; keeping it in one place documents the contract
and lets tests pin the behavior.

State naming follows digital-signage convention: ACTIVE (content shown) and
IDLE (attract mode / photo frame).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class State(Enum):
    ACTIVE = "active"
    IDLE = "idle"


@dataclass
class Transition:
    current: State
    next: State
    reason: str  # "input" | "idle-timeout"
    idle_elapsed: float


class KioskStateMachine:
    """Deterministic, pure-logic state machine (no I/O).

    Timeline is driven by ``tick(now, input_occurred)`` calls from the engine
    or the test harness. No wall-clock dependence, so tests are exact.
    """

    def __init__(self, idle_timeout_seconds: float = 120.0):
        self.timeout = idle_timeout_seconds
        self.state = State.ACTIVE
        self.last_input_ts: float = 0.0
        self.idle_since_ts: Optional[float] = None

    def reset(self, now: float) -> None:
        self.state = State.ACTIVE
        self.last_input_ts = now
        self.idle_since_ts = now  # idle clock starts at reset (last input)

    def tick(self, now: float, input_occurred: bool = False) -> Optional[Transition]:
        """Advance the machine to time ``now`` and return a transition if state
        changed, else None. ``input_occurred`` = the page saw input since last tick.
        """
        if input_occurred:
            self.last_input_ts = now
            self.idle_since_ts = now
            if self.state is State.IDLE:
                self.state = State.ACTIVE
                return Transition(
                    current=State.IDLE, next=State.ACTIVE,
                    reason="input", idle_elapsed=0.0,
                )
            return None

        # No input this tick.
        if self.state is State.ACTIVE:
            if self.idle_since_ts is None:
                self.idle_since_ts = now
            if now - self.idle_since_ts >= self.timeout:
                self.state = State.IDLE
                return Transition(
                    current=State.ACTIVE, next=State.IDLE,
                    reason="idle-timeout", idle_elapsed=now - self.idle_since_ts,
                )
        return None

    @property
    def is_idle(self) -> bool:
        return self.state is State.IDLE