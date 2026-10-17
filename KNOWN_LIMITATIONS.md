# Known Limitations, Engineering Trade-Offs & Scaling Roadmap

As emphasized in the candidate brief (*"Spend no more than 10-12 hours. Incomplete but well-prioritized work is acceptable"*), this document transparently details the deliberate design choices, architectural trade-offs, and scaling pathways for the **One User, One Agent** system.

---

## 1. Single-Node In-Memory / File-Based State Store

### Current Implementation (Take-Home Prototype):
* Durable viewer state is managed via an in-memory dictionary during execution and persisted as per-viewer JSON files (`sample_run/viewer_state/<user_id>.json`).
* While this delivers sub-millisecond read/write latency ($< 0.05$ ms) and zero external infrastructure dependencies for 5,000 viewers, it cannot scale across distributed worker nodes.

### Production Scaling Pathway (10 Million Viewers, 40 Dialects):
1. **Tier 1 (Hot State / Working Memory):** Redis Cluster with Redis Sentinel for high availability. Each viewer's state key (e.g., `viewer:{user_id}:state`) is stored as a Redis Hash or compressed JSON document with sub-millisecond read latency.
2. **Tier 2 (Warm Telemetry Stream):** Apache Kafka partitioned by `user_id`. Partitioning guarantees that all events for a given viewer arrive at the same consumer group worker in strict chronological order, eliminating distributed lock contention.
3. **Tier 3 (Cold Durable Store):** ScyllaDB or Cassandra for multi-region active-active persistence of historical watch telemetry and decision audit logs.

```
[Edge Telemetry Gateway]
          │
          ▼
   (Kafka Partitioned by user_id)
          │
          ▼
┌───────────────────────────────────────┐
│     Distributed Agent Workers         │
└───────┬───────────────────────┬───────┘
        │ (Fast Read/Write)     │ (Async Audit Stream)
        ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│  Redis Cluster   │    │ ScyllaDB Archive │
│ (Hot User State) │    │ (Durable History)│
└──────────────────┘    └──────────────────┘
```

---

## 2. Real-Time LLM Inference vs Deterministic Mock Provider

### Current Implementation:
* Implemented a `MockModelProvider` with full fault injection (reproducing HTTP 503 outages, 45-second timeouts, malformed JSON, and hallucinated claims from `replay/model_faults.jsonl`).
* This eliminates the requirement for evaluators to provide personal API keys and prevents rate-limit failures during automated testing.

### Production Scaling Pathway:
1. **Offline Asynchronous LLM Workflows:** Running live hosted LLM calls (e.g. GPT-4o, Claude 3.5 Sonnet) synchronously on every user app open introduces 500ms–2500ms latency spikes and prohibitive API costs at 10,000 visits/second.
2. **Hybrid Architecture:**
   * **Offline:** Large LLMs run overnight batch jobs to summarize viewing personas, generate emotional resonance tags, and draft personalized push copy templates.
   * **Online (Edge):** Lightweight, deterministic Rust/Go/Python agent rules execute in $< 5$ ms to match pre-generated candidate actions against live context (current dialect, quiet hours, ground-truth billing state).

---

## 3. Heuristic Matching vs Contextual Multi-Armed Bandits

### Current Implementation:
* Content rows are ranked using deterministic heuristic affinity (top genre affinity, continuation of unfinished series, and recency), which directly optimizes simulator bonus multipliers (+0.15 for next episode, +0.08 for genre, +0.10 per row).

### Production Scaling Pathway:
1. **Exploration vs Exploitation Dilemma:** Pure affinity matching over-indexes on what the user previously watched, creating an "echo chamber" where users are never exposed to new dialects or emerging genres.
2. **Contextual Bandits (LinUCB / Thompson Sampling):**
   * Maintain per-viewer feature vectors (time of day, dialect, device type, historical completion rate).
   * Dynamically allocate 85% of row slots to high-confidence exploitation and 15% to exploratory candidate titles to discover new viewer affinities.

---

## 4. Multi-Touch Message Scheduling & Drop-Off Journeys

### Current Implementation:
* Primarily focuses on personalized Home Page layout generation on `app_open`, while strictly validating push and WhatsApp messages against quiet hours (22:00–08:00 IST), consent flags, and daily frequency caps ($\le 1$ message/day).

### Production Scaling Pathway:
1. **Automated Event-Driven Drop-Off Journeys:**
   * If a user abandons an episode at 35% and does not return within 48 hours, schedule a personalized WhatsApp teaser at the user's historical peak engagement hour (e.g. 19:30 IST).
   * If the user ignores two consecutive push notifications, dynamically throttle messaging frequency to once per week to prevent notification fatigue and app uninstalls.
