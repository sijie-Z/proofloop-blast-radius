# Web control plane

A small FastAPI service that exposes the ProofLoop demo through a single-page UI.
It is the trusted control plane: the Vultr key stays in this process and is never
handed to a sandbox container.

## Run locally

```bash
python -m pip install -r webapp/requirements.txt
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Modes

- `vultr` — real Vultr Serverless Inference agent; requires `VULTR_INFERENCE_API_KEY`.
- `fixed` — bundled-fix self-test; no key and no network required (Docker is still required).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Single-page UI |
| GET | `/health` | Liveness and active run id |
| POST | `/api/runs` | Start a run (`{"mode": "vultr"\|"fixed"}`) |
| GET | `/api/runs` | Recent runs |
| GET | `/api/runs/{id}` | Run state, stages, verdicts, checks, agent events |
| GET | `/api/runs/{id}/report/{pass\|fail}` | Sanitized HTML evidence report |
| GET | `/api/runs/{id}/evidence/{pass\|fail}` | Sanitized evidence JSON |
| GET | `/api/containment` | Run the containment probe in a fresh container |

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VULTR_INFERENCE_API_KEY` | none | Server-side key for Vultr mode |
| `VULTR_MODEL` | `deepseek-v4.1-flash-1017` | Model id override |
| `PROOFLOOP_DEMO_MAX_RUNS_PER_HOUR` | `20` | Per-process rate limit |
| `PROOFLOOP_DEMO_TIMEOUT` | `300` | Per-suite test timeout (seconds) |
| `PROOFLOOP_DOCKER_RUNTIME` | Docker default | Optional runtime, e.g. `runsc` for gVisor |

## Security notes

- One demo run at a time; runs are rate-limited.
- Evidence served by the API is sanitized: project roots and local drive paths are replaced.
- The containment probe runs in the same hardened sandbox as the agent tools: no network, 512 MB, 1 CPU, all capabilities dropped, host UID on POSIX.
