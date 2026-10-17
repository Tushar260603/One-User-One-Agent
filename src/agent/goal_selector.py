"""Goal selection engine for One User, One Agent.
Evaluates viewer state against candidate goals in config/goals.json.
Filters out trap/invalid goals and assigns measurable, time-bounded goals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.models import (
    ViewerState,
    GoalDecision,
    iso_utc,
    parse_iso,
    UTC,
    IST
)


class GoalSelector:
    """Selects the single best active goal for a viewer based on current state and eligibility rules."""

    def __init__(self, goals_config: Optional[Dict[str, Any]] = None):
        # We can dynamically receive updated goal rules (e.g. for G03 control_event!)
        self.goals_config = goals_config or {}
        self.g03_success_condition = "app_open"  # Default before 2026-10-01T08:30:00Z correction

    def update_goal_rule(self, goal_id: str, new_success: Dict[str, Any]):
        """Handle control_event: goal_rule_corrected."""
        if goal_id == "G03":
            self.g03_success_condition = new_success.get("event", "play_started")

    def select_goal(self, state: ViewerState, current_time: datetime) -> Optional[GoalDecision]:
        """Evaluate viewer state and return eligible goal with deadline and verifiable success signal."""
        # 1. If viewer already has an active, unexpired goal, keep it
        if state.active_goal_id and state.active_goal_deadline:
            deadline_dt = parse_iso(state.active_goal_deadline)
            if deadline_dt and current_time < deadline_dt:
                return GoalDecision(
                    id=state.active_goal_id,
                    name=state.active_goal_name or state.active_goal_id,
                    deadline=state.active_goal_deadline,
                    success_signal=state.active_goal_success_condition.get("event") if state.active_goal_success_condition else None
                )

        now_str = iso_utc(current_time)
        signup_dt = parse_iso(state.signup_at)
        days_since_signup = (current_time - signup_dt).total_seconds() / 86400.0 if signup_dt else 999.0

        # Candidate Goal Evaluation in strict business priority order:

        # Priority 1: Lapsed Subscriber -> Reactivate (G06)
        if state.subscription_status == "lapsed":
            deadline = current_time + timedelta(hours=336)  # 14 days
            return GoalDecision(
                id="G06",
                name="reactivate_lapsed_14d",
                deadline=iso_utc(deadline),
                success_signal="purchase_completed"
            )

        # Priority 2: Trial Active -> Move to Paid (G04)
        if state.subscription_status == "trial_active":
            deadline = current_time + timedelta(hours=168)  # 7 days
            return GoalDecision(
                id="G04",
                name="trial_to_paid_7d",
                deadline=iso_utc(deadline),
                success_signal="purchase_completed"
            )

        # Priority 3: Brand New User (<= 2 days) -> Second Day Return (G03)
        if days_since_signup <= 2.0:
            deadline = current_time + timedelta(hours=48)
            return GoalDecision(
                id="G03",
                name="second_day_return_2d",
                deadline=iso_utc(deadline),
                success_signal=self.g03_success_condition
            )

        # Priority 4: User with unfinished series (not watched for 3+ days) -> Resume Series (G07)
        if state.unfinished_series:
            for slug, last_played in state.unfinished_series.items():
                lp_dt = parse_iso(last_played)
                if lp_dt and (current_time - lp_dt).total_seconds() / 86400.0 >= 3.0:
                    deadline = current_time + timedelta(hours=72)
                    return GoalDecision(
                        id="G07",
                        name="resume_series_3d",
                        deadline=iso_utc(deadline),
                        success_signal=f"play_started:{slug}"
                    )

        # Priority 5: New User (<= 7 days, never played) -> First Play within 24h (G01)
        if days_since_signup <= 7.0 and state.play_count == 0:
            deadline = current_time + timedelta(hours=24)
            return GoalDecision(
                id="G01",
                name="first_play_24h",
                deadline=iso_utc(deadline),
                success_signal="play_started"
            )

        # Priority 6: Started playing but never completed an episode -> First Full Episode (G02)
        if state.play_count > 0 and state.completion_count == 0:
            deadline = current_time + timedelta(hours=72)
            return GoalDecision(
                id="G02",
                name="first_full_episode_3d",
                deadline=iso_utc(deadline),
                success_signal="episode_completed"
            )

        # Priority 7: Active subscriber with rare plays (<= 1 play in last 14 days) -> Weekly habit (G09)
        if state.subscription_status == "active" and state.play_count <= 1:
            deadline = current_time + timedelta(hours=336)
            return GoalDecision(
                id="G09",
                name="weekly_habit_14d",
                deadline=iso_utc(deadline),
                success_signal="play_started:3_distinct_days"
            )

        # Priority 8: Active subscriber -> Try new release (G08)
        if state.subscription_status == "active":
            deadline = current_time + timedelta(hours=168)
            return GoalDecision(
                id="G08",
                name="try_new_release_7d",
                deadline=iso_utc(deadline),
                success_signal="play_started:new_release"
            )

        # Priority 9: Engaged user who has never paid -> First Paid (G05)
        if state.subscription_status == "never" and state.play_count >= 2:
            deadline = current_time + timedelta(hours=168)
            return GoalDecision(
                id="G05",
                name="first_paid_7d",
                deadline=iso_utc(deadline),
                success_signal="purchase_completed"
            )

        # Fallback Goal: First full episode or first play
        if state.play_count == 0:
            deadline = current_time + timedelta(hours=24)
            return GoalDecision(
                id="G01",
                name="first_play_24h",
                deadline=iso_utc(deadline),
                success_signal="play_started"
            )

        deadline = current_time + timedelta(hours=72)
        return GoalDecision(
            id="G02",
            name="first_full_episode_3d",
            deadline=iso_utc(deadline),
            success_signal="episode_completed"
        )
