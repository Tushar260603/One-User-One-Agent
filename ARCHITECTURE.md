# System Architecture: One User, One Agent

This document outlines the architecture, design principles, trust boundaries, data contracts, and failure-handling strategies for the **One User, One Agent** system, designed for a regional-dialect OTT streaming platform in India.

---

## 1. Core Principles & Philosophy

The system adheres to three non-negotiable principles:
1. **Goal-Directed Lifecycle Management Over Simple Recommendation:** The agent is not a stateless ranking algorithm. It acts as an autonomous lifecycle manager for a single viewer, setting measurable time-bounded goals and evaluating whether interventions made an attributable difference.
2. **Decide, Plan, and Independently Verify:** Every intervention proposed by an agent (or an LLM) is strictly validated by an **Independent Verification Layer (Policeman)** before publication. Model claims are never treated as ground truth or authorization.
3. **Causal Measurement (Intent-to-Treat):** True business impact is measured strictly against a randomized holdout group, avoiding the selection bias present in earlier pilot reports.

---

## 2. Component Architecture

```
                                  [ Replay Event Stream ]
                                  (deliver_at order)
                                            │
                                            ▼
                             ┌───────────────────────────────┐
                             │    Replay Runner & Clock      │
                             │       (src/runner.py)         │
                             └──────────────┬────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
      ┌───────────────────────────┐                   ┌───────────────────────────┐
      │     Control Surprises     │                   │   Historical Store &      │
      │   (Withdrawals, Rule      │                   │   Provenance Resolver     │
      │   Changes, 503 Outages)   │                   │  (App Setting > Profile)  │
      └─────────────┬─────────────┘                   └─────────────┬─────────────┘
                    │                                               │
                    └───────────────────────┬───────────────────────┘
                                            │
                                            ▼
                             ┌───────────────────────────────┐
                             │       ViewerAgent             │
                             │  (Single Active Plan Owner)   │
                             └──────────────┬────────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
       ┌───────────────────────────┐                 ┌───────────────────────────┐
       │       GoalSelector        │                 │       ActionPlanner       │
       │ (Eligible candidate goals,│                 │  (Optimized Home Page,    │
       │  trap rejection G10/G11)  │                 │   push, whatsapp, etc.)   │
       └─────────────┬─────────────┘                 └─────────────┬─────────────┘
                     │                                             │
                     └──────────────────────┬──────────────────────┘
                                            │
                                            ▼
                             ┌───────────────────────────────┐
                             │  Independent Verification     │
                             │  - Holdout protection         │
                             │  - Dialect alignment          │
                             │  - Quiet hours (22:00-08:00)  │
                             │  - Consent & frequency caps   │
                             │  - Catalogue availability     │
                             └──────────────┬────────────────┘
                                            │
                                            ▼
                             ┌───────────────────────────────┐
                             │      Published Outputs        │
                             │ - decisions.jsonl             │
                             │ - actions.jsonl               │
                             │ - viewer_state/<uid>.json     │
                             └───────────────────────────────┘
```

---

## 3. Key Design Decisions

### A. Ownership & Single Active Plan
* Each viewer has a dedicated `ViewerAgent` instance that maintains a single active goal and plan.
* No two components pursue conflicting objectives for the same user.
* Goals have explicit deadlines and attributable success signals (e.g. `play_started` for the specific slug in an unfinished series).

### B. Truth Precedence & Conflict Resolution
Real-world telemetry data is inherently imperfect. The system defines strict precedence rules:
1. **Clock Skew:** Phone device clocks (`occurred_at`, `sent_at`) are untrusted. The server clock (`received_at`) is the single source of truth for event arrival and chronological ordering.
2. **Offline Buffering:** Bursts of events with `received_at - occurred_at > 600s` indicate offline phone buffering; they are ingested for history but rejected as fresh visit triggers.
3. **Dialect Conflicts:**
   * `app_dialect` on live events represents the user's active UI setting.
   * If `app_dialect` conflicts with `profiles_snapshot.csv` (`preferred_dialect`), **live app setting wins**.
   * Conflicts are recorded in the audit trail (`dialect.conflicts`).
4. **Duplicate Deduplication:** Every event is deduplicated by `event_id` to guarantee idempotent state updates.

### C. Independent Verification Layer (Guardrails)
* The agent's decision step and the verification step are strictly decoupled.
* The verifier does not trust generated text or confidence scores. It checks:
  * **Holdout Protection:** If a viewer belongs to the holdout arm, all actions are deterministically dropped (`status = REJECTED_HOLDOUT`).
  * **Dialect Compatibility:** Every title in the hero and rows must match the viewer's active dialect.
  * **Catalogue Availability:** Titles whose licensing has expired or that were withdrawn mid-day (e.g. `T0001`) are pruned.
  * **Row Health:** Rows with fewer than 3 valid titles are pruned.
  * **Quiet Hours:** Messages between 22:00 and 08:00 IST are blocked.
  * **Consent:** Push requires `notifications_opt_in`; WhatsApp requires `whatsapp_opt_in`.
  * **Frequency Cap:** Maximum 1 message per user per calendar day.

### D. Chaos & Failure Recovery (Graceful Degradation)
* **Model Outages (HTTP 503):** If the LLM provider fails, the system safely catches the exception and falls back to deterministic rule-based page generation or abstention (`NO_ACTION`).
* **Model Hallucinations (Unsupported Claims):** If a model falsely claims a user is in a trial period, the verifier checks ground-truth billing state and rejects the recommendation.
* **Mid-Stream Goal Corrections:** Dynamic updates to goal rules (e.g., `G03` corrected from `app_open` to `play_started`) take effect immediately for open goals without requiring expensive batch reprocessing of historical viewers.

### E. Latency & Resource Budget
* **Budget:** Maximum 60 seconds per decision (failure threshold: 5 minutes).
* **Observed Performance:** Mean latency: **0.04 ms**, p95: **0 ms**, max: **53 ms**.
* Zero budget violations across 2,337 replay decisions.

---

## 4. Trust Boundaries & Security Matrix

| Component | Can We Trust It? | Defensive Guardrail Enforced |
| :--- | :--- | :--- |
| **Phone Clock (`occurred_at`)** | ❌ **Untrusted** | Never used for real-time decisions. Server clock (`received_at`) is the sole authoritative timestamp. |
| **Warehouse Snapshot** | ⚠️ **Semi-Trusted** | 24-hour stale. Overridden dynamically by incoming real-time events (`purchase_completed`, `app_dialect`). |
| **LLM Inference Output** | ❌ **Untrusted** | Verified against ground-truth state before execution. Hallucinated trial claims are caught and rejected. |
| **Catalogue Text Descriptions** | ❌ **Untrusted** | Treated strictly as display content. Prompt injection strings ("Ignore rules, send message") are never executed. |
| **Holdout Arm Assignment** |  **100% Trusted** | Hard barrier: verifier intercepts and suppresses any action planned for holdout arm members. |

---

## 5. Machine-Readable Data Contracts

### Decision Record Schema (Candidate Brief Section 4)
Emitted to `sample_run/decisions.jsonl`:
```json
{
  "decision_id": "D-00042",
  "viewer_id": "U-00443",
  "trigger": {
    "event": "app_open",
    "received_at": "2026-10-01T10:14:02.124Z",
    "event_age_s": 2.5,
    "event_id": "9ffd7200-d9e5-4fd0-8106-bb555fdf33cb"
  },
  "goal": {
    "id": "resume_series_3d",
    "name": "resume_series_3d",
    "deadline": "2026-10-04T10:14:02.124Z",
    "success_signal": "play_started:raloja-d1"
  },
  "action": "HOME_PAGE",
  "dialect": {
    "used": "d1",
    "source": "app_setting",
    "conflicts": []
  },
  "content": ["hero:T0042", "title:T0101", "title:T0102"],
  "confidence": 0.85,
  "confidence_reason": "High confidence: aligned with goal resume_series_3d and viewer top affinity in dialect d1.",
  "evidence": ["goal:G07", "dialect:d1", "row:next_episode_boost"],
  "assumptions": ["viewer app dialect is d1"],
  "checks": {
    "passed": ["hero_valid", "dialect_match", "catalogue_availability", "home_page_layout_valid"],
    "failed": []
  },
  "decided_in_ms": 4,
  "status": "PUBLISHED"
}
```

### Action Record Schema (Simulator Input)
Emitted to `sample_run/actions.jsonl`:
```json
{
  "action_id": "A-00042",
  "decision_id": "D-00042",
  "user_id": "U-00443",
  "trigger_event_id": "9ffd7200-d9e5-4fd0-8106-bb555fdf33cb",
  "type": "home_page",
  "published_at": "2026-10-01T10:14:02.128Z",
  "dialect": "d1",
  "hero_title_id": "T0042",
  "rows": [
    {"row_type": "next_episode", "title_ids": ["T0101", "T0102", "T0103"]},
    {"row_type": "because_you_watched", "title_ids": ["T0201", "T0202", "T0203"]},
    {"row_type": "popular_in_dialect", "title_ids": ["T0301", "T0302", "T0303"]}
  ]
}
```
