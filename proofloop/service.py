from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def _allowed_roots():
    raw = os.environ.get("PROOFLOOP_ALLOWED_ROOTS", "").strip()
    if raw:
        roots = [Path(item).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip()]
    else:
        roots = [(ROOT / "examples").resolve(), (ROOT / "agent_runs").resolve()]
    return roots


def _assert_allowed(path: Path, label: str) -> Path:
    path = Path(path).expanduser().resolve()
    roots = _allowed_roots()
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError(f"{label} must be under one of: {', '.join(str(r) for r in roots)}")
    return path


def _assert_trusted_task(task_path: Path) -> Path:
    task_path = _assert_allowed(task_path, "task_path")
    trusted = (ROOT / "examples").resolve()
    if not task_path.is_relative_to(trusted):
        raise ValueError("task_path must be a trusted task under ProofLoop/examples")
    return task_path


def _safe_out_dir(out_dir: str) -> Path:
    """Validate a new, not-yet-existing evidence directory under runs/.

    Existing directories are refused so evidence is never overwritten.  The
    directory itself is created exclusively by the CLI (exist_ok=False), so
    two concurrent verifications cannot silently share one out_dir.
    """
    RUNS.mkdir(parents=True, exist_ok=True)
    runs_root = RUNS.resolve()
    if out_dir:
        out = Path(out_dir).expanduser().resolve()
        if not out.is_relative_to(runs_root) or out == runs_root:
            raise ValueError(
                f"out_dir must be a new directory under {RUNS} (not the runs root)"
            )
    else:
        out = RUNS / (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        )
    if out.exists():
        raise ValueError(
            f"out_dir already exists; refusing to overwrite existing evidence: {out}"
        )
    return out


def verify_repo_impl(repo_path: str, task_path: str, patch_path: str, out_dir: str = "") -> dict:
    """Run ProofLoop verification and return the evidence summary.

    This module deliberately does not import openjiuwen: the same function is used
    by the MCP server, REST API and SDK adapter, and MCP stdio stdout must stay clean.
    """
    repo = _assert_allowed(Path(repo_path), "repo_path")
    task = _assert_trusted_task(Path(task_path))
    patch = _assert_allowed(Path(patch_path), "patch_path")
    # The programmatic API must only verify the repository that owns the task.
    # Otherwise a caller could point repo_path at a hidden-tests directory
    # (both live under examples/) and read the hidden assertions from the
    # pytest output.  Direct `proofloop verify` CLI use is trusted-local only.
    if repo != task.parent:
        raise ValueError(
            "repo_path must be the repository that contains the task file "
            f"(repo_path={repo}, task parent={task.parent}); "
            "this API does not accept arbitrary repositories under examples/"
        )

    task_data = json.loads(task.read_text(encoding="utf-8"))
    hidden = task_data.get("hidden_tests_dir", "")
    if hidden:
        hidden_path = (repo / hidden).resolve()
        _assert_allowed(hidden_path, "hidden_tests_dir")

    out = _safe_out_dir(out_dir)
    cmd = [
        sys.executable,
        "-m",
        "proofloop",
        "verify",
        "--repo",
        str(repo),
        "--task",
        str(task),
        "--patch",
        str(patch),
        "--out",
        str(out),
        "--sandbox",
        "docker",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        stdin=subprocess.DEVNULL,
    )
    evidence_path = ""
    for line in p.stdout.splitlines():
        if line.startswith("evidence:"):
            evidence_path = line.split(":", 1)[1].strip()
    data = {}
    if evidence_path and Path(evidence_path).is_file():
        data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    if not data:
        raise RuntimeError(
            "verification failed to produce evidence "
            f"(exit={p.returncode}): {(p.stderr or p.stdout)[-800:]}"
        )
    return {
        "verdict": data.get("verdict", "UNKNOWN"),
        "evidence_path": evidence_path,
        "exit_code": p.returncode,
        "summary": p.stdout[-3000:] + ("\nSTDERR:\n" + p.stderr[-1000:] if p.stderr else ""),
    }
