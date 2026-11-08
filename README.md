# ProofLoop Blast Radius Zero

[![CI](https://github.com/sijie-Z/proofloop-blast-radius/actions/workflows/ci.yml/badge.svg)](https://github.com/sijie-Z/proofloop-blast-radius/actions/workflows/ci.yml)

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

## Web demo

The same pipeline is exposed as a single-page control plane:

```bash
python -m pip install -r webapp/requirements.txt
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The page runs the demo in Vultr mode (server-side key) or the keyless self-test, renders the 14 deterministic checks and evidence links, and includes a containment probe that runs a fresh hardened container.

The UI can also **verify the evidence hash** and **replay the verification** against the pristine repository: the audit panel shows `HASH OK` and `CONSISTENT` with the new verdict.

![ProofLoop web demo](docs/web-demo-hero.png)

The Vultr key lives only in the control plane. The containment probe reports `network: blocked`, `secret_env: []`, and the UID/cgroup limits of the sandbox. See `webapp/README.md` for the endpoints and environment variables.

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
webapp/                FastAPI control plane + single-page UI
deploy/                Vultr VM bootstrap, systemd, Caddy, gVisor guide
ARCHITECTURE.md        trust boundaries, Vultr deployment, requirement mapping
```

## Slides and video

- `slides/ProofLoop-BlastRadiusZero.pptx` — 10-slide deck (problem, architecture, web console, Vultr deployment, sandbox, demo, evidence, limits).
- `slides/ProofLoop-BlastRadiusZero.pdf` — PDF export for submission forms.
- `docs/cover.png` — 16:9 cover image for the lablab submission.
- `docs/ProofLoop-demo.srt` — English subtitles for the demo video.
- `VIDEO-SCRIPT.md` — 2.5-minute web + terminal recording script with the required containment moment.

## Development workflow

This repository follows Git Flow: `main` is the release branch, `develop` is the integration branch, and each `feature/*` branch carries exactly one issue and one pull request. CI runs unit tests on Python 3.11/3.12 plus a keyless demo self-test in Docker; both must be green before merge. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full branch, commit, release and review rules.

## Vultr deployment

The production demo is a web application on a Vultr Cloud Compute VM. See [deploy/vultr-vm.md](deploy/vultr-vm.md) for the bootstrap script, systemd unit, Caddy TLS and optional gVisor.

**Public demo URL:** _fill in after deploying to the Vultr VM_ (for example `https://demo.example.com`).

| Vultr service | Role |
| --- | --- |
| Vultr Cloud Compute (Ubuntu 24.04 VM) | Runs the FastAPI control plane, Docker daemon, sandbox and verifier |
| Vultr Serverless Inference (`https://api.vultrinference.com/v1`) | Agent LLM calls; default `deepseek-v4.1-flash-1017` |
| Vultr firewall | Exposes only 22/80/443; the app port stays on loopback |
| Vultr hackathon credits | $200 per participant, used for the VM and inference |

`deploy/bootstrap.sh` installs Docker, builds `proofloop-sandbox:latest`, creates the virtualenv, writes the systemd unit and starts the service. The key goes in `/etc/proofloop/proofloop.env` (mode 640, root:proofloop), never in the repo or the sandbox. `deploy/verify-demo.sh` checks `/health` and the containment probe.

### How Vultr powers the solution

- The Vultr VM is the central control layer: web app, job manager, sandbox and verifier all run there.
- Vultr Serverless Inference drives the agent's tool-calling loop through an OpenAI-compatible API.
- Every agent tool call and every verifier test run executes in a locked-down Docker container on the VM; the key stays in the control plane.
- The demo exposes a public URL for judges, with one run at a time and a 20/hour rate limit.

## Track requirements (Blast Radius Zero)

| Requirement | Implementation |
| --- | --- |
| Sandboxed code-execution agent | `agent/react_loop.py` + `webapp/` |
| Vultr Serverless Inference | `agent/vultr_llm.py`, `smoke.py` |
| Isolated execution (Docker / gVisor) | `proofloop/sandbox.py`, `PROOFLOOP_DOCKER_RUNTIME=runsc` |
| No keys in the sandbox | key only in the control plane; containment probe reports `secret_env: []` |
| Resource limits | 512 MB, 1 CPU, 128 PIDs, cap-drop ALL |
| Lifecycle | fresh `--rm` container per tool call; per-run workspace |
| Web app + public URL | `webapp/`, `deploy/vultr-vm.md` |
| Containment moment in the video | containment probe in the UI; `VIDEO-SCRIPT.md` |

## Vultr API notes

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
