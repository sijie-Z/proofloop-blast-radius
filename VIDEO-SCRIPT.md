# 2.5-minute demo video — recording script

This script records a real run: Vultr Serverless Inference drives the agent, and ProofLoop verifies the result. Record after `python smoke.py` reports `tool calling: OK`.

## Setup

- Terminal in the repository root, font size large enough for 1600×900.
- `.env` contains `VULTR_INFERENCE_API_KEY` (never show `.env` on screen).
- A browser tab ready to open `runs/demo-<timestamp>/evidence-pass/report.html`.
- If `api.vultrinference.com` is not directly reachable, `.env` also contains the proxy line and `python smoke.py` already passes.

## Timeline and narration

### 0:00–0:20 — What this is

On screen: title slide, or the terminal with the README open.

> "Coding agents can pass tests without fixing the bug: edit the tests, hardcode the expected values, or forge the report. ProofLoop is an independent acceptance layer. The agent edits code inside a Docker sandbox; a separate verifier decides whether the result is real."

中文对照：写代码的 Agent 可以通过改测试、硬编码、伪造报告来让测试变绿。ProofLoop 是独立验收层：Agent 在 Docker 沙箱里改代码，由另一个验证器决定结果是否可信。

### 0:20–0:45 — Show the task

On screen:

```bash
cat examples/discount_demo/app/discount.py
cat examples/discount_demo/tests/test_discount.py
```

> "One small Python bug: discounts below zero or above one hundred percent must raise. The visible tests and the held-out tests both enforce it."

### 0:45–1:15 — Smoke the Vultr path

On screen:

```bash
python smoke.py
```

> "This is Vultr Serverless Inference speaking the OpenAI tool-calling protocol. The smoke test lists the live model catalog and forces one tool call."

### 1:15–1:50 — Run the demo

On screen:

```bash
python demo.py
```

> "The ReAct loop reads the source, writes a fix, and runs pytest — every tool call executes in a fresh container with no network, 512 megabytes of RAM, one CPU, and all capabilities dropped. Then ProofLoop re-runs the patch under its own sandbox."

If the model takes longer than the recording budget, cut from the tool-call loop to the verdict lines; do not fabricate output.

### 1:50–2:15 — Show the PASS evidence

On screen: open `runs/demo-<timestamp>/evidence-pass/report.html`.

> "Fourteen deterministic checks. The baseline bug reproduced, the candidate passed, the held-out tests passed, mutation score eight of eight, coverage one hundred percent. The evidence sheet is hash-addressed and replayable."

Also run:

```bash
python -m proofloop verify-evidence --evidence runs/demo-<timestamp>/evidence-pass/evidence.json
```

> "The hash matches. A human can replay the run instead of trusting the model."

### 2:15–2:35 — Show the FAIL evidence

On screen: open `runs/demo-<timestamp>/evidence-fail/report.html`.

> "Same verifier, cheat patch: the hidden test fails, and the hardcoded lookup table is flagged. The transcript names exactly which checks failed."

### 2:35–2:45 — Close

On screen: the slides' closing slide.

> "Small, real, reproducible. Trust the evidence, not the agent."

## Honesty checklist

- Do not cut in a PASS that did not happen. If the network or model fails, show the blocker on screen and stop.
- Keep the model name and the sandbox flags visible somewhere in the recording.
- Do not show `.env`, API keys, or local absolute paths.
- The stack self-test (`python demo.py --agent-mode fixed`) is not the LLM demo; do not present it as one.
