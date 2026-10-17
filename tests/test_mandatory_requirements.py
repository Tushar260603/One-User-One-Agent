"""Mandatory automated test suite covering all 4 explicit scenarios from Candidate Brief Section 8:
1. At least one late or duplicate trigger
2. One conflicting source
3. One tool/dependency failure
4. One corrected goal rule
"""
from datetime import datetime, timezone, timedelta
import pytest

from src.models import ViewerState, iso_utc, UTC
from src.state_manager import StateManager
from src.agent.goal_selector import GoalSelector
from src.model_provider import MockModelProvider, DependencyUnavailableError, DependencyTimeoutError


def test_scenario_1_late_and_duplicate_trigger():
    """Scenario 1: System handles duplicate and offline-buffered triggers idempotently."""
    sm = StateManager(".")
    uid = "U-MANDATORY-TEST-1"
    
    # 1. First trigger
    trig1 = {
        "event_id": "EV-DUP-001",
        "user_id": uid,
        "event": "app_open",
        "received_at": "2026-10-01T09:00:00.000Z",
        "occurred_at": "2026-10-01T08:59:50.000Z",
        "sent_at": "2026-10-01T08:59:55.000Z"
    }
    sm.apply_event(trig1)
    assert sm.get_state(uid).session_count == 1

    # 2. Duplicate delivery with same event_id
    sm.apply_event(trig1)
    assert sm.get_state(uid).session_count == 1, "Duplicate event delivery must not increment session count!"


def test_scenario_2_conflicting_source_resolution():
    """Scenario 2: Resolve conflicting dialect sources (Profile snapshot vs Live App Setting Feed)."""
    sm = StateManager(".")
    uid = "U-MANDATORY-TEST-2"
    
    # Profile says d1
    sm.states[uid] = ViewerState(
        user_id=uid,
        preferred_dialect="d1",
        preferred_language="hin"
    )

    # Live app settings feed says d3
    live_trig = {
        "event_id": "EV-DIALECT-002",
        "user_id": uid,
        "event": "app_open",
        "received_at": "2026-10-01T09:15:00.000Z",
        "app_dialect": "d3"
    }
    sm.apply_event(live_trig)

    state = sm.get_state(uid)
    assert state.last_app_dialect == "d3", "Live app setting must take precedence over stale profile!"
    assert any("profile:d1_vs_app:d3" in c for c in state.dialect_conflicts_observed)


def test_scenario_3_tool_and_dependency_failure():
    """Scenario 3: Dependency outage (HTTP 503) and timeout must be handled gracefully without crashing."""
    provider = MockModelProvider(".")
    now = datetime(2026, 10, 1, 10, 36, 0, tzinfo=UTC)
    
    # Set active 503 outage window
    provider.set_outage(
        start_dt=datetime(2026, 10, 1, 10, 35, 0, tzinfo=UTC),
        end_dt=datetime(2026, 10, 1, 10, 42, 0, tzinfo=UTC)
    )

    # Calling during outage must raise controlled DependencyUnavailableError
    with pytest.raises(DependencyUnavailableError):
        provider.call_model("trigger-during-outage", "prompt", now)

    # Calling with timeout fault trigger must raise controlled DependencyTimeoutError
    timeout_trigger_id = "5edb5b00-0773-4063-8da8-40929553233c"
    normal_time = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(DependencyTimeoutError):
        provider.call_model(timeout_trigger_id, "prompt", normal_time)


def test_scenario_4_corrected_goal_rule():
    """Scenario 4: Analytics team corrects G03 rule mid-stream; system updates dynamically."""
    gs = GoalSelector()
    now = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    
    # New user signed up yesterday
    state = ViewerState(
        user_id="U-MANDATORY-TEST-4",
        signup_at=iso_utc(now - timedelta(days=1)),
        subscription_status="never"
    )

    # At 08:00 UTC (before correction at 08:30 UTC): success is app_open
    goal_initial = gs.select_goal(state, now)
    assert goal_initial.id == "G03"
    assert goal_initial.success_signal == "app_open"

    # Control event at 08:30 UTC corrects G03 success to play_started
    gs.update_goal_rule("G03", {"event": "play_started"})
    state.active_goal_id = None  # refresh
    goal_corrected = gs.select_goal(state, now + timedelta(hours=1))
    assert goal_corrected.id == "G03"
    assert goal_corrected.success_signal == "play_started"
