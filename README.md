# ProofLoop Blast Radius Zero

**An independent acceptance and evidence layer for coding agents running untrusted code.**

Built for the Vultr Agent Rush Hackathon (Blast Radius Zero track). An LLM agent edits code inside a hardened Docker sandbox — but the agent never gets to declare success. A separate verifier freezes the acceptance criteria, runs visible and hidden tests plus adversarial checks, and emits a tamper-evident, replayable evidence sheet that a human can sign off on.

## Why this exists

A coding agent can make tests green without fixing the bug:

- deleting or weakening the failing tests;
- hardcoding the values the tests expect;
- planting `conftest.py` / `sitecustomize.py` / `.pth` files during a test run;
- rewriting the JUnit report from an `atexit` hook;
- changing files outside the intended scope.

ProofLoop treats every candidate patch as untrusted. The agent's self-report is not evidence; the verifier's frozen gates are.

## What the demo does

`python demo.py` runs two scenes end to end:

| Scene | Candidate | Expected verdict |
|---|---|---|
| 1 | Vultr Serverless Inference agent (ReAct loop: `read_file` / `write_file` / `run_tests`) | **PASS** |
| 2 | Hardcoded cheat patch | **FAIL** |

Every tool call in scene 1 executes in a fresh hardened container. The resulting patch is then verified independently under ProofLoop's own sandbox.

## Architecture

```text
Vultr Serverless Inference  (OpenAI-compatible API)
        │
        ▼
agent/react_loop.py  ── read_file / write_file / run_tests ──▶  Docker sandbox
        │                                                      --network none
        │                                                      512 MB / 1 CPU / pids 128
        │                                                      cap-drop ALL, no-new-privileges
        ▼
candidate patch
        │
        ▼
ProofLoop verifier  (deterministic, no LLM)
  baseline vs candidate · hidden tests · test integrity · scope
  hardcode · runtime tamper · runtime file injection
  mutation score · coverage
        │
        ▼
evidence.json + evidence.sha256 + report.html
        │
        ▼
human reviews evidence, not the model's claim
```

## Quickstart

Prerequisites: Python 3.11+, Docker, git, and the sandbox image.

```bash
docker build -t proofloop-sandbox:latest docker
cp .env.example .env          # then add VULTR_INFERENCE_API_KEY (never commit .env)
python smoke.py               # model catalog + one tool-calling round trip
python demo.py                # scene 1 (agent fix) + scene 2 (cheat patch)
```

No API key yet? Run the keyless stack self-test:

```bash
python demo.py --agent-mode fixed
```

Artifacts land in `runs/demo-<timestamp>/`:

- `evidence-pass/evidence.json`, `evidence.sha256`, `report.html`
- `evidence-fail/evidence.json`, `evidence.sha256`, `report.html`
- `scene1-agent.patch`, `agent_transcript.json`, `summary.json`

## The 14 deterministic checks

| Check | What it catches |
|---|---|
| `baseline_reproduced` | The bug must actually fail on the pristine base; otherwise the run is `INSUFFICIENT_EVIDENCE` |
| `candidate_tests_passed` | Candidate run must exit 0 with no failures and no new skips |
| `visible_tests_match_baseline` | The same visible tests must exist before and after |
| `hidden_tests_passed` | Held-out tests must pass |
| `hidden_tests_expected` | Exactly the hidden node IDs collected on the pristine base must run and pass |
| `candidate_tree_frozen` | The staged tree and tracked worktree cannot change while tests run |
| `test_integrity` | Test files and harness files are byte-identical between base and candidate |
| `scope` | All changes stay inside `allowed_paths` |
| `noop` | A test-only or empty patch is rejected |
| `hardcode_suspicion` | Added source lines that reuse several numeric literals from the tests |
| `runtime_tamper_suspicion` | Added source lines touching junit, coverage, `atexit`, `os._exit`, pytest hooks |
| `runtime_file_injection` | Files planted during a test run are quarantined and reported |
| `mutation_score` | AST mutants of the changed source must be killed by the test suite |
| `coverage` | Changed source must be exercised above the configured threshold |

## Sandbox hardening

- `--network none` (no exfiltration, no package installs)
- `--memory 512m --memory-swap 512m --cpus 1 --pids-limit 128`
- `--cap-drop ALL --security-opt no-new-privileges`
- pytest/coverage launched with `python -P -c` so a repo-root `pytest.py`, `coverage.py`, `sitecustomize.py`, or `.pth` cannot shadow the real tools
- HMAC-signed pytest result summary cross-checked against the JUnit report
- runtime-written executable/config files quarantined between test phases
- agent writes restricted to `allowed_paths`; test and harness files are denied

## Example result (keyless stack self-test)

Scene 1 with the bundled fix: **PASS**, 14/14 checks — hidden 3/3, mutation 8/8, coverage 100%.
Scene 2 with the hardcoded cheat: **FAIL** — failed `hidden_tests_passed`, `hidden_tests_expected`, and `hardcode_suspicion`.

The LLM scene produces the same verification path; only the candidate patch changes.

## Evidence screenshots

Scene 1 — PASS: 14/14 checks, hidden 3/3, mutation 8/8, coverage 100%.

![ProofLoop PASS evidence](docs/evidence-pass.png)

Scene 2 — FAIL: the hidden test failed and the hardcoded lookup table was flagged.

![ProofLoop FAIL evidence](docs/evidence-fail.png)

These screenshots come from the keyless stack self-test with the bundled fix and cheat patches.

## Repository layout

```text
agent/vultr_llm.py     OpenAI-compatible Vultr client (stdlib only)
agent/react_loop.py    ReAct loop; every tool call runs in Docker
demo.py                one-command demo: agent fix -> PASS, cheat -> FAIL
smoke.py               model catalog + tool-calling smoke test
proofloop/             frozen ProofLoop acceptance/evidence subset
examples/discount_demo deliberately buggy repo, fixes and cheat patches
examples/discount_hidden  held-out tests
docker/Dockerfile      sandbox image
```

## Slides and video

- `slides/ProofLoop-BlastRadiusZero.pptx` — 8-slide deck (problem, architecture, sandbox, demo, evidence, limits).
- `VIDEO-SCRIPT.md` — 2.5-minute terminal recording script with narration and exact commands.

## Vultr notes

- Base URL: `https://api.vultrinference.com/v1`
- `GET /v1/models` is the live catalog (the older `/v1/chat/models` is retired)
- Default model: `deepseek-v4.1-flash-1017`; override with `VULTR_MODEL`
- If `python smoke.py` reports `tool calling: NOT OBSERVED`, try another tool-capable model from `/v1/models` (for example `glm-5.3` or `qwen3.8-27b`)
- If the endpoint is not directly reachable from your network, set `VULTR_PROXY=http://host:port` in `.env`

## Honest limitations

- One synthetic demo task, not a benchmark; the numbers are not a general accuracy claim.
- Hidden tests are local to the demo; a production deployment would keep them in a separate trust domain.
- Several checks are heuristics (hardcode, runtime tamper); they raise the bar, they do not prove isolation.
- The acceptance criteria are enforced as aggregate gates, not mapped one-to-one to tests.

## License and attribution

Apache-2.0. `proofloop/` and `examples/` are a frozen subset of the ProofLoop project, adapted for this hackathon. See `LICENSE` and `NOTICE`.
