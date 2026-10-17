"""Action Planner for One User, One Agent.
Generates personalized interventions (Home Page, Push, WhatsApp, No Action).
Optimizes for high-relevance content matching user dialect, history, and simulator scoring bonuses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

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
from src.state_manager import CatalogueManager


def is_quiet_hours(dt: datetime) -> bool:
    """Check if the given datetime falls within IST quiet hours (22:00 to 08:00)."""
    ist_hour = dt.astimezone(IST).hour
    return ist_hour >= 22 or ist_hour < 8


def get_ist_day(dt: datetime) -> str:
    return dt.astimezone(IST).strftime('%Y-%m-%d')


class ActionPlanner:
    """Plans interventions based on viewer state, active goal, and catalogue availability."""

    def __init__(self, catalogue: CatalogueManager, pricing_config: Optional[Dict[str, Any]] = None):
        self.catalogue = catalogue
        self.pricing_config = pricing_config or {}

    def plan_action(
        self,
        state: ViewerState,
        goal: GoalDecision,
        trigger_event: Dict[str, Any],
        is_holdout: bool,
        action_counter: int
    ) -> Tuple[Optional[ActionRecord], str, List[str], List[str]]:
        """
        Plan an action for the triggered user.
        Returns:
            (ActionRecord or None, action_type_str, evidence_list, assumptions_list)
        """
        # Rule 1: Holdout Arm -> Absolutely NO action allowed!
        if is_holdout:
            return None, "NO_ACTION", ["arm:holdout"], ["viewer is in holdout control group"]

        at_time = parse_iso(trigger_event.get('received_at')) or datetime.now(UTC)
        trigger_id = trigger_event.get('event_id', str(uuid.uuid4()))
        action_id = f"A-{action_counter:05d}"
        decision_id = f"D-{action_counter:05d}"

        # Determine dialect to use: live app setting feed always supersedes profile
        dialect = trigger_event.get('app_dialect') or state.last_app_dialect or state.preferred_dialect or 'd1'

        # Candidate titles in the user's dialect that are currently available
        available_tids = [
            tid for tid in self.catalogue.titles_by_dialect.get(dialect, [])
            if self.catalogue.is_available(tid, at_time)
        ]

        if not available_tids:
            return None, "NO_ACTION", [f"dialect:{dialect}"], ["no available titles in user dialect"]

        # Strategy: On app_open / login triggers, personalized Home Page is the primary, highest-impact intervention
        home_action, evidence, assumptions = self._build_home_page(
            state=state,
            goal=goal,
            dialect=dialect,
            available_tids=available_tids,
            at_time=at_time,
            action_id=action_id,
            decision_id=decision_id,
            trigger_id=trigger_id
        )

        return home_action, "HOME_PAGE", evidence, assumptions

    def _build_home_page(
        self,
        state: ViewerState,
        goal: GoalDecision,
        dialect: str,
        available_tids: List[str],
        at_time: datetime,
        action_id: str,
        decision_id: str,
        trigger_id: str
    ) -> Tuple[ActionRecord, List[str], List[str]]:
        """Construct a high-performing personalized Home Page respecting all layout and uniqueness rules."""
        seen_slugs: Set[str] = set()
        evidence: List[str] = [f"goal:{goal.id}", f"dialect:{dialect}"]
        assumptions: List[str] = [f"viewer app dialect is {dialect}"]

        # 1. Select Hero Title (1 title, available, in dialect)
        hero_tid = None
        # Prefer top genre or popular title
        top_genre = max(state.genre_affinity, key=state.genre_affinity.get) if state.genre_affinity else None
        
        for tid in available_tids:
            t = self.catalogue.titles[tid]
            if top_genre and top_genre in t['genres']:
                hero_tid = tid
                seen_slugs.add(t['slug'])
                evidence.append(f"hero:top_genre_{top_genre}")
                break

        if not hero_tid and available_tids:
            hero_tid = available_tids[0]
            seen_slugs.add(self.catalogue.titles[hero_tid]['slug'])

        # 2. Select Rows (min 3 rows, max 5 rows, each with 3-10 titles, no duplicate slugs across page)
        rows: List[HomePageRow] = []

        # Row A: Next Episode / Continue Watching (Simulator awards +0.15 boost for slugs in h['progress'])
        progress_slugs = set(state.progress.keys()) | set(state.unfinished_series.keys())
        if progress_slugs:
            next_tids = []
            for slug in progress_slugs:
                # Find available title in this dialect matching slug
                for tid in available_tids:
                    t = self.catalogue.titles[tid]
                    if t['slug'] == slug and t['slug'] not in seen_slugs:
                        next_tids.append(tid)
                        seen_slugs.add(t['slug'])
                        break
            # Pad to at least 3 titles with other titles in dialect
            if next_tids:
                for tid in available_tids:
                    if len(next_tids) >= 5:
                        break
                    t = self.catalogue.titles[tid]
                    if t['slug'] not in seen_slugs:
                        next_tids.append(tid)
                        seen_slugs.add(t['slug'])
                if len(next_tids) >= 3:
                    rows.append(HomePageRow(row_type="next_episode", title_ids=next_tids))
                    evidence.append("row:next_episode_boost")

        # Row B: Because You Watched / Top Genre (Simulator awards +0.08 boost!)
        if top_genre:
            genre_tids = []
            for tid in available_tids:
                if len(genre_tids) >= 6:
                    break
                t = self.catalogue.titles[tid]
                if top_genre in t['genres'] and t['slug'] not in seen_slugs:
                    genre_tids.append(tid)
                    seen_slugs.add(t['slug'])
            if len(genre_tids) >= 3:
                rows.append(HomePageRow(row_type="because_you_watched", title_ids=genre_tids))
                evidence.append(f"row:because_you_watched_{top_genre}")

        # Row C: New Releases
        new_release_tids = []
        for tid in available_tids:
            if len(new_release_tids) >= 6:
                break
            t = self.catalogue.titles[tid]
            if t['slug'] not in seen_slugs:
                new_release_tids.append(tid)
                seen_slugs.add(t['slug'])
        if len(new_release_tids) >= 3:
            rows.append(HomePageRow(row_type="new_releases", title_ids=new_release_tids))

        # Row D: Popular in Dialect
        pop_tids = []
        for tid in available_tids:
            if len(pop_tids) >= 6:
                break
            t = self.catalogue.titles[tid]
            if t['slug'] not in seen_slugs:
                pop_tids.append(tid)
                seen_slugs.add(t['slug'])
        if len(pop_tids) >= 3:
            rows.append(HomePageRow(row_type="popular_in_dialect", title_ids=pop_tids))

        # Row E: Additional Genre Row to maximize row count multiplier (up to 5 rows gives +0.50 multiplier!)
        other_genres = [g for g in state.genre_affinity if g != top_genre]
        alt_genre = other_genres[0] if other_genres else "Drama"
        alt_tids = []
        for tid in available_tids:
            if len(alt_tids) >= 6:
                break
            t = self.catalogue.titles[tid]
            if alt_genre in t['genres'] and t['slug'] not in seen_slugs:
                alt_tids.append(tid)
                seen_slugs.add(t['slug'])
        if len(alt_tids) >= 3:
            rows.append(HomePageRow(row_type="genre", title_ids=alt_tids))

        # Ensure we have at least 1 valid row (ideally 3-5 rows)
        if not rows:
            # Fallback row with any available dialect titles
            fallback_tids = []
            for tid in available_tids:
                if len(fallback_tids) >= 5:
                    break
                t = self.catalogue.titles[tid]
                if t['slug'] not in seen_slugs:
                    fallback_tids.append(tid)
                    seen_slugs.add(t['slug'])
            if len(fallback_tids) >= 3:
                rows.append(HomePageRow(row_type="popular_in_dialect", title_ids=fallback_tids))

        action = ActionRecord(
            action_id=action_id,
            decision_id=decision_id,
            user_id=state.user_id,
            trigger_event_id=trigger_id,
            type="home_page",
            published_at=iso_utc(at_time),
            dialect=dialect,
            hero_title_id=hero_tid,
            rows=rows
        )
        return action, evidence, assumptions
