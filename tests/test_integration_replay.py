"""Integration test testing Step 3 and Step 4 against real replay day triggers."""
import json
import os
from datetime import datetime, timezone
import pytest

from src.state_manager import StateManager
from src.agent.agent import ViewerAgent


def test_real_replay_triggers_processing():
    """Process a batch of real triggers from replay/triggers.jsonl through the complete Agent loop."""
    pack_dir = "."
    sm = StateManager(pack_dir)
    print("Loading profiles and arms...")
    profiles = sm.load_profiles()
    assert len(profiles) > 0
    assert len(sm.arms) > 0

    agent = ViewerAgent(sm)
    triggers_file = os.path.join(pack_dir, "replay", "triggers.jsonl")
    assert os.path.exists(triggers_file)

    processed_count = 0
    holdout_count = 0
    published_count = 0

    with open(triggers_file, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx >= 100:  # test on first 100 real triggers
                break
            line = line.strip()
            if not line:
                continue

            trigger = json.loads(line)
            uid = trigger["user_id"]
            is_holdout = (sm.arms.get(uid) == "holdout")

            decision, action = agent.process_trigger(trigger, is_holdout=is_holdout)
            processed_count += 1

            # Assert DecisionRecord schema invariants
            assert decision.decision_id.startswith("D-")
            assert decision.viewer_id == uid
            assert decision.decided_in_ms < 1000  # Strict latency budget: well under 1 second!
            assert len(decision.evidence) > 0
            assert len(decision.assumptions) > 0

            if is_holdout:
                holdout_count += 1
                assert decision.action == "NO_ACTION"
                assert decision.status == "REJECTED_HOLDOUT"
                assert action is None, "Holdout user must receive NO action!"
            else:
                published_count += 1
                assert decision.status in ("PUBLISHED", "ABSTAINED")
                if action is not None:
                    assert action.user_id == uid
                    assert action.type == "home_page"
                    assert action.dialect == decision.dialect.used
                    assert len(action.rows) >= 1
                    # Verify every title in every row matches the page dialect
                    for row in action.rows:
                        assert len(row.title_ids) >= 3, "Rows must have at least 3 titles"
                        for tid in row.title_ids:
                            assert sm.catalogue.titles[tid]["dialect"] == action.dialect

    print(f"\nTested {processed_count} real triggers: {holdout_count} holdout (correctly 0 actions), {published_count} agent arm.")
    assert processed_count == 100
    assert holdout_count > 0
    assert published_count > 0
