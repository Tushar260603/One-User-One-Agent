"""Unit tests for Step 1 (Data Models) and Step 2 (State & Ingestion Engine)."""
import os
import json
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

from src.models import (
    TriggerInfo,
    GoalDecision,
    DialectDecision,
    ChecksResult,
    DecisionRecord,
    ActionRecord,
    HomePageRow,
    ViewerState,
    iso_utc,
    parse_iso,
    UTC
)
from src.state_manager import StateManager, CatalogueManager


def test_step1_decision_record_schema():
    """Verify that DecisionRecord strictly matches Candidate Brief Section 4 requirements."""
    decision = DecisionRecord(
        decision_id="D-0417",
        viewer_id="V-2291",
        trigger=TriggerInfo(
            event="app_open",
            received_at="2026-09-12T19:04:11.000Z",
            event_age_s=9.0,
            event_id="E-001"
        ),
        goal=GoalDecision(
            id="first_full_episode_3d",
            name="first_full_episode_3d",
            deadline="2026-09-15T19:04:11.000Z",
            success_signal="episode_completed"
        ),
        action="HOME_PAGE",
        dialect=DialectDecision(
            used="d3",
            source="app_setting",
            conflicts=["profile:d1"]
        ),
        content=["title:T0412", "title:T0977", "title:T1203"],
        confidence=0.61,
        confidence_reason="High affinity for crime genre and recent sign-up",
        evidence=["event:E88123", "catalogue:T0412", "goal_rule:G04-r2"],
        assumptions=["viewer has not started any episode of T0412"],
        checks=ChecksResult(
            passed=["eligibility", "dialect", "frequency_cap"],
            failed=[]
        ),
        decided_in_ms=6400,
        status="PUBLISHED"
    )

    data = decision.model_dump()
    assert data["decision_id"] == "D-0417"
    assert data["viewer_id"] == "V-2291"
    assert data["goal"]["id"] == "first_full_episode_3d"
    assert data["dialect"]["used"] == "d3"
    assert data["dialect"]["conflicts"] == ["profile:d1"]
    assert "eligibility" in data["checks"]["passed"]
    assert data["status"] == "PUBLISHED"


def test_step1_action_record_schema():
    """Verify ActionRecord format conforms to simulator requirements."""
    action = ActionRecord(
        action_id="A-0001",
        decision_id="D-0001",
        user_id="U-00042",
        trigger_event_id="uuid-123",
        type="home_page",
        published_at="2026-10-01T08:00:00.000Z",
        dialect="d2",
        hero_title_id="T0042",
        rows=[
            HomePageRow(
                row_type="popular_in_dialect",
                title_ids=["T0101", "T0102", "T0103"]
            )
        ]
    )
    act_dict = action.model_dump(exclude_none=True)
    assert act_dict["action_id"] == "A-0001"
    assert act_dict["type"] == "home_page"
    assert act_dict["hero_title_id"] == "T0042"
    assert len(act_dict["rows"][0]["title_ids"]) == 3


def test_step2_catalogue_loading_and_availability():
    """Verify CatalogueManager loads catalogue.csv and handles availability."""
    cat = CatalogueManager(".")
    assert len(cat.titles) > 1000
    assert len(cat.episodes_by_slug) > 0
    
    # Check random title
    sample_tid = list(cat.titles.keys())[0]
    t = cat.titles[sample_tid]
    assert "slug" in t
    assert "dialect" in t
    assert "available_from" in t

    # Test availability check
    now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    is_avail = cat.is_available(sample_tid, now)
    assert isinstance(is_avail, bool)

    # Test title withdrawal handling (control event simulation)
    cat.withdraw_title(sample_tid, now)
    # Right at withdrawal time or after, it shouldn't be available
    assert not cat.is_available(sample_tid, now + timedelta(seconds=1))


def test_step2_state_manager_conflict_resolution():
    """Verify that StateManager detects dialect conflict and prioritizes live app feed over profile."""
    sm = StateManager(".")
    
    # Simulate a user with profile dialect d1
    uid = "TEST_USER_999"
    sm.states[uid] = ViewerState(
        user_id=uid,
        preferred_dialect="d1",
        preferred_language="hin",
        subscription_status="never"
    )
    
    # Incoming event with app_dialect d2
    event = {
        "event_id": "EV-TEST-1",
        "user_id": uid,
        "event": "app_open",
        "received_at": "2026-10-01T09:00:00.000Z",
        "app_dialect": "d2",
        "app_language": "hin",
        "session_id": "sess-1"
    }
    sm.apply_event(event)

    state = sm.get_state(uid)
    # Live feed should win
    assert state.last_app_dialect == "d2"
    # Conflict should be captured in provenance
    assert any("profile:d1_vs_app:d2" in c for c in state.dialect_conflicts_observed)
    assert state.session_count == 1


def test_step2_duplicate_event_deduplication():
    """Verify that duplicate events with the same event_id are ignored idempotently."""
    sm = StateManager(".")
    uid = "TEST_USER_DEDUP"
    event = {
        "event_id": "DUP_ID_001",
        "user_id": uid,
        "event": "app_open",
        "received_at": "2026-10-01T09:00:00.000Z",
        "session_id": "sess-dedup"
    }
    sm.apply_event(event)
    assert sm.get_state(uid).session_count == 1

    # Deliver same event again
    sm.apply_event(event)
    assert sm.get_state(uid).session_count == 1  # Still 1, not duplicated!


def test_step2_state_persistence_to_disk():
    """Verify saving viewer state to disk and verifying JSON contents."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(".", state_dir=tmpdir)
        uid = "TEST_USER_DISK"
        sm.states[uid] = ViewerState(
            user_id=uid,
            subscription_status="active",
            play_count=5
        )
        sm.save_state(uid)

        target_file = os.path.join(tmpdir, f"{uid}.json")
        assert os.path.exists(target_file)

        with open(target_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
            assert saved_data["user_id"] == uid
            assert saved_data["subscription_status"] == "active"
            assert saved_data["play_count"] == 5
