"""State Manager and Ingestion Engine for One User, One Agent.
Responsible for:
1. Ingesting 30 days of raw events and profile snapshots.
2. Resolving provenance, conflicts (dialects, stale data, clock skew).
3. Maintaining and persisting durable per-viewer state.
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from src.models import (
    ViewerState,
    iso_utc,
    parse_iso,
    UTC,
    IST
)


def ist_date_str(dt: datetime) -> str:
    """Return IST calendar date string YYYY-MM-DD."""
    return dt.astimezone(IST).strftime('%Y-%m-%d')


class CatalogueManager:
    """Manages titles and episodes, including availability windows and dynamic withdrawals."""
    def __init__(self, pack_dir: str):
        self.pack_dir = pack_dir
        self.titles: Dict[str, Dict[str, Any]] = {}
        self.episodes_by_slug: Dict[str, List[Tuple[int, int, str]]] = defaultdict(list)
        self.titles_by_dialect: Dict[str, List[str]] = defaultdict(list)
        self.titles_by_genre: Dict[Tuple[str, str], List[str]] = defaultdict(list)  # (dialect, genre) -> title_ids
        self._load()

    def _load(self):
        cat_file = os.path.join(self.pack_dir, 'data', 'catalogue.csv')
        with open(cat_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for r in reader:
                tid = r['title_id']
                from_dt = parse_iso(r['available_from'])
                until_dt = parse_iso(r['available_until']) if r['available_until'] else None
                genres = [g.strip() for g in r['genres'].split('|') if g.strip()]
                
                info = {
                    'title_id': tid,
                    'slug': r['content_slug'],
                    'title': r['title'],
                    'dialect': r['dialect'],
                    'language': r['language'],
                    'content_type': r['content_type'],
                    'genres': genres,
                    'release_date': r['release_date'],
                    'available_from': from_dt,
                    'available_until': until_dt,
                    'is_premium': r['is_premium'].lower() == 'true',
                    'description': r.get('description', '')
                }
                self.titles[tid] = info
                self.titles_by_dialect[r['dialect']].append(tid)
                for g in genres:
                    self.titles_by_genre[(r['dialect'], g)].append(tid)

        ep_file = os.path.join(self.pack_dir, 'data', 'episodes.csv')
        with open(ep_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for r in reader:
                slug = r['content_slug']
                season = int(r['season'])
                episode = int(r['episode'])
                ep_id = r['episode_id']
                self.episodes_by_slug[slug].append((season, episode, ep_id))

        for slug in self.episodes_by_slug:
            self.episodes_by_slug[slug].sort()

    def is_available(self, title_id: str, at_time: datetime) -> bool:
        """Check if a title is valid and actively available at a specific timestamp."""
        t = self.titles.get(title_id)
        if not t:
            return False
        if t['available_from'] and at_time < t['available_from']:
            return False
        if t['available_until'] and at_time >= t['available_until']:
            return False
        return True

    def withdraw_title(self, title_id: str, at_time: datetime):
        """Handle control_event: title withdrawn early due to licensing end."""
        if title_id in self.titles:
            current_until = self.titles[title_id]['available_until']
            if current_until is None or at_time < current_until:
                self.titles[title_id]['available_until'] = at_time


class StateManager:
    """Builds and maintains durable per-viewer state with strict provenance and conflict resolution."""
    def __init__(self, pack_dir: str, state_dir: Optional[str] = None):
        self.pack_dir = pack_dir
        self.state_dir = state_dir or os.path.join(pack_dir, 'sample_run', 'viewer_state')
        self.catalogue = CatalogueManager(pack_dir)
        self.states: Dict[str, ViewerState] = {}
        self.seen_event_ids: Set[str] = set()
        self.arms: Dict[str, str] = {}
        self._load_arms()

    def _load_arms(self):
        arms_file = os.path.join(self.pack_dir, 'replay', 'arms.csv')
        if os.path.exists(arms_file):
            with open(arms_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for r in reader:
                    self.arms[r['user_id']] = r['arm']

    def load_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Load profile snapshot, deduplicating multiple records by taking the newest updated_at."""
        profiles = {}
        prof_file = os.path.join(self.pack_dir, 'data', 'profiles_snapshot.csv')
        if not os.path.exists(prof_file):
            return profiles

        with open(prof_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for r in reader:
                uid = r['user_id']
                existing = profiles.get(uid)
                if existing is None or r['updated_at'] > existing['updated_at']:
                    profiles[uid] = r
        return profiles

    def initialize_states(self):
        """Initialize viewer states from profile snapshot and 30-day historical events."""
        profiles = self.load_profiles()
        
        # 1. Initialize states with profile data
        for uid, prof in profiles.items():
            self.states[uid] = ViewerState(
                user_id=uid,
                signup_at=prof.get('signup_at'),
                subscription_status=prof.get('subscription_status', 'never'),
                plan_started_at=prof.get('plan_started_at') or None,
                plan_ends_at=prof.get('plan_ends_at') or None,
                preferred_dialect=prof.get('preferred_dialect'),
                preferred_language=prof.get('preferred_language'),
                notifications_opt_in=prof.get('notifications_opt_in', 'false').lower() == 'true',
                whatsapp_opt_in=prof.get('whatsapp_opt_in', 'false').lower() == 'true',
                updated_at=prof.get('updated_at', iso_utc(datetime.now(UTC)))
            )

        # 2. Ingest 30 days of historical events in order of trusted server received_at
        events_file = os.path.join(self.pack_dir, 'data', 'events.jsonl')
        if os.path.exists(events_file):
            with open(events_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        self.apply_event(event, is_historical=True)

    def apply_event(self, event: Dict[str, Any], is_historical: bool = False):
        """Apply an incoming event (historical or live trigger) to update the viewer's durable state."""
        eid = event.get('event_id')
        if eid and eid in self.seen_event_ids:
            # Duplicate event delivery: ignore idempotently
            return
        if eid:
            self.seen_event_ids.add(eid)

        uid = event.get('user_id')
        if not uid:
            return

        state = self.states.get(uid)
        if state is None:
            state = ViewerState(user_id=uid)
            self.states[uid] = state

        # Use server received_at as the trustworthy clock
        recv_dt = parse_iso(event.get('received_at')) or datetime.now(UTC)
        recv_str = iso_utc(recv_dt)

        if not state.first_seen_at or recv_str < state.first_seen_at:
            state.first_seen_at = recv_str
        if not state.last_seen_at or recv_str > state.last_seen_at:
            state.last_seen_at = recv_str

        # Track app dialect feed and check for dialect conflicts
        ev_dialect = event.get('app_dialect')
        if ev_dialect:
            if state.last_app_dialect and state.last_app_dialect != ev_dialect:
                conflict_msg = f"dialect_shift:{state.last_app_dialect}->{ev_dialect}"
                if conflict_msg not in state.dialect_conflicts_observed:
                    state.dialect_conflicts_observed.append(conflict_msg)
            elif state.preferred_dialect and state.preferred_dialect != ev_dialect:
                conflict_msg = f"profile:{state.preferred_dialect}_vs_app:{ev_dialect}"
                if conflict_msg not in state.dialect_conflicts_observed:
                    state.dialect_conflicts_observed.append(conflict_msg)
            
            # App setting feed on live event always supersedes static profile
            state.last_app_dialect = ev_dialect

        ev_lang = event.get('app_language')
        if ev_lang:
            state.last_app_language = ev_lang

        # Process specific event types
        ev_type = event.get('event')
        props = event.get('properties', {}) or {}

        if ev_type == 'app_open':
            state.session_count += 1
            day_str = ist_date_str(recv_dt)
            if day_str not in state.active_days:
                state.active_days.append(day_str)

        elif ev_type == 'play_started':
            state.play_count += 1
            tid = props.get('title_id')
            t_info = self.catalogue.titles.get(tid)
            if t_info:
                slug = t_info['slug']
                state.unfinished_series[slug] = recv_str
                for g in t_info['genres']:
                    state.genre_affinity[g] = state.genre_affinity.get(g, 0) + 1

        elif ev_type == 'episode_completed':
            state.completion_count += 1
            tid = props.get('title_id')
            ep_id = props.get('episode_id')
            t_info = self.catalogue.titles.get(tid)
            if t_info and ep_id:
                slug = t_info['slug']
                state.progress[slug] = ep_id
                
                # Check if this completed the entire series
                eps = self.catalogue.episodes_by_slug.get(slug, [])
                if eps and eps[-1][2] == ep_id:
                    # Last episode finished, no longer unfinished
                    state.unfinished_series.pop(slug, None)

        elif ev_type == 'purchase_completed':
            is_trial = props.get('is_trial', False)
            state.subscription_status = 'trial_active' if is_trial else 'active'
            state.plan_started_at = recv_str

        elif ev_type == 'subscription_renewed':
            state.subscription_status = 'active'

        elif ev_type == 'subscription_lapsed':
            state.subscription_status = 'lapsed'

        elif ev_type == 'notification_opt_out':
            channel = props.get('channel')
            if channel:
                if channel not in state.opted_out_channels:
                    state.opted_out_channels.append(channel)
                if channel == 'push':
                    state.notifications_opt_in = False
                elif channel == 'whatsapp':
                    state.whatsapp_opt_in = False

        state.updated_at = recv_str

    def get_state(self, user_id: str) -> ViewerState:
        """Get the current state for a viewer, creating one if not present."""
        if user_id not in self.states:
            self.states[user_id] = ViewerState(user_id=user_id)
        return self.states[user_id]

    def save_state(self, user_id: str):
        """Persist a single viewer state to disk in JSON format."""
        if user_id not in self.states:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        file_path = os.path.join(self.state_dir, f"{user_id}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(self.states[user_id].model_dump_json(indent=2))

    def save_all_states(self):
        """Save all loaded viewer states to disk."""
        os.makedirs(self.state_dir, exist_ok=True)
        for uid in self.states:
            self.save_state(uid)
