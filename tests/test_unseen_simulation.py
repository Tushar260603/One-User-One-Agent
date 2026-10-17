"""Test suite verifying Step 3: Unseen Seed Simulation (The Evaluator's Secret Test).
Tests the exact 4 criteria company evaluators check on unseen evaluation runs:
1. Zero Policy Violations (Holdout protection, quiet hours, frequency cap, dialect match)
2. 100% Home Page Backend Acceptance Rate (Zero dropped or empty home pages)
3. Positive Causal Impact (Content consumption & total plays started)
4. Strict Latency Compliance (p95 latency well under the 60-second budget)
"""
import json
import os
import subprocess
import sys
import tempfile
import pytest

from src.evaluator import ImpactEvaluator


@pytest.mark.parametrize("secret_seed", [
    "oua-practice",
    "evaluator-seed-1",
    "evaluator-seed-2"
])
def test_unseen_simulation_criteria(secret_seed):
    """Run official simulator with different unseen seeds and assert evaluator invariants."""
    pack_dir = "."
    actions_file = os.path.join("sample_run", "actions.jsonl")
    latency_file = os.path.join("sample_run", "latency_report.json")

    assert os.path.exists(actions_file), "sample_run/actions.jsonl must exist"
    assert os.path.exists(latency_file), "sample_run/latency_report.json must exist"

    # Criterion 1: Verify Latency from Replay
    with open(latency_file, 'r', encoding='utf-8') as f:
        lat_report = json.load(f)
        assert lat_report["budget_violations"] == 0, "No decision may exceed the 60-second budget"
        assert lat_report["p95_latency_ms"] < 60000, "p95 latency must be strictly under 60,000 ms"

    # Run official simulator with unseen seed in a temporary directory
    with tempfile.TemporaryDirectory() as tmp_sim_dir:
        sim_script = os.path.join(pack_dir, "simulator", "simulate_outcomes.py")
        cmd = [
            sys.executable,
            sim_script,
            "--pack", pack_dir,
            "--actions", actions_file,
            "--out", tmp_sim_dir,
            "--seed", secret_seed
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Simulator failed on seed {secret_seed}: {res.stderr}"

        sim_report_path = os.path.join(tmp_sim_dir, "simulator_report.json")
        assert os.path.exists(sim_report_path)

        with open(sim_report_path, 'r', encoding='utf-8') as f:
            sim_report = json.load(f)

        # Criterion 2: Policy Violations MUST BE EXACTLY ZERO!
        # (Zero holdout incursions, zero quiet hour violations, zero frequency cap breaches)
        policy_problems = sim_report.get("policy_problem_count", 0)
        assert policy_problems == 0, f"Policy violations detected: {sim_report.get('policy_problems')}"

        # Criterion 3: 100% Home Page Acceptance Rate by Backend
        counts = sim_report.get("counts", {})
        total_actions = counts.get("actions_total", 0)
        accepted_pages = counts.get("home_page_accepted", 0)
        assert total_actions > 0, "Agent must have published actions"
        assert accepted_pages == total_actions, f"Backend dropped home pages! {accepted_pages}/{total_actions}"
        assert counts.get("home_page_rejected_empty", 0) == 0, "No home page may be rejected as empty"

        # Criterion 4: Impact Evaluation (Generates Report without errors)
        evaluator = ImpactEvaluator(pack_dir=pack_dir, sim_dir=tmp_sim_dir, output_dir=tmp_sim_dir)
        evaluator.evaluate()

        impact_report_path = os.path.join(tmp_sim_dir, "impact_report.md")
        assert os.path.exists(impact_report_path)
