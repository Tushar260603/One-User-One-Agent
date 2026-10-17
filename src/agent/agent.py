"""Agent orchestrator for One User, One Agent.
Owns a single viewer's lifecycle:
1. Gathers and reconciles viewer state.
2. Selects an eligible, time-bounded goal.
3. Plans candidate intervention or decides to abstain.
4. Submits decision to IndependentVerifier before acting.
5. Emits strict DecisionRecord (Section 4) and ActionRecord.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.models import (
    ViewerState,
    GoalDecision,
    DialectDecision,
    TriggerInfo,
    DecisionRecord,
    ActionRecord,
    iso_utc,
    parse_iso,
    UTC
)
from src.state_manager import StateManager, CatalogueManager
from src.agent.goal_selector import GoalSelector
from src.agent.action_planner import ActionPlanner
from src.verifier.independent_verifier import IndependentVerifier


class ViewerAgent:
    """A dedicated agent owning one viewer's lifecycle decisions."""

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.catalogue = state_manager.catalogue
        self.goal_selector = GoalSelector()
        self.action_planner = ActionPlanner(self.catalogue)
        self.verifier = IndependentVerifier(self.catalogue)
        self.decision_counter = 0

    def process_trigger(
        self,
        trigger_event: Dict[str, Any],
        is_holdout: bool
    ) -> Tuple[DecisionRecord, Optional[ActionRecord]]:
        """
        Execute the decide -> verify -> act loop for an incoming app_open or login trigger.
        Meets all requirements of Candidate Brief Section 4 and Section 5.
        """
        start_time = time.perf_counter()
        self.decision_counter += 1
        decision_id = f"D-{self.decision_counter:05d}"

        uid = trigger_event.get('user_id')
        recv_dt = parse_iso(trigger_event.get('received_at')) or datetime.now(UTC)
        occurred_dt = parse_iso(trigger_event.get('occurred_at')) or recv_dt
        event_age_s = max(0.0, (recv_dt - occurred_dt).total_seconds())

        trigger_info = TriggerInfo(
            event=trigger_event.get('event', 'app_open'),
            received_at=iso_utc(recv_dt),
            event_age_s=round(event_age_s, 2),
            event_id=trigger_event.get('event_id'),
            session_id=trigger_event.get('session_id')
        )

        # 1. Update viewer state with current trigger
        self.state_manager.apply_event(trigger_event, is_historical=False)
        state = self.state_manager.get_state(uid)

        # 2. Resolve dialect and capture provenance conflicts
        dialect_used = trigger_event.get('app_dialect') or state.last_app_dialect or state.preferred_dialect or 'd1'
        dialect_source = "app_setting" if trigger_event.get('app_dialect') else "profile_snapshot"
        conflicts = list(state.dialect_conflicts_observed)

        dialect_decision = DialectDecision(
            used=dialect_used,
            source=dialect_source,
            conflicts=conflicts
        )

        # 3. Select Goal
        goal = self.goal_selector.select_goal(state, recv_dt)

        # 4. Plan Candidate Action
        proposed_action, action_type, evidence, assumptions = self.action_planner.plan_action(
            state=state,
            goal=goal,
            trigger_event=trigger_event,
            is_holdout=is_holdout,
            action_counter=self.decision_counter
        )

        # 5. Independent Verification Layer (Police / Guardrail)
        is_approved, final_action, checks_result, failure_reasons = self.verifier.verify(
            action=proposed_action,
            state=state,
            is_holdout=is_holdout,
            current_time=recv_dt
        )

        # 6. Determine final status, content references, and confidence
        content_refs: List[str] = []
        if final_action and final_action.type == "home_page":
            if final_action.hero_title_id:
                content_refs.append(f"hero:{final_action.hero_title_id}")
            for row in (final_action.rows or []):
                for tid in row.title_ids[:3]:
                    content_refs.append(f"title:{tid}")

        # Compute Confidence with explicit reasoning
        confidence = 0.50
        conf_reason = "Base confidence on available profile and event history."
        if is_holdout:
            confidence = 1.0
            conf_reason = "Deterministically abstaining: viewer is assigned to holdout arm."
            status = "REJECTED_HOLDOUT"
        elif not is_approved:
            confidence = 0.90
            conf_reason = f"Action rejected by independent verifier: {'; '.join(failure_reasons)}."
            status = "REJECTED_VERIFICATION"
        elif action_type == "NO_ACTION" or final_action is None:
            confidence = 0.70
            conf_reason = "Abstaining: insufficient dialect inventory or no viable intervention."
            status = "ABSTAINED"
        else:
            # High confidence based on personalization quality
            bonuses = 0.0
            if "hero:top_genre" in str(evidence):
                bonuses += 0.15
            if "row:next_episode_boost" in str(evidence):
                bonuses += 0.20
            confidence = min(0.95, round(0.60 + bonuses, 2))
            conf_reason = f"High confidence: aligned with goal {goal.name} and viewer top affinity in dialect {dialect_used}."
            status = "PUBLISHED"

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        decision_record = DecisionRecord(
            decision_id=decision_id,
            viewer_id=uid,
            trigger=trigger_info,
            goal=goal,
            action="NO_ACTION" if (final_action is None or not is_approved) else final_action.type.upper(),
            dialect=dialect_decision,
            content=content_refs,
            confidence=confidence,
            confidence_reason=conf_reason,
            evidence=evidence,
            assumptions=assumptions,
            checks=checks_result,
            decided_in_ms=elapsed_ms,
            status=status
        )

        # Update viewer state audit trails
        state.last_decision_id = decision_id
        if final_action and is_approved:
            state.last_action_id = final_action.action_id
            if goal:
                state.active_goal_id = goal.id
                state.active_goal_name = goal.name
                state.active_goal_deadline = goal.deadline

        return decision_record, (final_action if is_approved else None)
