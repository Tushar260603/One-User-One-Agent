"""Data models and schemas for One User, One Agent.
Strict adherence to the Candidate Brief (Section 4) and Simulator requirements.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO 8601 UTC string format (e.g. 2026-10-01T08:00:00.000Z)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp string with Z or offset into timezone-aware datetime."""
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


# =====================================================================
# Decision Models (Candidate Brief Section 4)
# =====================================================================

class TriggerInfo(BaseModel):
    event: str
    received_at: str
    event_age_s: float
    event_id: Optional[str] = None
    session_id: Optional[str] = None


class GoalDecision(BaseModel):
    id: str
    name: Optional[str] = None
    deadline: str
    success_signal: Optional[str] = None


class DialectDecision(BaseModel):
    used: str
    source: str  # e.g., "app_setting", "profile_snapshot", "fallback"
    conflicts: List[str] = Field(default_factory=list)


class ChecksResult(BaseModel):
    passed: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    """Machine-readable decision log as mandated by Section 4 of the Candidate Brief."""
    decision_id: str
    viewer_id: str
    trigger: TriggerInfo
    goal: Optional[GoalDecision] = None
    action: str  # "HOME_PAGE", "PUSH", "WHATSAPP", "IN_APP_MESSAGE", "NO_ACTION"
    dialect: DialectDecision
    content: List[str] = Field(default_factory=list)  # e.g. ["title:T0101", ...]
    confidence: float
    confidence_reason: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)  # e.g. ["event:uuid", "catalogue:T0101", "goal_rule:G01"]
    assumptions: List[str] = Field(default_factory=list)
    checks: ChecksResult
    decided_in_ms: int
    status: str  # "PUBLISHED", "ABSTAINED", "REJECTED_HOLDOUT", "REJECTED_VERIFICATION", "FALLBACK"


# =====================================================================
# Action Models (Simulator Input Schema)
# =====================================================================

class HomePageRow(BaseModel):
    row_type: str
    title_ids: List[str]


class ActionRecord(BaseModel):
    """Action sent to simulator (must conform to simulator/simulate_outcomes.py)."""
    action_id: str
    decision_id: str
    user_id: str
    trigger_event_id: str
    type: str  # 'home_page', 'push', 'whatsapp', 'in_app_message'
    published_at: str
    dialect: Optional[str] = None
    # Home page fields
    hero_title_id: Optional[str] = None
    rows: Optional[List[HomePageRow]] = None
    # Push/Message fields
    send_at: Optional[str] = None
    title_id: Optional[str] = None
    text: Optional[Dict[str, str]] = None
    deeplink: Optional[str] = None
    # WhatsApp fields
    template_id: Optional[str] = None
    variables: Optional[Dict[str, str]] = None


# =====================================================================
# Viewer State Models (Durable Memory & Provenance)
# =====================================================================

class ViewerState(BaseModel):
    """Durable per-viewer state representing lifetime history, subscription, and goals."""
    user_id: str
    signup_at: Optional[str] = None
    subscription_status: str = "never"  # never, trial_active, active, lapsed
    plan_started_at: Optional[str] = None
    plan_ends_at: Optional[str] = None
    
    # Dialect preferences and observed history
    preferred_dialect: Optional[str] = None  # from profile
    preferred_language: Optional[str] = None
    last_app_dialect: Optional[str] = None   # from app_open feed
    last_app_language: Optional[str] = None
    dialect_conflicts_observed: List[str] = Field(default_factory=list)
    
    # Consents & Policy
    notifications_opt_in: bool = False
    whatsapp_opt_in: bool = False
    opted_out_channels: List[str] = Field(default_factory=list)
    messages_sent_per_day: Dict[str, int] = Field(default_factory=dict)  # "YYYY-MM-DD" -> count
    
    # Engagement stats
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    active_days: List[str] = Field(default_factory=list)  # distinct IST dates
    session_count: int = 0
    play_count: int = 0
    completion_count: int = 0
    genre_affinity: Dict[str, int] = Field(default_factory=dict)
    
    # Content progress tracking: content_slug -> last episode_id watched
    progress: Dict[str, str] = Field(default_factory=dict)
    # Slugs started but not completed: content_slug -> last_played_at
    unfinished_series: Dict[str, str] = Field(default_factory=dict)
    
    # Active Goal tracking
    active_goal_id: Optional[str] = None
    active_goal_name: Optional[str] = None
    active_goal_start: Optional[str] = None
    active_goal_deadline: Optional[str] = None
    active_goal_success_condition: Optional[Dict[str, Any]] = None
    
    # Audit & Provenance
    last_decision_id: Optional[str] = None
    last_action_id: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: iso_utc(datetime.now(timezone.utc)))
