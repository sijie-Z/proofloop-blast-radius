#!/usr/bin/env python3
"""One-command demo: agent fixes the bug, ProofLoop issues PASS/FAIL evidence.

Scene 1: the Vultr-driven agent edits the repo inside the Docker sandbox;
         the resulting patch is verified by ProofLoop (expected PASS).
Scene 2: a hardcoded cheat patch is verified (expected FAIL).

Use --agent-mode fixed to exercise the pipeline without an API key; that mode
uses the bundled fixed.patch and is a stack self-test, not the LLM demo.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
DEMO_REPO = EXAMPLES / "discount_demo"
HIDDEN_TESTS = EXAMPLES / "discount_hidden"
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")

sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from proofloop import snapshot  # noqa: E402


def _run(cmd, cwd, env=None, timeout=900):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def copy_demo_repo(dest: Path) -> Path:
    """Copy only what the agent needs; answer patches stay out of reach."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("app", "tests", "task.json"):
        source = DEMO_REPO / name
        target = dest / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=IGNORE)
        elif source.is_file():
            shutil.copy2(source, target)
    (dest / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n*.pyc\n*.pyo\n*.xml\n*.attest.json\ncoverage.json\n",
        encoding="utf-8",
        newline="\n",
    )
    return dest


def copy_hidden(dest: Path) -> Path:
    shutil.copytree(HIDDEN_TESTS, dest, ignore=IGNORE)
    return dest


def make_patch(repo: Path, base_commit: str, patch_path: Path) -> bytes:
    _run(["git", "add", "-A"], repo)
    result = subprocess.run(
        ["git", "diff", "--cached", "--binary", base_commit],
        cwd=str(repo),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    data = result.stdout.replace(b"\r\n", b"\n")
    patch_path.write_bytes(data)
    return data


def run_verify(repo: Path, task: Path, patch: Path, out_dir: Path, timeout: int):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    allowed = [str(repo.parent), str(EXAMPLES), str(ROOT)]
    if env.get("PROOFLOOP_ALLOWED_ROOTS"):
        allowed.append(env["PROOFLOOP_ALLOWED_ROOTS"])
    env["PROOFLOOP_ALLOWED_ROOTS"] = os.pathsep.join(allowed)
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
        str(out_dir),
        "--sandbox",
        "docker",
        "--timeout",
        str(timeout),
    ]
    result = _run(cmd, ROOT, env=env, timeout=timeout * 4 + 300)
    evidence_path = out_dir / "evidence.json"
    if not evidence_path.is_file():
        evidence_path = None
        for line in (result.stdout or "").splitlines():
            if line.startswith("evidence:"):
                candidate = Path(line.split(":", 1)[1].strip())
                if candidate.is_file():
                    evidence_path = candidate
                break
    data = None
    if evidence_path and evidence_path.is_file():
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    return result, data, evidence_path


def summarize(data: dict | None) -> dict:
    if not data:
        return {"verdict": "NO_EVIDENCE"}
    failed = [c["name"] for c in data.get("checks", []) if not c.get("passed")]
    return {
        "verdict": data.get("verdict"),
        "failed_checks": failed,
        "checks_passed": sum(1 for c in data.get("checks", []) if c.get("passed")),
        "checks_total": len(data.get("checks", [])),
        "checks": [
            {
                "name": c.get("name", "unknown"),
                "passed": bool(c.get("passed")),
                "detail": str(c.get("detail", ""))[:500],
            }
            for c in data.get("checks", [])
        ],
        "hidden": (data.get("hidden") or {}).get("stats"),
        "mutation": {
            "killed": (data.get("mutation") or {}).get("killed"),
            "total": (data.get("mutation") or {}).get("total"),
        },
        "coverage": (data.get("coverage") or {}).get("percent"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None, help="run directory (default runs/demo-<ts>)")
    parser.add_argument("--timeout", type=int, default=300, help="per-suite test timeout")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--agent-mode",
        choices=("vultr", "fixed"),
        default="vultr",
        help="vultr: real LLM agent; fixed: stack self-test with the bundled patch",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.out) if args.out else ROOT / "runs" / (
        "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    summary = {"run_dir": str(run_dir), "agent_mode": args.agent_mode, "scenes": {}}

    # ---------------- scene 1 ----------------
    scene1 = run_dir / "scene1"
    repo1 = copy_demo_repo(scene1 / "repo")
    # Verification must run against a pristine checkout: ProofLoop's contract
    # is "base repository + candidate patch", not a worktree the agent mutated.
    verify_repo1 = copy_demo_repo(scene1 / "verify-repo")
    copy_hidden(scene1 / "discount_hidden")
    task1 = verify_repo1 / "task.json"
    task_data = json.loads(task1.read_text(encoding="utf-8"))
    print(f"[scene 1] workspace: {scene1}")

    if args.agent_mode == "vultr":
        from agent.react_loop import ReactAgent, SandboxedRepo
        from agent.vultr_llm import VultrClient, VultrError

        try:
            client = VultrClient(model=args.model)
        except VultrError as exc:
            print(f"BLOCKED: {exc}")
            print("Write the key into .env as VULTR_INFERENCE_API_KEY, then rerun.")
            print("For a keyless stack test use: python demo.py --agent-mode fixed")
            return 2
        base_commit = snapshot.git_init_commit(repo1)
        sandbox = SandboxedRepo(
            repo1,
            allowed_paths=task_data.get("allowed_paths"),
            scratch_dir=run_dir / "agent-scratch",
        )
        agent = ReactAgent(
            client,
            sandbox,
            task_data,
            max_steps=args.max_steps,
            log_path=run_dir / "agent_transcript.json",
        )
        print(f"[scene 1] agent model: {client.model} (max {args.max_steps} steps)")
        agent_result = agent.run()
        print(
            f"[scene 1] agent finished: protocol={agent_result['protocol']} "
            f"steps={agent_result['steps']} tool_calls={agent_result['tool_calls']}"
        )
        patch1 = run_dir / "scene1-agent.patch"
        patch_bytes = make_patch(repo1, base_commit, patch1)
        summary["scenes"]["agent"] = agent_result
    else:
        patch1 = DEMO_REPO / "patches" / "fixed.patch"
        patch_bytes = patch1.read_bytes()
        summary["scenes"]["agent"] = {"mode": "fixed-patch (self-test)"}

    print(f"[scene 1] patch: {patch1} ({len(patch_bytes)} bytes)")
    result1, data1, evidence1 = run_verify(
        verify_repo1, task1, patch1, run_dir / "evidence-pass", args.timeout
    )
    summary["scenes"]["pass"] = summarize(data1)
    summary["scenes"]["pass"]["evidence"] = str(evidence1 or "")
    print(f"[scene 1] verdict: {summary['scenes']['pass']['verdict']}")
    for line in (result1.stdout or "").splitlines()[-16:]:
        print("  " + line)

    # ---------------- scene 2 ----------------
    scene2 = run_dir / "scene2"
    repo2 = copy_demo_repo(scene2 / "repo")
    copy_hidden(scene2 / "discount_hidden")
    patch2 = DEMO_REPO / "patches" / "cheat_hardcode.patch"
    print(f"\n[scene 2] cheat patch: {patch2.name}")
    result2, data2, evidence2 = run_verify(
        repo2, repo2 / "task.json", patch2, run_dir / "evidence-fail", args.timeout
    )
    summary["scenes"]["fail"] = summarize(data2)
    summary["scenes"]["fail"]["evidence"] = str(evidence2 or "")
    print(f"[scene 2] verdict: {summary['scenes']['fail']['verdict']}")
    for line in (result2.stdout or "").splitlines()[-16:]:
        print("  " + line)

    # ---------------- summary ----------------
    pass_ok = summary["scenes"]["pass"]["verdict"] == "PASS"
    fail_ok = summary["scenes"]["fail"]["verdict"] == "FAIL"
    summary["ok"] = bool(pass_ok and fail_ok)
    summary_file = run_dir / "summary.json"
    summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n========== demo summary ==========")
    print(f"scene 1 (fix)   : {summary['scenes']['pass']['verdict']}")
    print(f"scene 2 (cheat) : {summary['scenes']['fail']['verdict']}")
    print(f"expected        : scene1=PASS scene2=FAIL -> {'OK' if summary['ok'] else 'MISMATCH'}")
    print(f"summary: {summary_file}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
