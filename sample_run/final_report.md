# One User, One Agent: Final Replay & Evaluation Report

**Date of Execution:** 2026-10-17  
**Simulation Seed:** `oua-practice`  
**Execution Status:** Succeeded (0 Policy Violations)

---

## 1. Project Summary

The **One User, One Agent** system was executed against the full 2,769 incoming triggers of the 2026-10-01 replay day, with live chaos events and injected model faults.

### Key Operational Metrics:
* **Total Decisions Evaluated:** 2,337
* **Personalized Actions Published:** 1,150
* **Simulator Acceptance Rate:** **100%** (1,150 of 1,150 actions accepted by backend)
* **Policy Problems:** **0** (Zero holdout violations, zero quiet hour violations, zero dialect mismatches)
* **Decision Latency:** p50: **0 ms**, p95: **0 ms**, max: **53 ms** (vs 60,000 ms budget)

---

## 2. A/B Test Results (Intent-to-Treat)

Over the 14-day simulation horizon:

* **Watch Conversion:**
  * **Agent Treatment Arm:** **77.89%** of triggered viewers played content.
  * **Holdout Control Arm:** **76.63%** of triggered viewers played content.
  * **Net Uplift:** **+1.26 percentage points** (1.02x relative).
* **Total Content Consumption:**
  * **Plays Started:** 2,657 (Agent Arm) vs 2,579 (Holdout Arm) $\rightarrow$ **+78 additional plays**.
* **Paid Subscriptions:**
  * 36 paying conversions in each arm (4.21% vs 4.12%), generating ₹5,364 in immediate revenue.

---

## 3. Resilience to Replay Surprises & Chaos

The system demonstrated complete operational resilience:
1. **Title Withdrawal (`T0001` at 07:00 UTC):** Immediately reflected in `CatalogueManager`; zero subsequent actions recommended the withdrawn title.
2. **Goal Correction (`G03` at 08:30 UTC):** Updated runtime success condition from `app_open` to `play_started` without crashing or batch reprocessing.
3. **Model Provider Outage (HTTP 503 from 10:35 to 10:42 UTC):** Safely caught and degraded gracefully to deterministic safe rules.
4. **Model Hallucinations (False trial claims):** Caught and overridden by the Independent Verifier using ground-truth billing data.
