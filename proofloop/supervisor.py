"""Supervisor x Executor loop: drive a CLI coding agent, then verify its patch.

The supervisor copies the repository into a private workspace, asks a local CLI
agent (Claude Code by default) to fix the task there, turns each round of edits
into a patch, and runs the normal `proofloop verify` pipeline.  Failed checks
are fed back to the agent for the next round, up to a fixed number of rounds.

Boundary note: the workspace copy is a *hygiene* boundary -- it keeps official
patches and benchmark answers out of the executor's view.  It is **not** a
security boundary: the executor is a trusted local tool and can still read the
host filesystem.  The security boundary is the Docker sandbox used by
`proofloop verify` when the candidate patch is actually executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from . import snapshot
from .service import _assert_allowed, _safe_out_dir

ROOT = Path(__file__).resolve().parent.parent
AGENT_CLAUDE = "claude"
PROTECTED_DIRS = ("patches", ".benchmarks")
GITIGNORE = "__pycache__/\n.pytest_cache/\n*.pyc\n*.pyo\n"


def _proofloop_version():
    try:
        with open(ROOT / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _find_agent(agent):
    if agent != AGENT_CLAUDE:
        raise ValueError(f"unsupported agent: {agent!r} (only {AGENT_CLAUDE!r} is supported)")
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError(
            "claude executable not found on PATH; install Claude Code or use a supported --agent"
        )
    return exe


def _resolve_inputs(repo, task):
    repo_path = Path(repo).expanduser().resolve()
    task_path = Path(task).expanduser().resolve()
    if not repo_path.is_dir():
        raise ValueError(f"repo not found or not a directory: {repo_path}")
    if not task_path.is_file():
        raise ValueError(f"task file not found: {task_path}")
    if repo_path != task_path.parent:
        raise ValueError(
            "repo must be the directory that contains the task file "
            f"(repo={repo_path}, task parent={task_path.parent})"
        )
    _assert_allowed(repo_path, "repo")
    _assert_allowed(task_path, "task")
    return repo_path, task_path


def _rmtree(path):
    def onerror(func, p, excinfo):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def _git(args, cwd, check=True, timeout=120):
    try:
        p = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {timeout}s: {list(args)!r}") from exc
    if check and p.returncode != 0:
        raise RuntimeError(f"git command failed ({p.returncode}): {list(args)!r}\n{p.stdout}\n{p.stderr}")
    return p


def _prepare_workspace(repo, workspace):
    """Copy `repo` into `workspace`, strip answer keys, and commit the base.

    Returns the base commit.  `workspace/task.json` stays byte-for-byte identical
    to the source file: the executor sees the same task description, only without
    the official patches and benchmark answers.
    """
    workspace = Path(workspace)
    snapshot.copy_repo(Path(repo), workspace)

    for name in PROTECTED_DIRS:
        target = workspace / name
        if not (target.exists() or target.is_symlink()):
            continue
        try:
            if target.is_dir() and not target.is_symlink():
                _rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise RuntimeError(f"cannot remove protected path {target}: {exc}") from exc
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"protected path still exists after removal: {target}")

    src_task = Path(repo) / "task.json"
    dst_task = workspace / "task.json"
    if src_task.is_file() and (
        not dst_task.is_file()
        or snapshot.sha256_file(src_task) != snapshot.sha256_file(dst_task)
    ):
        raise RuntimeError("workspace/task.json does not match the source task file")

    (workspace / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    _git(["init", "-q"], workspace)
    _git(["add", "-A"], workspace)
    _git(
        ["-c", "user.email=proofloop@local", "-c", "user.name=proofloop", "commit", "-qm", "base"],
        workspace,
    )
    return _git(["rev-parse", "HEAD"], workspace).stdout.strip()


def _build_prompt(task_data, workspace, round_no, previous_failed):
    workspace = Path(workspace)
    task_md = workspace / "task.md"
    if task_md.is_file():
        body = task_md.read_text(encoding="utf-8", errors="replace").strip()
    else:
        lines = [
            f"# Task: {task_data.get('name') or 'unnamed task'}",
            "",
            str(task_data.get("description") or "").strip(),
            "",
            "## Acceptance criteria",
            "",
        ]
        lines.extend(f"- {item}" for item in (task_data.get("acceptance") or []))
        body = "\n".join(lines).strip()

    allowed = ", ".join(str(item) for item in (task_data.get("allowed_paths") or []))
    parts = [
        body,
        "",
        "## Execution environment",
        "",
        f"- Repository root: {workspace} (your current working directory).",
        f"- Python interpreter: {sys.executable}",
        "- Fix the task by editing files in this repository.",
        f"- Only modify files under: {allowed or '(only the source files needed to fix the task)'}",
        "- Do not modify tests, conftest.py, task files, .gitignore, or any test harness file.",
        "- Do not read or write outside this repository; do not look for hidden tests or official patches.",
        "- Make focused source changes. Do not create build artifacts, caches, or unrelated files.",
        "- Leave your changes in the working tree; do not commit or reset the git repository.",
    ]
    if round_no > 1 and previous_failed:
        parts.extend(["", "## Previous attempt failed", ""])
        parts.append("ProofLoop rejected the previous patch. Fix these failed checks:")
        parts.append("")
        parts.extend(f"- {name}: {detail}" for name, detail in previous_failed)
    return "\n".join(parts).rstrip() + "\n"


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_agent(workspace, prompt, claude_exe, max_turns, timeout, log_path):
    """Run one headless agent turn and return its exit status and timing.

    Claude Code on Windows is commonly installed as an npm ``claude.cmd`` shim.
    A batch shim cannot safely carry a multi-line/large prompt as an argument
    (cmd.exe truncates at the first newline and expands ``%``), so in that case
    the prompt is piped on stdin while ``-p`` stays on the command line.  Native
    executables get the prompt as the documented positional argument.
    """
    batch_shim = os.name == "nt" and Path(claude_exe).suffix.lower() in (".cmd", ".bat")
    tail = ["--allowedTools", "Read,Edit,Write,Bash", "--max-turns", str(max_turns),
            "--output-format", "text"]
    cmd = [claude_exe, "-p"] + (tail if batch_shim else [prompt] + tail)

    started = time.monotonic()
    timed_out, error, exit_code, stdout, stderr = False, "", None, "", ""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace),
            stdin=subprocess.PIPE if batch_shim else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
            start_new_session=(os.name != "nt"),
        )
        try:
            stdout, stderr = proc.communicate(
                input=prompt if batch_shim else None, timeout=timeout
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                stdout, stderr = (stdout or ""), (stderr or "")
            exit_code = None
    except OSError as exc:
        error = stderr = f"{type(exc).__name__}: {exc}"

    seconds = round(time.monotonic() - started, 3)
    Path(log_path).write_text(
        "\n".join(
            [
                f"agent: {claude_exe}",
                f"exit_code: {exit_code}",
                f"timed_out: {timed_out}",
                f"seconds: {seconds}",
                "",
                "----- stdout -----",
                stdout or "",
                "----- stderr -----",
                stderr or "",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {"exit_code": exit_code, "seconds": seconds, "timed_out": timed_out, "error": error}


def _make_patch(workspace, patch_path, base_commit=None):
    """Stage the workspace and write the diff against the base commit (LF)."""
    _git(["add", "-A"], workspace)
    cmd = ["git", "diff", "--cached", "--binary"] + ([base_commit] if base_commit else [])
    p = subprocess.run(
        cmd,
        cwd=str(workspace),
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"git diff --cached failed ({p.returncode}): {p.stderr.decode('utf-8', 'replace')}"
        )
    data = p.stdout.replace(b"\r\n", b"\n")
    Path(patch_path).write_bytes(data)
    return data


def _verify_candidate(repo, task, patch, out_dir, sandbox, timeout):
    cmd = [
        sys.executable, "-m", "proofloop", "verify", "--repo", str(repo), "--task", str(task),
        "--patch", str(patch), "--out", str(out_dir), "--sandbox", sandbox,
        "--timeout", str(timeout),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run(
            cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return {"verdict": "VERIFY_ERROR", "evidence_path": "",
                "failed_checks": [["verify_timeout", f"verify exceeded 600s: {exc}"]]}

    evidence_path = ""
    for line in (p.stdout or "").splitlines():
        if line.startswith("evidence:"):
            evidence_path = line.split(":", 1)[1].strip()
    error_detail = (p.stderr or p.stdout or "")[-1500:] or f"verify exit={p.returncode}"
    data = {}
    if evidence_path and Path(evidence_path).is_file():
        try:
            data = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            error_detail = f"cannot read evidence {evidence_path}: {exc}"
    if not data:
        return {"verdict": "VERIFY_ERROR", "evidence_path": evidence_path,
                "failed_checks": [["verify_error", error_detail]]}

    failed = [
        [str(c.get("name", "unknown")), str(c.get("detail", ""))]
        for c in data.get("checks", [])
        if not c.get("passed")
    ]
    return {"verdict": str(data.get("verdict", "UNKNOWN")),
            "evidence_path": evidence_path, "failed_checks": failed}


def _write_report_md(path, report):
    lines = [
        "# ProofLoop Supervisor x Executor Loop", "",
        f"- Task: {report['task_name']} (`{report['task']}`)",
        f"- Repo: `{report['repo']}`",
        f"- Workspace: `{report['workspace']}`",
        f"- Agent: `{report['agent']}` (`{report['agent_exe']}`)",
        f"- Started: {report['started_at']}",
        f"- Finished: {report['finished_at']}",
        f"- Final verdict: **{report['final_verdict']}**", "",
        "## Rounds", "",
        "| Round | Agent exit | Agent seconds | Patch sha256 | Verdict | No change |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["rounds"]:
        lines.append(
            f"| {item['round']} | {item['agent_exit']} | {item['agent_seconds']} | "
            f"`{str(item['patch_sha256'])[:16]}` | {item['verdict']} | {item['no_change']} |"
        )
    for item in report["rounds"]:
        lines.extend(["", f"### Round {item['round']}: {item['verdict']}", "",
                      f"- patch: `{item['patch_path']}`",
                      f"- evidence: `{item['evidence_path']}`" if item["evidence_path"]
                      else "- evidence: (none)"])
        if item["failed_checks"]:
            lines.append("- failed checks:")
            lines.extend(f"  - `{name}`: {detail}" for name, detail in item["failed_checks"])
        else:
            lines.append("- failed checks: none")
    lines.extend(["", "## Conclusion", "",
                  f"Final verdict: **{report['final_verdict']}**", "",
                  "本报告由 proofloop loop 生成，agent 输出原文见 round-N-agent.log。", ""])
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def run_loop(repo, task, out, agent=AGENT_CLAUDE, max_rounds=3, max_turns=40,
             agent_timeout=600, timeout=300, sandbox="docker"):
    """Run the supervisor x executor loop and return the report dictionary."""
    repo_path, task_path = _resolve_inputs(repo, task)
    if agent != AGENT_CLAUDE:
        raise ValueError(f"unsupported agent: {agent!r} (only {AGENT_CLAUDE!r} is supported)")
    if max_rounds < 1 or max_turns < 1 or agent_timeout <= 0 or timeout <= 0:
        raise ValueError("max_rounds/max_turns must be >= 1 and timeouts must be positive")
    claude_exe = _find_agent(agent)
    task_data = json.loads(task_path.read_text(encoding="utf-8"))

    run_dir = _safe_out_dir(str(out))
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace = run_dir / "workspace"
    base_commit = _prepare_workspace(repo_path, workspace)

    started_at = datetime.now().isoformat(timespec="seconds")
    rounds, previous_failed, final_verdict = [], [], "ERROR"
    print(f"[loop] task: {task_data.get('name', task_path.name)}")
    print(f"[loop] repo: {repo_path}")
    print(f"[loop] workspace: {workspace}")

    for round_no in range(1, max_rounds + 1):
        prompt = _build_prompt(task_data, workspace, round_no, previous_failed)
        log_path = run_dir / f"round-{round_no}-agent.log"
        print(f"[loop] round {round_no}/{max_rounds}: running agent ...", flush=True)
        agent_result = _run_agent(
            workspace, prompt, claude_exe, max_turns, agent_timeout, log_path
        )
        if agent_result.get("timed_out"):
            print(f"[loop] round {round_no}: agent timed out after {agent_result.get('seconds')}s")
        elif agent_result.get("error"):
            print(f"[loop] round {round_no}: agent error: {agent_result.get('error')}")
        else:
            print(f"[loop] round {round_no}: agent exit={agent_result.get('exit_code')} "
                  f"in {agent_result.get('seconds')}s")

        patch_path = run_dir / f"round-{round_no}.patch"
        record = {
            "round": round_no,
            "agent_exit": agent_result.get("exit_code"),
            "agent_seconds": agent_result.get("seconds"),
            "agent_timed_out": bool(agent_result.get("timed_out")),
            "agent_error": agent_result.get("error", ""),
            "patch_sha256": "",
            "patch_path": str(patch_path),
            "verdict": "PATCH_ERROR",
            "evidence_path": "",
            "failed_checks": [],
            "no_change": False,
        }
        try:
            patch_bytes = _make_patch(workspace, patch_path, base_commit)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            record["failed_checks"] = [["patch_error", str(exc)]]
            rounds.append(record)
            final_verdict = "PATCH_ERROR"
            print(f"[loop] round {round_no}: PATCH_ERROR: {exc}")
            previous_failed = record["failed_checks"]
            continue

        record["patch_sha256"] = hashlib.sha256(patch_bytes).hexdigest()
        record["no_change"] = not patch_bytes.strip()
        if record["no_change"]:
            record["verdict"] = "NO_CHANGE"
            record["failed_checks"] = [["no_change", "the agent produced no code changes"]]
            rounds.append(record)
            final_verdict = "NO_CHANGE"
            print(f"[loop] round {round_no}: NO_CHANGE")
            previous_failed = record["failed_checks"]
            continue

        print(f"[loop] round {round_no}: patch {len(patch_bytes)} bytes "
              f"sha256={record['patch_sha256'][:12]}")
        result = _verify_candidate(
            repo_path, task_path, patch_path, run_dir / f"round-{round_no}", sandbox, timeout
        )
        record["verdict"] = result["verdict"]
        record["evidence_path"] = result["evidence_path"]
        record["failed_checks"] = result["failed_checks"]
        rounds.append(record)
        final_verdict = record["verdict"]
        print(f"[loop] round {round_no}: verdict {final_verdict}")
        for name, detail in record["failed_checks"]:
            print(f"[loop]   FAIL {name}: {detail}")
        if final_verdict == "PASS":
            break
        previous_failed = record["failed_checks"] or [
            ["verdict", f"verification returned {final_verdict}"]
        ]

    report = {
        "task": str(task_path),
        "task_name": str(task_data.get("name") or ""),
        "repo": str(repo_path),
        "workspace": str(workspace),
        "agent": agent,
        "agent_exe": claude_exe,
        "rounds": rounds,
        "final_verdict": final_verdict,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "proofloop_version": _proofloop_version(),
    }
    (run_dir / "supervisor_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    _write_report_md(run_dir / "supervisor_report.md", report)
    print(f"[loop] final verdict: {final_verdict}")
    print(f"[loop] report: {run_dir / 'supervisor_report.json'}")
    return report
