# Impact Evaluation & Statistical Analysis Report

**Evaluation Window:** 14 Days Post-Replay  
**Methodology:** Randomized Intent-to-Treat (ITT) A/B Comparison  
**Policy Problems:** 0 (Zero policy violations target)

---

## 1. Executive Summary Table (Intent-to-Treat)

| Metric | Agent Treatment Arm | Holdout Control Arm | Absolute Difference | Relative Uplift |
| :--- | :--- | :--- | :--- | :--- |
| **Triggered Viewers** | 855 | 873 | - | - |
| **Viewers Who Played** | 666 (77.89%) | 669 (76.63%) | **+1.26 pp** | **1.02x** |
| **Total Plays Started** | 2,657 | 2,579 | +78 | 1.03x |
| **Total Episode Completes**| 1,291 | 1,322 | +-31 | 0.98x |
| **Paid Conversions** | 36 (4.21%) | 36 (4.12%) | **+0.09 pp** | 1.02x |
| **Total Revenue (INR)** | ₹5,364 | ₹5,364 | **+₹0** | - |

---

## 2. Why the Pilot Report Was Flawed (Selection Bias)

In `pilot/pilot_summary_report.md`, the internal analytics team reported:
> *"Users served by the agent reached their goal 40.1% of the time, against 17.7% for all other users: a 2.3x uplift."*

### The Fundamental Flaw:
The pilot compared **Served Viewers** against **All Other Users (Unserved + Holdout)**. This suffers from severe **selection bias / immortal time bias**:
1. Viewers who opened the app multiple times or stayed engaged were far more likely to receive an intervention.
2. Comparing active, served viewers against passive, dormant viewers artificially inflates the perceived uplift because the served group already had higher baseline intent.

### Our Solution (Intent-to-Treat / ITT):
Our evaluation compares **every user randomized to the Agent arm** against **every user randomized to the Holdout arm**, regardless of whether an intervention was delivered or if the agent chose to abstain (`NO_ACTION`). This provides an unbiased, mathematically trustworthy causal measurement of the agent's true business value.

---

## 3. Policy & Safety Compliance

* **Policy Violations Count:** 0
* **Holdout Incursions:** 0 (Independent verifier strictly prevented any actions on holdout users).
* **Quiet Hours Violations:** 0 (All notifications held or suppressed between 22:00 and 08:00 IST).
* **Frequency Cap Breaches:** 0 (Strict <= 1 message per user per calendar day).
* **Dialect Alignment:** 100% of published home page rows strictly matched viewer active app dialect.
