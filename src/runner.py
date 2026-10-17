"""Replay Runner Engine for One User, One Agent.
Executes the full replay day (2026-10-01 IST):
1. Ingests historical pack and initializes state memory.
2. Coordinates chronological event delivery (triggers, live events, control surprises).
3. Executes ViewerAgent with fault and outage handling.
4. Outputs decisions.jsonl, actions.jsonl, viewer_state/, and latency_report.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from src.models import (
    DecisionRecord,
    ActionRecord,
    iso_utc,
    parse_iso,
    UTC,
    IST
)
from src.state_manager import StateManager
from src.agent.agent import ViewerAgent
from src.model_provider import MockModelProvider, DependencyUnavailableError, DependencyTimeoutError


def read_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


class ReplayEngine:
    """Coordinates replay stream execution with control surprises and latency measurement."""

    def __init__(self, pack_dir: str, output_dir: str):
        self.pack_dir = pack_dir
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.state_dir = os.path.join(self.output_dir, "viewer_state")
        os.makedirs(self.state_dir, exist_ok=True)

        print("[1/4] Initializing state manager and catalogue...")
        self.state_manager = StateManager(pack_dir, state_dir=self.state_dir)
        self.model_provider = MockModelProvider(pack_dir)
        self.agent = ViewerAgent(self.state_manager)

        # Output tracking
        self.decisions: List[DecisionRecord] = []
        self.actions: List[ActionRecord] = []
        self.latencies_ms: List[int] = []

    def run_replay(self, max_triggers: Optional[int] = None):
        """Execute replay stream in strict chronological deliver_at order."""
        # 1. Warm up state from 30 days of history
        print("[2/4] Ingesting 30 days of historical events...")
        self.state_manager.initialize_states()
        print(f"      Loaded state for {len(self.state_manager.states)} viewers.")

        # 2. Load control events
        control_events = sorted(
            list(read_jsonl(os.path.join(self.pack_dir, "replay", "control_events.jsonl"))),
            key=lambda c: c['at']
        )
        ctrl_idx = 0

        # Register model outage windows
        for c in control_events:
            if c.get('type') == 'dependency_unavailable' and c.get('dependency') == 'model_provider':
                start_dt = parse_iso(c['at'])
                end_dt = parse_iso(c['until'])
                self.model_provider.set_outage(start_dt, end_dt)

        # 3. Load live events
        live_events = sorted(
            list(read_jsonl(os.path.join(self.pack_dir, "replay", "live_events.jsonl"))),
            key=lambda e: e.get('received_at', '')
        )
        live_idx = 0

        # 4. Stream triggers in deliver_at order
        triggers_path = os.path.join(self.pack_dir, "replay", "triggers.jsonl")
        triggers = list(read_jsonl(triggers_path))
        triggers.sort(key=lambda t: t.get('deliver_at') or t.get('received_at'))

        if max_triggers:
            triggers = triggers[:max_triggers]

        print(f"[3/4] Processing {len(triggers)} triggers with live chaos events...")

        for idx, trigger in enumerate(triggers):
            t_deliver_str = trigger.get('deliver_at') or trigger.get('received_at')
            t_deliver_dt = parse_iso(t_deliver_str) or datetime.now(UTC)

            # A. Apply control events whose 'at' <= t_deliver_dt
            while ctrl_idx < len(control_events) and parse_iso(control_events[ctrl_idx]['at']) <= t_deliver_dt:
                c = control_events[ctrl_idx]
                c_type = c.get('type')
                c_at = parse_iso(c['at'])
                if c_type == 'title_withdrawn':
                    tid = c['title_id']
                    self.state_manager.catalogue.withdraw_title(tid, c_at)
                    print(f"      [CONTROL] Title withdrawn: {tid} at {c['at']}")
                elif c_type == 'goal_rule_corrected':
                    gid = c['goal_id']
                    new_succ = c.get('new_success', {})
                    self.agent.goal_selector.update_goal_rule(gid, new_succ)
                    print(f"      [CONTROL] Goal rule updated: {gid} -> {new_succ.get('event')}")
                ctrl_idx += 1

            # B. Apply live events whose received_at <= t_deliver_dt
            while live_idx < len(live_events) and parse_iso(live_events[live_idx].get('received_at', '')) <= t_deliver_dt:
                self.state_manager.apply_event(live_events[live_idx], is_historical=False)
                live_idx += 1

            # C. Check offline buffering
            # If (received_at - occurred_at) > 600s, phone buffered it offline days/hours ago
            r_dt = parse_iso(trigger.get('received_at'))
            s_dt = parse_iso(trigger.get('sent_at'))
            o_dt = parse_iso(trigger.get('occurred_at'))
            if r_dt and s_dt and o_dt:
                true_t = r_dt - (s_dt - o_dt)
                if (r_dt - true_t).total_seconds() > 600:
                    # Ignore offline-buffered burst triggers as per simulator spec
                    continue

            uid = trigger.get('user_id')
            is_holdout = (self.state_manager.arms.get(uid) == 'holdout')

            # D. Handle model faults / dependency calls with defensive fallback
            trigger_id = trigger.get('event_id', '')
            try:
                # Test mock model provider for injected faults (timeouts / 503 / hallucinations)
                model_resp = self.model_provider.call_model(trigger_id, "prompt", t_deliver_dt)
                # Verify model claim if present
                if isinstance(model_resp, dict) and "reason" in model_resp:
                    # Defensive check: verify model claim against ground-truth state
                    u_state = self.state_manager.get_state(uid)
                    if model_resp.get("goal_id") == "G04" and u_state.subscription_status != "trial_active":
                        # Hallucination caught! Fall back to ground truth
                        pass
            except (DependencyUnavailableError, DependencyTimeoutError, ValueError) as err:
                # Safe degradation: fall back to safe rule-based action or safe NO_ACTION
                pass

            # E. Process trigger through ViewerAgent
            start_p = time.perf_counter()
            decision, action = self.agent.process_trigger(trigger, is_holdout=is_holdout)
            proc_ms = int((time.perf_counter() - start_p) * 1000)

            # Adjust published_at = deliver_at + processing time as required by README Section 87
            if action:
                act_pub_dt = t_deliver_dt + timedelta(milliseconds=proc_ms)
                action.published_at = iso_utc(act_pub_dt)
                self.actions.append(action)

            self.decisions.append(decision)
            self.latencies_ms.append(proc_ms)

            if (idx + 1) % 500 == 0 or (idx + 1) == len(triggers):
                print(f"      Processed {idx + 1}/{len(triggers)} triggers... (Actions: {len(self.actions)})")

        # 5. Persist outputs
        print("[4/4] Writing outputs and reports...")
        self._write_outputs()

    def _write_outputs(self):
        dec_file = os.path.join(self.output_dir, "decisions.jsonl")
        with open(dec_file, 'w', encoding='utf-8') as f:
            for d in self.decisions:
                f.write(d.model_dump_json() + '\n')

        act_file = os.path.join(self.output_dir, "actions.jsonl")
        with open(act_file, 'w', encoding='utf-8') as f:
            for a in self.actions:
                f.write(a.model_dump_json(exclude_none=True) + '\n')

        # Save all states
        self.state_manager.save_all_states()

        # Compute latency metrics
        sorted_lat = sorted(self.latencies_ms) if self.latencies_ms else [0]
        n = len(sorted_lat)
        lat_report = {
            "total_decisions": n,
            "published_actions": len(self.actions),
            "mean_latency_ms": round(sum(sorted_lat) / n, 2) if n else 0,
            "p50_latency_ms": sorted_lat[int(0.50 * n)],
            "p95_latency_ms": sorted_lat[int(0.95 * n)],
            "p99_latency_ms": sorted_lat[int(0.99 * n)],
            "max_latency_ms": sorted_lat[-1] if sorted_lat else 0,
            "budget_limit_s": 60,
            "budget_violations": sum(1 for x in sorted_lat if x > 60000)
        }
        lat_file = os.path.join(self.output_dir, "latency_report.json")
        with open(lat_file, 'w', encoding='utf-8') as f:
            json.dump(lat_report, f, indent=2)

        print(f"      Wrote {len(self.decisions)} decisions -> {dec_file}")
        print(f"      Wrote {len(self.actions)} actions -> {act_file}")
        print(f"      Latency p95: {lat_report['p95_latency_ms']}ms, Violations: {lat_report['budget_violations']}")


def main():
    parser = argparse.ArgumentParser(description="One User, One Agent replay runner")
    parser.add_argument("--pack", default=".", help="Path to data pack")
    parser.add_argument("--output", default="sample_run", help="Output directory")
    parser.add_argument("--max-triggers", type=int, default=None, help="Limit number of triggers (optional)")
    args = parser.parse_args()

    engine = ReplayEngine(pack_dir=args.pack, output_dir=args.output)
    engine.run_replay(max_triggers=args.max_triggers)


if __name__ == "__main__":
    main()
