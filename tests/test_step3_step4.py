"""Unit tests for Step 3 (Goal Selection & Action Planner) and Step 4 (Independent Verifier)."""
from datetime import datetime, timezone, timedelta
import pytest

from src.models import (
    ViewerState,
    GoalDecision,
    ActionRecord,
    HomePageRow,
    iso_utc,
    parse_iso,
    UTC,
    IST
)
from src.state_manager import StateManager
from src.agent.goal_selector import GoalSelector
from src.agent.action_planner import ActionPlanner
from src.verifier.independent_verifier import IndependentVerifier
from src.agent.agent import ViewerAgent


def test_goal_selector_lapsed_user():
    """Verify lapsed subscriber gets reactivate_lapsed_14d (G06)."""
    gs = GoalSelector()
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    state = ViewerState(user_id="U-1", subscription_status="lapsed")
    goal = gs.select_goal(state, now)
    assert goal.id == "G06"
    assert goal.name == "reactivate_lapsed_14d"


def test_goal_selector_trial_user():
    """Verify trial subscriber gets trial_to_paid_7d (G04)."""
    gs = GoalSelector()
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    state = ViewerState(user_id="U-2", subscription_status="trial_active")
    goal = gs.select_goal(state, now)
    assert goal.id == "G04"
    assert goal.name == "trial_to_paid_7d"


def test_goal_selector_unfinished_series():
    """Verify user with unfinished series not watched for 3+ days gets resume_series_3d (G07)."""
    gs = GoalSelector()
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    four_days_ago = now - timedelta(days=4)
    state = ViewerState(
        user_id="U-3",
        subscription_status="active",
        play_count=5,
        unfinished_series={"series-xyz": iso_utc(four_days_ago)}
    )
    goal = gs.select_goal(state, now)
    assert goal.id == "G07"
    assert "series-xyz" in goal.success_signal


def test_goal_rule_update_g03():
    """Verify dynamic update to goal G03 rule from control_events."""
    gs = GoalSelector()
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    state = ViewerState(
        user_id="U-4",
        signup_at=iso_utc(now - timedelta(days=1)),
        subscription_status="never"
    )
    # Default condition is app_open
    goal_before = gs.select_goal(state, now)
    assert goal_before.id == "G03"
    assert goal_before.success_signal == "app_open"

    # Analytics team corrects rule in control_events.jsonl to play_started
    gs.update_goal_rule("G03", {"event": "play_started"})
    state.active_goal_id = None  # reset to pick fresh
    goal_after = gs.select_goal(state, now)
    assert goal_after.id == "G03"
    assert goal_after.success_signal == "play_started"


def test_action_planner_holdout_protection():
    """Verify ActionPlanner returns NO_ACTION for holdout viewers."""
    sm = StateManager(".")
    planner = ActionPlanner(sm.catalogue)
    state = ViewerState(user_id="U-HOLDOUT", subscription_status="never")
    goal = GoalDecision(id="G01", name="first_play_24h", deadline="2026-10-02T10:00:00Z")
    trigger = {"user_id": "U-HOLDOUT", "event": "app_open", "received_at": "2026-10-01T10:00:00Z"}

    action, action_type, evidence, assumptions = planner.plan_action(
        state=state,
        goal=goal,
        trigger_event=trigger,
        is_holdout=True,
        action_counter=1
    )
    assert action is None
    assert action_type == "NO_ACTION"
    assert "arm:holdout" in evidence


def test_independent_verifier_quiet_hours_and_consent():
    """Verify Verifier blocks messages during quiet hours or without consent."""
    sm = StateManager(".")
    verifier = IndependentVerifier(sm.catalogue)

    # 1. Quiet hours (23:30 IST)
    night_time = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)  # 18:00 UTC = 23:30 IST (Quiet hours!)
    state = ViewerState(user_id="U-5", notifications_opt_in=True)
    push_action = ActionRecord(
        action_id="A-TEST-1",
        decision_id="D-TEST-1",
        user_id="U-5",
        trigger_event_id="E-1",
        type="push",
        published_at=iso_utc(night_time),
        send_at=iso_utc(night_time),
        title_id=list(sm.catalogue.titles.keys())[0]
    )
    approved, _, checks, reasons = verifier.verify(push_action, state, is_holdout=False, current_time=night_time)
    assert not approved
    assert "quiet_hours" in checks.failed

    # 2. Daytime but No Consent
    day_time = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 06:00 UTC = 11:30 IST (Daytime)
    state_no_consent = ViewerState(user_id="U-6", notifications_opt_in=False)
    push_action_day = ActionRecord(
        action_id="A-TEST-2",
        decision_id="D-TEST-2",
        user_id="U-6",
        trigger_event_id="E-2",
        type="push",
        published_at=iso_utc(day_time),
        send_at=iso_utc(day_time),
        title_id=list(sm.catalogue.titles.keys())[0]
    )
    approved_c, _, checks_c, _ = verifier.verify(push_action_day, state_no_consent, is_holdout=False, current_time=day_time)
    assert not approved_c
    assert "consent_push" in checks_c.failed


def test_viewer_agent_full_orchestration():
    """Verify ViewerAgent runs complete trigger flow producing DecisionRecord."""
    sm = StateManager(".")
    agent = ViewerAgent(sm)
    trigger = {
        "event_id": "TRIG-001",
        "user_id": "U-AGENT-TEST",
        "event": "app_open",
        "received_at": "2026-10-01T08:00:00.000Z",
        "occurred_at": "2026-10-01T07:59:58.000Z",
        "app_dialect": "d2",
        "session_id": "sess-test"
    }

    decision, action = agent.process_trigger(trigger, is_holdout=False)
    assert decision.decision_id.startswith("D-")
    assert decision.viewer_id == "U-AGENT-TEST"
    assert decision.goal is not None
    assert decision.action == "HOME_PAGE"
    assert decision.status == "PUBLISHED"
    assert decision.decided_in_ms >= 0
    assert "dialect_match" in decision.checks.passed
    assert action is not None
    assert action.dialect == "d2"
    assert len(action.rows) >= 3
