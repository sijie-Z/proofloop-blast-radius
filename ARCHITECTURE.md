# Architecture

ProofLoop Blast Radius Zero is a control plane plus an independent acceptance layer for coding agents that run untrusted code. The agent is never trusted to declare success; a deterministic verifier decides, and every verdict carries replayable evidence.

## One picture

```text
Browser (untrusted input)
   │  HTTPS
   ▼
FastAPI control plane  (trusted; holds the Vultr key; never runs candidate code)
   │  docker run: no network, 512 MB, 1 CPU, cap-drop ALL, host UID
   ▼
Docker sandbox  (untrusted candidate code; no key, no network)
   │  tool output / candidate patch
   ▼
ProofLoop verifier  (trusted; deterministic checks; its own sandbox)
   │
   ▼
evidence.json + evidence.sha256 + report.html
   │
   ▼
Human reviews the evidence, not the model's claim
```

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| Web control plane | `webapp/app.py`, `webapp/jobs.py` | Runs one demo job at a time, holds the Vultr key, streams stages to the UI, sanitizes evidence |
| Vultr client | `agent/vultr_llm.py` | OpenAI-compatible chat + tool calling against `https://api.vultrinference.com/v1` |
| ReAct agent | `agent/react_loop.py` | Model → `read_file` / `write_file` / `run_tests`; every tool call is a hardened container |
| Sandbox | `proofloop/sandbox.py` | Docker execution, resource limits, shadow-proof pytest/coverage launchers, HMAC attestation, host UID on POSIX |
| Verifier | `proofloop/cli.py`, `rules.py`, `runner.py`, `snapshot.py`, `mutation.py` | Frozen acceptance, 14 deterministic checks, mutation, coverage, evidence |
| Evidence | `proofloop/evidence.py` | `evidence.json`, `evidence.sha256`, `report.html`; `verify-evidence` and `replay` |
| Demo tasks | `examples/discount_demo`, `examples/discount_hidden` | Deliberately buggy repo, fixed/cheat patches, held-out tests |
| Deployment | `deploy/` | Vultr VM bootstrap, systemd, Caddy, gVisor option |

## Trust boundaries

1. **Browser → control plane.** Browser input is untrusted. The control plane exposes only a fixed demo task; there is no path input and no upload endpoint.
2. **Control plane → sandbox.** The control plane is trusted and holds the Vultr key. The key is never passed as a container environment variable, never mounted, and never written into a workspace. A containment probe reads the container environment and reports `secret_env: []`.
3. **Sandbox → verifier.** The sandbox returns a candidate patch. The verifier does not trust the worktree the agent touched: it copies a pristine base, applies the patch, freezes the staged view, and runs baseline/candidate/hidden/mutation/coverage itself.
4. **Verifier → human.** The verdict is a PASS / FAIL / INSUFFICIENT_EVIDENCE over deterministic gates. The evidence sheet is hash-addressed and replayable; a human signs off on the evidence.

## Containment properties

| Property | Implementation |
| --- | --- |
| Process isolation | All agent tool calls and verifier test runs execute in Docker containers, never on the host |
| Secret hygiene | The Vultr key lives only in the control plane; the sandbox receives no key. The probe checks for secret-looking env vars |
| Resource limits | `--network none`, 512 MB, 1 CPU, 128 PIDs, `cap-drop ALL`, `no-new-privileges` |
| Lifecycle discipline | Every tool call is a fresh `--rm` container; each demo run gets its own workspace under `webapp/runs/<id>` |
| Test integrity | Base and candidate test blobs are compared; test and harness edits are rejected |
| Runtime tampering | Added source lines touching JUnit/coverage/atexit/pytest hooks are flagged; runtime-written executable files are quarantined |
| Non-root execution | On POSIX the container runs as the host UID/GID so it can write only where the workspace owner can |
| Optional stronger isolation | `PROOFLOOP_DOCKER_RUNTIME=runsc` runs the sandbox under gVisor |

## Vultr deployment

```text
Vultr Cloud Compute VM (Ubuntu 24.04, 2 vCPU / 4 GB / 80 GB)
├─ systemd: uvicorn webapp.app:app on 127.0.0.1:8000
├─ Caddy: public TLS reverse proxy on 80/443
├─ Docker daemon + proofloop-sandbox:latest
├─ /etc/proofloop/proofloop.env  (key, mode 640 root:proofloop)
└─ /opt/proofloop-blast-radius   (repo, venv, per-run workspaces)
        │ HTTPS (OpenAI-compatible)
        ▼
Vultr Serverless Inference  https://api.vultrinference.com/v1
```

Vultr is the central control layer: the VM runs the web application, the job manager, the Docker sandbox and the verifier. Vultr Serverless Inference supplies the agent's LLM calls. The Vultr firewall exposes only SSH, HTTP and HTTPS; the app port stays on loopback. See `deploy/vultr-vm.md`.

## Data flow

1. The browser starts a run (`vultr` or keyless `fixed`).
2. The control plane copies the demo repo and its held-out tests into a per-run workspace.
3. A containment probe runs in a fresh container and records network, secret env, UID and cgroup limits.
4. In Vultr mode the ReAct agent edits the repo through sandboxed tool calls; in fixed mode the bundled patch is used.
5. The candidate patch is verified against a pristine copy by ProofLoop.
6. A hardcoded cheat patch is verified as scene 2.
7. The UI shows PASS/FAIL, all 14 checks and links to the sanitized evidence report.
8. A human can replay the evidence with `python -m proofloop verify-evidence` and `python -m proofloop replay`.

## Evidence

`evidence.json` records:

- verdict and task metadata (acceptance criteria, allowed paths, thresholds)
- base and candidate git trees, patch/diff/acceptance SHA-256
- the exact commands executed
- 14 checks with pass/fail and detail
- baseline/candidate/hidden test stats, mutation score, coverage, environment
- runtime file reports (quarantine)
- `evidence_sha256`, with a sidecar `evidence.sha256` and an HTML report

`verify-evidence` recomputes the canonical hash; `replay` re-runs the verification and compares verdicts. Evidence is never overwritten.

## Requirement mapping (lablab, Challenge 1 — Blast Radius Zero)

| Requirement | Where it is implemented |
| --- | --- |
| Sandboxed code-execution agent | `agent/react_loop.py`, `webapp/` |
| Vultr Serverless Inference for agent LLM calls | `agent/vultr_llm.py`, `smoke.py` |
| Isolated execution (Docker; gVisor optional) | `proofloop/sandbox.py`, `docker/Dockerfile`, `PROOFLOOP_DOCKER_RUNTIME` |
| No API keys inside the sandbox | key only in the control plane; `/api/containment` reports `secret_env: []` |
| Time and memory caps | `--memory 512m`, `--cpus 1`, `--pids-limit 128`, test timeouts |
| Reset/destroy environment after each task | fresh `--rm` container per tool call; per-run workspace |
| Web application + public demo URL | `webapp/`, `deploy/vultr-vm.md` |
| Vultr VM backend | `deploy/bootstrap.sh`, `deploy/proofloop-web.service` |
| One containment moment in the video | containment probe in the UI; `VIDEO-SCRIPT.md` |
| README explains Vultr usage | README "Vultr deployment" and this document |

## Honest limitations

- One synthetic demo task; the numbers are not a benchmark or a general accuracy claim.
- Hardcode and runtime-tamper checks are heuristics, not proofs of isolation.
- The public demo has no user accounts; it is rate-limited to one run at a time and 20 runs/hour by default.
- Hidden tests are local to the demo; production deployments should keep them in a separate trust domain.
- Acceptance criteria are enforced as aggregate gates, not mapped one-to-one to tests.
