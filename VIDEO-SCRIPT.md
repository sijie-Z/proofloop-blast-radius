# 2.5-minute demo video — recording script

Record a **real** run: Vultr Serverless Inference drives the agent, and ProofLoop verifies the result. The video must include at least one containment moment (Challenge 1 requirement).

## Setup

- Public demo: `https://<your-vultr-domain>/` (deployed with `deploy/vultr-vm.md`).
- Terminal on the VM or locally, repository root.
- `.env` / `/etc/proofloop/proofloop.env` contains the Vultr key; never show it on screen.
- Browser tab on the demo; terminal tab ready for `verify-evidence`.
- If the endpoint is not directly reachable, set `VULTR_PROXY` in `.env` (local) or the systemd environment (VM).

## Timeline and narration

### 0:00–0:20 — What this is

On screen: title slide, then the demo page.

> "A coding agent can pass tests without fixing the bug: edit the tests, hardcode the expected values, or forge the report. ProofLoop is an independent acceptance layer. The agent edits code inside a Docker sandbox on Vultr; a separate verifier decides whether the result is real."

### 0:20–0:45 — The web console

On screen: the demo page at the public URL; point at the mode selector and the containment panel.

> "This is the control plane on a Vultr Cloud Compute VM. It can run a real Vultr Serverless Inference agent or a keyless self-test. The Vultr key stays in the control plane; it never enters the sandbox."

### 0:45–1:15 — Containment moment (required)

On screen: click **Probe the sandbox**.

> "The containment probe starts a fresh container. It tries the network — blocked. It looks for secret-looking environment variables — none visible. It reports the limits: 512 megabytes, one CPU, all capabilities dropped, and on Linux the host UID."

Let the badges render. This is the containment moment; keep it on screen for several seconds.

### 1:15–1:55 — Real Vultr agent

On screen: select **Vultr agent (real LLM)**, click **Run demo**. Point at the live stages.

> "The ReAct loop reads the source, writes a fix, and runs pytest. Every tool call is a fresh container with no network and no key. The candidate patch then goes to ProofLoop, which re-runs it under its own sandbox."

If the model takes longer than the recording budget, cut between the tool-call stage and the verdict; never fabricate a PASS.

### 1:55–2:20 — PASS evidence

On screen: scene 1 is **PASS**. Open **Open evidence report**.

> "Fourteen deterministic checks: baseline reproduced, candidate tests, hidden tests, test integrity, scope, hardcoded literals, runtime tampering, mutation eight of eight, coverage one hundred percent."

Open `evidence.json`, then in the terminal:

```bash
python -m proofloop verify-evidence --evidence webapp/runs/<run-id>/evidence-pass/evidence.json
```

> "The evidence sheet is hash-addressed. A human can replay the run instead of trusting the model."

### 2:20–2:40 — FAIL evidence

On screen: scene 2 is **FAIL**; open the report.

> "Same verifier, cheat patch: the hidden test fails and the hardcoded lookup table is flagged. The report names exactly which checks failed."

### 2:40–2:50 — Close

On screen: closing slide.

> "Small, real, reproducible. Trust the evidence, not the agent."

## Honesty checklist

- Do not cut in a PASS that did not happen. If the network or model fails, show the blocker on screen and stop.
- Keep the model name and the sandbox flags visible somewhere in the recording.
- Do not show `.env`, API keys, or local absolute paths.
- The keyless self-test is not the LLM demo; do not present it as one.
- The containment probe must be a real probe against the deployed sandbox.
