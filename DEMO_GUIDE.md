# 🎙️ RecoverIT Live Demonstration Guide & Playbook

This document is your step-by-step presentation script and cheat-sheet for demonstrating **RecoverIT** to stakeholders, team members, or evaluators.

---

## 1. The 60-Second Elevator Pitch

> *"When a production service crashes or a deployment fails at 2 AM, on-call engineers waste 30 to 60 minutes manually hopping between Datadog metrics, CloudWatch logs, GitHub PR diffs, and CI/CD pipelines to figure out what went wrong.*
> 
> ***RecoverIT is an Autonomous CI/CD Incident Triager & Self-Healer**. It acts as an AI-powered SRE investigator. When an alert fires, it automatically discovers what operational data is missing, queries 6 observability sources, reconstructs a chronological event timeline, evaluates commit diffs, tests multiple causal hypotheses, and produces a ranked root-cause diagnosis with exact evidence citations in seconds.*
> 
> *Best of all: it is **not an in-code library** that bloats your microservices. It is a standalone, read-only DevOps agent."*

---

## 2. Live Demo Option A: The Interactive Web Cockpit (Recommended)

### Step 1: Launch the Dashboard
Open your terminal in the repository directory and run:
```bash
python -m recoverit.serve
```
* The server starts at `http://127.0.0.1:8000` and will **automatically open your browser**.
* Point out to the audience:
  * **Top Status Badge**: `AGENT ACTIVE • MULTI-SOURCE MONITORING`
  * **AI Mode Selector**: Can seamlessly switch between **Zero-Token Offline Mode** (safe for demos, zero latency, zero cost) and **Live LLM Mode** (Gemini / OpenAI).

---

### Step 2: Demonstrate Scenario 1 — Configuration Regression
1. In the **Incident Benchmark Preset** dropdown, select:  
   `Bad Database Configuration Introduced by Deployment (payment-api)`
2. Point out the incident summary:
   * *“Payment API error rate spiked to 35% shortly after deployment v2.4.1 rolled out.”*
3. Click the cyan **"Launch Autonomous Investigation"** button.
4. **Explain the Live Multi-Round Pipeline** while the animated stepper runs:
   * *Step 1: Alert Ingested* — Sanitizes and validates the incoming alert seed.
   * *Step 2: Gap Assessment* — Determines what information is missing.
   * *Step 3: 6 Sources Queried* — Discovers capabilities and gathers data across Logs, Metrics, Git changes, Deployments, Pipelines, and Config.
   * *Step 4: Timeline Built* — Person 2 normalizes timestamps, strips PII/secrets, and builds a strict chronological event sequence.
   * *Step 5: Hypotheses Tested* — Evaluates multiple potential causes (change-related and non-change related) against real context evidence.
   * *Step 6: Root Causes Ranked* — Deterministically scores evidence support vs contradiction.

5. **Walk Through the Results**:
   * **Diagnosis Panel (Left)**:
     * **Rank #1 (Medium/High Confidence)**: *"The recent deployment introduced an invalid database connection configuration."*
     * **Evidence Citations**: Show the corroborating citation pointing to the exact error log timestamp.
     * **Rank #2**: Notice it also tested an alternative hypothesis (*"External payment gateway outage"*) and ranked it lower because evidence didn't support it.
   * **Chronology & Git Diff (Right)**:
     * Show the **Git Diff box** highlighting the actual commit change:  
       `- host: db-cluster.internal`  
       `+ host: db-primary-misconfig`
     * Show how the timeline links the commit at 10:15 $\rightarrow$ deployment at 10:25 $\rightarrow$ database timeout at 10:27 $\rightarrow$ HTTP 500 spike.
6. Click **"Download Post-Mortem Report (.md)"** to show the generated Markdown report ready for Jira/Slack!

---

### Step 3: The "Killer Feature" — Coincidental Deployment (Avoiding Bias)
1. Select `Coincidental Deployment During External Gateway Outage (checkout-api)` in the dropdown.
2. Click **"Launch Autonomous Investigation"**.
3. **What to tell the audience**:
   > *"Human engineers (and naive LLM prompts) have a cognitive bias: when an incident happens right after a deployment, they almost always blame the deployment.  
   > Watch what RecoverIT does here: deployment v1.9.0 succeeded at 10:25, and errors spiked at 10:27. But RecoverIT checks the commit diffs and gateway logs, detects that the code changes were completely unrelated to payment processing, and discovers an active third-party gateway downtime.  
   > It correctly identifies **External Dependency Failure** as the root cause, preventing a costly and useless rollback!"*

---

## 3. Live Demo Option B: Scanning a Real Local Codebase & Logs (CLI)

Show that RecoverIT can point to an **actual directory on disk** with real git commits and log files!

### Command 1: List Built-in Benchmark Scenarios
```bash
python -m recoverit.cli list
```
Displays the clean formatted table of available incident presets.

---

### Command 2: Run Scenario Triage via CLI
```bash
python -m recoverit.cli run --scenario bad_db_config
```
* Shows the live CLI spinner.
* Outputs the overview panel, reconstructed incident chronology table, and ranked hypotheses cards.

---

### Command 3: Scan a Real Local Folder & Log File
Point RecoverIT at the included sample microservice:
```bash
python -m recoverit.cli scan --repo ./examples/demo_service --logs ./examples/demo_service/logs/payment-api.log --service payment-api
```
* **What this proves to evaluators**:
  1. RecoverIT runs actual `git log` and `git diff` against a real local `.git` folder.
  2. It parses an actual log file on disk (`payment-api.log`).
  3. It performs the complete autonomous investigation and outputs the diagnosis.

---

## 4. Key Questions & Answers Cheat-Sheet

| Question | Your Answer |
|---|---|
| **"Do I have to import RecoverIT into my application code?"** | *"No. RecoverIT is an external observer agent, like Datadog or Sentry. Your services remain completely untouched."* |
| **"Can it only detect these 5 errors?"** | *"No. Those 5 scenarios are reproducible benchmark presets for reliable offline demos. When connected to live LLMs (Gemini/OpenAI/Ollama) and your real repositories, it is a general-purpose investigator capable of diagnosing any code defect, infrastructure outage, memory leak, or misconfiguration."* |
| **"Can it perform automated self-healing actions?"** | *"Per architectural safety principles, RecoverIT strictly enforces a boundary: it produces a RankedHypothesisSet with evidence citations. It provides recommended actions to engineers without making reckless unverified changes to production."* |
| **"Does it require an internet connection for this demo?"** | *"No! It has a built-in deterministic mode that runs 100% locally with zero latency, zero token costs, and zero rate limits."* |
