"""CLI entrypoint for One User, One Agent.
Provides 1-command replay, simulation, and evaluation.
Candidate Brief Section 8 requirement.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from src.runner import ReplayEngine
from src.evaluator import ImpactEvaluator


def run_pipeline(pack_dir: str, output_dir: str, max_triggers: int = None, seed: str = "oua-practice"):
    """Run full end-to-end pipeline: replay -> simulate -> evaluate."""
    print("=" * 70)
    print("ONE USER, ONE AGENT - END-TO-END PIPELINE")
    print("=" * 70)

    # 1. Replay Loop
    print("\n>>> STEP 1: Running Replay Loop on Triggers & Chaos Events...")
    engine = ReplayEngine(pack_dir=pack_dir, output_dir=output_dir)
    engine.run_replay(max_triggers=max_triggers)

    # 2. Simulator Execution
    print("\n>>> STEP 2: Running Official Simulator on Published Actions...")
    actions_file = os.path.join(output_dir, "actions.jsonl")
    sim_dir = os.path.join(output_dir, "sim")
    os.makedirs(sim_dir, exist_ok=True)

    sim_cmd = [
        sys.executable,
        os.path.join(pack_dir, "simulator", "simulate_outcomes.py"),
        "--pack", pack_dir,
        "--actions", actions_file,
        "--out", sim_dir,
        "--seed", seed
    ]
    res = subprocess.run(sim_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Simulator Error: {res.stderr}")
        sys.exit(res.returncode)
    else:
        print(f"      Simulator completed successfully. Output in {sim_dir}")

    # 3. Impact Evaluation
    print("\n>>> STEP 3: Evaluating Impact against Randomized Holdout...")
    evaluator = ImpactEvaluator(pack_dir=pack_dir, sim_dir=sim_dir, output_dir=output_dir)
    evaluator.evaluate()

    print("\n" + "=" * 70)
    print(f"PIPELINE COMPLETE! Results stored in {output_dir}/")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="One User, One Agent - CLI Interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: run-all (default)
    p_all = subparsers.add_parser("run-all", help="Execute complete replay, simulation, and evaluation")
    p_all.add_argument("--pack", default=".", help="Path to data pack")
    p_all.add_argument("--output", default="sample_run", help="Output directory")
    p_all.add_argument("--max-triggers", type=int, default=None, help="Limit number of triggers (for testing)")
    p_all.add_argument("--seed", default="oua-practice", help="Simulation seed")

    # Command: replay
    p_rep = subparsers.add_parser("replay", help="Run replay loop only")
    p_rep.add_argument("--pack", default=".", help="Path to data pack")
    p_rep.add_argument("--output", default="sample_run", help="Output directory")
    p_rep.add_argument("--max-triggers", type=int, default=None)

    # Command: evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate existing simulation outcomes")
    p_eval.add_argument("--pack", default=".", help="Path to data pack")
    p_eval.add_argument("--sim", default="sample_run/sim", help="Simulation output dir")
    p_eval.add_argument("--output", default="sample_run", help="Report output dir")

    args = parser.parse_args()

    if args.command in (None, "run-all"):
        run_pipeline(
            pack_dir=getattr(args, 'pack', '.'),
            output_dir=getattr(args, 'output', 'sample_run'),
            max_triggers=getattr(args, 'max_triggers', None),
            seed=getattr(args, 'seed', 'oua-practice')
        )
    elif args.command == "replay":
        engine = ReplayEngine(pack_dir=args.pack, output_dir=args.output)
        engine.run_replay(max_triggers=args.max_triggers)
    elif args.command == "evaluate":
        evaluator = ImpactEvaluator(pack_dir=args.pack, sim_dir=args.sim, output_dir=args.output)
        evaluator.evaluate()


if __name__ == "__main__":
    main()
