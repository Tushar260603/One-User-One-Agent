"""Model provider and Mock Provider with Fault Injection for One User, One Agent.
Adheres to Candidate Brief Section 7:
'Provide a mock or replay mode so evaluators can run the project without your personal API key.'
And Section 6 (Surprise events) / replay/model_faults.jsonl.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from src.models import parse_iso


class DependencyUnavailableError(Exception):
    """Raised when model_provider dependency is down (e.g. HTTP 503)."""
    pass


class DependencyTimeoutError(Exception):
    """Raised when model provider times out."""
    pass


class MockModelProvider:
    """Mock LLM provider reproducing replay faults and supporting offline evaluation."""

    def __init__(self, pack_dir: str):
        self.pack_dir = pack_dir
        self.faults_by_trigger: Dict[str, Dict[str, Any]] = {}
        self.outage_windows: list[Tuple[datetime, datetime]] = []
        self._load_faults()

    def _load_faults(self):
        faults_file = os.path.join(self.pack_dir, 'replay', 'model_faults.jsonl')
        if os.path.exists(faults_file):
            with open(faults_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        self.faults_by_trigger[record['trigger_event_id']] = record

    def set_outage(self, start_dt: datetime, end_dt: datetime):
        """Record model provider outage window (HTTP 503)."""
        self.outage_windows.append((start_dt, end_dt))

    def is_outage(self, at_time: datetime) -> bool:
        """Check if at_time falls within an active 503 outage window."""
        for start_dt, end_dt in self.outage_windows:
            if start_dt <= at_time <= end_dt:
                return True
        return False

    def call_model(self, trigger_id: str, prompt: str, current_time: datetime) -> Dict[str, Any]:
        """
        Call model, injecting faults from replay/model_faults.jsonl or outage windows if applicable.
        """
        # 1. Check dependency outage window (control_event: dependency_unavailable)
        if self.is_outage(current_time):
            raise DependencyUnavailableError("HTTP 503: model_provider temporarily unavailable")

        # 2. Check specific trigger fault injection
        fault_info = self.faults_by_trigger.get(trigger_id)
        if fault_info:
            fault_type = fault_info.get('fault')
            if fault_type == 'timeout':
                # Replay requires reproducing timeout; in test/replay we raise TimeoutError
                raise DependencyTimeoutError("Model call exceeded latency budget (simulated 45s timeout)")
            
            elif fault_type == 'malformed_output':
                raw_output = fault_info.get('model_output')
                try:
                    return json.loads(raw_output)
                except Exception as e:
                    raise ValueError(f"Malformed JSON from model: {raw_output}") from e

            elif fault_type == 'unsupported_claim':
                # Model returns hallucinated claim unsupported by ground-truth database
                return fault_info.get('model_output', {})

        # Default normal model response: returns clean structured suggestion
        return {
            "recommended_action": "HOME_PAGE",
            "confidence": 0.85,
            "reasoning": "Normal model inference completed successfully."
        }
