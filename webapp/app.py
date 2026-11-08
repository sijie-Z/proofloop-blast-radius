"""FastAPI control plane for the ProofLoop Blast Radius Zero demo."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs
from .sanitize import sanitize_obj, sanitize_text

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent

app = FastAPI(
    title="ProofLoop Blast Radius Zero",
    description="Verifiable acceptance for coding agents running untrusted code.",
    version="0.1.4",
)
manager = jobs.RunManager(ROOT)
app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")


class RunRequest(BaseModel):
    mode: str = "vultr"


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "proofloop-blast-radius",
        "active_run": manager.active_id,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    )


@app.post("/api/runs", status_code=202)
def create_run(request: RunRequest):
    try:
        run = manager.start(request.mode)
    except jobs.RunBusy as exc:
        raise HTTPException(409, str(exc))
    except jobs.MissingKey as exc:
        raise HTTPException(400, str(exc))
    except jobs.RateLimited as exc:
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": run.id, "mode": run.mode, "status": run.status}


@app.get("/api/runs")
def list_runs():
    return {"runs": manager.list_runs()}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    data = manager.get(run_id)
    if not data:
        raise HTTPException(404, "run not found")
    for key, scene in (("pass_scene", "pass"), ("fail_scene", "fail")):
        data[key]["report_url"] = f"/api/runs/{run_id}/report/{scene}"
        data[key]["evidence_url"] = f"/api/runs/{run_id}/evidence/{scene}"
    replay = data.get("replay") or {}
    if replay.get("output"):
        replay["output"] = sanitize_text(replay["output"], manager.root)
    return data


@app.get("/api/runs/{run_id}/report/{scene}", response_class=HTMLResponse)
def get_report(run_id: str, scene: str):
    path = manager.report_path(run_id, scene)
    if not path:
        raise HTTPException(404, "report not found")
    return HTMLResponse(sanitize_text(path.read_text(encoding="utf-8"), manager.root))


@app.get("/api/runs/{run_id}/evidence/{scene}")
def get_evidence(run_id: str, scene: str):
    path = manager.evidence_path(run_id, scene)
    if not path:
        raise HTTPException(404, "evidence not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(sanitize_obj(data, manager.root))


@app.post("/api/runs/{run_id}/verify-evidence")
def verify_evidence(run_id: str):
    try:
        return manager.verify_evidence(run_id)
    except KeyError:
        raise HTTPException(404, "run not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(
            500, f"verify failed: {type(exc).__name__}: {exc}"
        )


@app.post("/api/runs/{run_id}/replay", status_code=202)
def replay(run_id: str):
    try:
        return manager.start_replay(run_id)
    except KeyError:
        raise HTTPException(404, "run not found")
    except jobs.RunBusy as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(
            500, f"replay failed: {type(exc).__name__}: {exc}"
        )


@app.get("/api/containment")
def containment():
    try:
        return manager.probe()
    except Exception as exc:  # report probe failures without killing the app
        raise HTTPException(
            503, f"containment probe failed: {type(exc).__name__}: {exc}"
        )
