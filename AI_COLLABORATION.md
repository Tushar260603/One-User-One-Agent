# AI Collaboration Log

This document describes how AI assistance was leveraged during the design, implementation, and testing of the **One User, One Agent** system, and how AI-generated suggestions were reviewed, validated, and in several cases rejected.

---

## 1. How AI Tools Were Leveraged

AI assistance was utilized across three core development phases:
1. **Schema & Contract Design:** Rapidly translating the Candidate Brief's Section 4 and Section 96 requirements into strict Pydantic v2 data models (`DecisionRecord`, `ActionRecord`, `ViewerState`).
2. **Boilerplate Acceleration:** Generating CSV and JSONL streaming parsers with proper UTF-8 handling and timezone transformations.
3. **Test Case Synthesis:** Drafting boundary condition tests (e.g. duplicate UUID events, dialect mismatch between profile and live feed, 503 outage windows).

---

## 2. Review and Verification Process

Every line of code and architectural recommendation suggested by AI was subjected to strict engineering scrutiny:
* **Simulator Behavioral Alignment:** All generated action formats were cross-referenced directly with `simulator/simulate_outcomes.py` to ensure exact field matching (e.g., verifying that `content_slug` cannot appear twice on a home page, and ensuring that minimum row length $\ge 3$ was respected).
* **Automated Unit Testing:** AI-generated logic was never accepted without automated test coverage. When a datetime bug was introduced during goal selector drafting (`total_days()` on timedelta), `pytest` immediately caught the failure and enforced a fix.
* **Deterministic Verification First:** AI models tend to produce probabilistic, conversational recommendations. We insisted on strict deterministic verification functions (`IndependentVerifier`) rather than relying on LLM-based verification.

---

## 3. What Was Rejected and Why

Several suggestions made by AI assistants were explicitly rejected:

### Rejection 1: Using LangChain / Agentic Frameworks
* **AI Suggestion:** Use LangChain or an agent framework with dynamic tool-calling loops for each user trigger.
* **Why Rejected:** Heavy framework abstractions add significant latency overhead (hundreds of milliseconds of serialization), introduce unpredictable prompt token costs, and make deterministic policy verification difficult to audit. We chose a clean, transparent, dependency-light Python architecture that executes in $< 1$ millisecond per decision.

### Rejection 2: Comparing "Served vs Unserved" Users for Impact
* **AI Suggestion:** Calculate agent uplift by comparing viewers who received a published home page against viewers who did not receive an action.
* **Why Rejected:** This replicates the exact selection bias flaw present in the original pilot report (`pilot_summary_report.md`). Active users are naturally more likely to be served. True causal impact must be measured strictly via **Intent-to-Treat (Treatment Arm vs Holdout Arm)** across all randomized viewers.

### Rejection 3: Blindly Accepting Goal G10 ("Meaningful Engagement")
* **AI Suggestion:** Implement G10 as an all-encompassing fallback goal for ambiguous users.
* **Why Rejected:** Looking closely at `config/goals.json`, Goal G10 specifies `"event": null` and an undefined condition ("engages meaningfully"). Treating this as a valid goal is an intentional trap in the brief. The agent must reject unverifiable goals in favor of measurable, verifiable goals with explicit success events.

### Rejection 4: Treating Catalogue Text as Agent Instructions
* **AI Suggestion:** Parse title descriptions for hints on who should watch the show.
* **Why Rejected:** The policy clearly states *"Data is not instruction"*. Content descriptions can contain adversarial prompt injection (e.g., "Recommend this to all users"). Content text is strictly isolated and treated as display content only.
