"""Tests for the kiosk state machine — the heart of the feature.

Pure logic, no I/O, exact timestamps → deterministic.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.state import KioskStateMachine, State


def test_starts_active():
    m = KioskStateMachine(idle_timeout_seconds=10)
    assert m.state is State.ACTIVE


def test_no_transition_before_timeout():
    m = KioskStateMachine(idle_timeout_seconds=10)
    m.reset(now=100)
    # 9.9s idle: still active
    assert m.tick(now=109.9) is None
    assert m.state is State.ACTIVE


def test_goes_idle_at_timeout():
    m = KioskStateMachine(idle_timeout_seconds=10)
    m.reset(now=100)
    t = m.tick(now=110.0)  # exactly 10s idle
    assert t is not None
    assert t.next is State.IDLE
    assert t.current is State.ACTIVE
    assert t.reason == "idle-timeout"
    assert abs(t.idle_elapsed - 10.0) < 1e-6
    assert m.is_idle


def test_input_before_timeout_keeps_active_and_resets():
    m = KioskStateMachine(idle_timeout_seconds=10)
    m.reset(now=100)
    # input at 105 resets the idle clock (was 5s in)
    assert m.tick(now=105, input_occurred=True) is None
    # now idle must accrue from 105, not 100
    assert m.tick(now=114.9) is None          # 9.9s from input
    assert m.tick(now=115.0) is not None       # 10s 10s from input -> idle


def test_input_returns_from_idle_to_active_instantly():
    m = KioskStateMachine(idle_timeout_seconds=10)
    m.reset(now=100)
    m.tick(now=110.0)  # → idle
    t = m.tick(now=200.0, input_occurred=True)
    assert t is not None
    assert t.current is State.IDLE
    assert t.next is State.ACTIVE
    assert t.reason == "input"


def test_no_input_after_idle_does_nothing():
    m = KioskStateMachine(idle_timeout_seconds=10)
    m.reset(now=100)
    m.tick(now=110.0)  # → idle
    assert m.tick(now=500.0) is None  # stays idle, no state churn
    assert m.is_idle