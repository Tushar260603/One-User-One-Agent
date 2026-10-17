"""Independent Verification Layer (The Policeman / Guardrail).
Candidate Brief Section 5 requirement:
'Verification: Use checks that can disagree with the decision step.
Generated text is never treated as evidence or as approval.'

Runs independently to validate all proposed interventions before publication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.models import (
    ActionRecord,
    ChecksResult,
    ViewerState,
    HomePageRow,
    parse_iso,
    UTC,
    IST
)
from src.state_manager import CatalogueManager
from src.agent.action_planner import is_quiet_hours, get_ist_day


class IndependentVerifier:
    """Independent verification engine ensuring policy compliance, data integrity, and safety."""

    def __init__(self, catalogue: CatalogueManager):
        self.catalogue = catalogue

    def verify(
        self,
        action: Optional[ActionRecord],
        state: ViewerState,
        is_holdout: bool,
        current_time: datetime
    ) -> Tuple[bool, Optional[ActionRecord], ChecksResult, List[str]]:
        """
        Verify proposed action against strict policy, catalogue availability, and consent rules.
        Returns:
            (is_approved, sanitized_action_or_none, checks_result, failure_reasons)
        """
        passed: List[str] = []
        failed: List[str] = []
        reasons: List[str] = []

        # 1. Holdout Arm Check
        if is_holdout:
            if action is not None:
                failed.append("holdout_protection")
                reasons.append("Action proposed for holdout arm viewer - strictly forbidden.")
                return False, None, ChecksResult(passed=passed, failed=failed), reasons
            else:
                passed.append("holdout_protection")
                return True, None, ChecksResult(passed=passed, failed=failed), []

        # If decision is NO_ACTION
        if action is None:
            passed.append("abstention_valid")
            return True, None, ChecksResult(passed=passed, failed=failed), []

        pub_dt = parse_iso(action.published_at) or current_time
        dialect = action.dialect or state.last_app_dialect or state.preferred_dialect or 'd1'

        # 2. Home Page Verification
        if action.type == "home_page":
            valid_rows: List[HomePageRow] = []
            seen_slugs: Set[str] = set()

            # Hero verification
            hero_ok = False
            if action.hero_title_id:
                t = self.catalogue.titles.get(action.hero_title_id)
                if t and t['dialect'] == dialect and self.catalogue.is_available(action.hero_title_id, pub_dt):
                    hero_ok = True
                    seen_slugs.add(t['slug'])
                    passed.append("hero_valid")
                else:
                    failed.append("hero_invalid")
                    reasons.append(f"Hero title {action.hero_title_id} unavailable or dialect mismatch.")
                    action.hero_title_id = None  # drop invalid hero

            # Rows verification
            dialect_all_matched = True
            availability_all_matched = True

            for row in (action.rows or []):
                keep_tids = []
                for tid in row.title_ids:
                    t = self.catalogue.titles.get(tid)
                    if not t:
                        availability_all_matched = False
                        continue
                    if t['dialect'] != dialect:
                        dialect_all_matched = False
                        continue
                    if not self.catalogue.is_available(tid, pub_dt):
                        availability_all_matched = False
                        continue
                    if t['slug'] in seen_slugs:
                        # Duplicate slug on page not allowed
                        continue

                    seen_slugs.add(t['slug'])
                    keep_tids.append(tid)

                # Platform rule: Row with fewer than 3 valid titles is dropped
                if len(keep_tids) >= 3:
                    valid_rows.append(HomePageRow(row_type=row.row_type, title_ids=keep_tids[:10]))
                else:
                    reasons.append(f"Row {row.row_type} dropped: only {len(keep_tids)} valid titles (minimum 3 required).")

            if dialect_all_matched:
                passed.append("dialect_match")
            else:
                failed.append("dialect_mismatch_detected")

            if availability_all_matched:
                passed.append("catalogue_availability")
            else:
                failed.append("unavailable_titles_pruned")

            # Page must have at least one valid row or valid hero
            if valid_rows or hero_ok:
                action.rows = valid_rows
                passed.append("home_page_layout_valid")
                return True, action, ChecksResult(passed=passed, failed=failed), reasons
            else:
                failed.append("empty_home_page")
                reasons.append("Home page has no valid rows and no hero after verification pruning.")
                return False, None, ChecksResult(passed=passed, failed=failed), reasons

        # 3. Push & WhatsApp Verification
        elif action.type in ("push", "whatsapp"):
            send_dt = parse_iso(action.send_at or action.published_at) or current_time
            send_day = get_ist_day(send_dt)

            # Quiet Hours Check (22:00 to 08:00 IST)
            if is_quiet_hours(send_dt):
                failed.append("quiet_hours")
                reasons.append(f"Message scheduled during quiet hours: {send_dt.isoformat()}.")
                return False, None, ChecksResult(passed=passed, failed=failed), reasons
            else:
                passed.append("quiet_hours")

            # Frequency Cap Check (Max 1 push/whatsapp per IST calendar day)
            already_sent = state.messages_sent_per_day.get(send_day, 0)
            if already_sent >= 1:
                failed.append("frequency_cap")
                reasons.append(f"Frequency cap exceeded: viewer already sent {already_sent} messages on {send_day}.")
                return False, None, ChecksResult(passed=passed, failed=failed), reasons
            else:
                passed.append("frequency_cap")

            # Consent Check
            if action.type == "push":
                if not state.notifications_opt_in or "push" in state.opted_out_channels:
                    failed.append("consent_push")
                    reasons.append("Viewer has not opted in or has opted out of push notifications.")
                    return False, None, ChecksResult(passed=passed, failed=failed), reasons
                else:
                    passed.append("consent_push")

            elif action.type == "whatsapp":
                if not state.whatsapp_opt_in or "whatsapp" in state.opted_out_channels:
                    failed.append("consent_whatsapp")
                    reasons.append("Viewer has not opted in or has opted out of WhatsApp.")
                    return False, None, ChecksResult(passed=passed, failed=failed), reasons
                else:
                    passed.append("consent_whatsapp")

            # Title check for message
            if action.title_id:
                t = self.catalogue.titles.get(action.title_id)
                if not t or t['dialect'] != dialect or not self.catalogue.is_available(action.title_id, send_dt):
                    failed.append("message_title_invalid")
                    reasons.append(f"Message title {action.title_id} is unavailable or dialect mismatch.")
                    return False, None, ChecksResult(passed=passed, failed=failed), reasons
                passed.append("message_title_valid")

            passed.append("message_compliance_valid")
            return True, action, ChecksResult(passed=passed, failed=failed), reasons

        return True, action, ChecksResult(passed=passed, failed=failed), reasons
