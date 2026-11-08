import argparse
import hmac
import json
import os
import platform
import sys
import tomllib
import uuid
from dataclasses import asdict
from datetime import datetime
from importlib import metadata as importlib_metadata
from pathlib import Path

from . import evidence, mutation, rules, runner, sandbox, snapshot
from .models import Task
from .service import _allowed_roots

ROOT = Path(__file__).resolve().parent.parent


def _package_version(name):
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _proofloop_version():
    try:
        with open(ROOT / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def load_task(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    test_command = raw.get("test_command", "python -m pytest -q").strip()
    if not (test_command.startswith("python -m pytest") or test_command.startswith("pytest")):
        raise ValueError("test_command must be a pytest command")
    if any(ch in test_command for ch in ";&|><`$"):
        raise ValueError("test_command contains unsafe shell characters")
    allowed_paths = raw.get("allowed_paths", [])
    for item in allowed_paths:
        p = Path(item)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"allowed_paths must be relative and stay inside the repo: {item}")
    max_mutants = max(1, min(int(raw.get("max_mutants", 12)), 32))
    mutation_threshold = max(0.0, min(float(raw.get("mutation_threshold", 0.0)), 1.0))
    coverage_threshold = max(0.0, min(float(raw.get("coverage_threshold", 0.0)), 1.0))
    source = raw.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError("task source must be a JSON object when present")
    return Task(
        name=raw.get("name", "unnamed task"),
        description=raw.get("description", ""),
        acceptance=raw.get("acceptance", []),
        allowed_paths=allowed_paths,
        forbid_test_changes=raw.get("forbid_test_changes", True),
        test_command=test_command,
        hidden_tests_dir=raw.get("hidden_tests_dir", ""),
        mutation_threshold=mutation_threshold,
        max_mutants=max_mutants,
        mutation_enforce=raw.get("mutation_enforce", False),
        coverage_threshold=coverage_threshold,
        coverage_source=raw.get("coverage_source", ""),
        coverage_enforce=raw.get("coverage_enforce", False),
        source=source,
    )


def verify(args):
    repo = Path(args.repo).resolve()
    task = getattr(args, "task_override", None) or load_task(args.task)
    patch = Path(args.patch).resolve()
    if not repo.is_dir():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2
    if not patch.is_file():
        print(f"patch not found: {patch}", file=sys.stderr)
        return 2

    if args.out:
        out = Path(args.out).resolve()
    else:
        out = Path.cwd() / "runs" / (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        )
    if out == (ROOT / "runs").resolve():
        print(f"refusing to use the runs root as out_dir: {out}", file=sys.stderr)
        return 2
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(
            f"out_dir already exists; refusing to overwrite existing evidence: {out}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"cannot create out_dir {out}: {exc}", file=sys.stderr)
        return 2

    base, base_commit = snapshot.prepare_base(repo, out)
    base_tree = snapshot.rev_parse_tree(base)
    # The candidate must be produced from the pristine base BEFORE the
    # baseline run: baseline tests may leave state behind that the candidate
    # would otherwise inherit (state-leak false accept).
    candidate = snapshot.prepare_candidate(base, out, patch)
    # Freeze the staged candidate view before any test executes; candidate
    # code may restore weakened test files at import time.
    frozen = snapshot.freeze_view(candidate)
    candidate_tree = frozen["tree"]
    base_blobs = snapshot.tree_blobs(base)
    candidate_blobs = snapshot.index_blobs(candidate)
    frozen["test_literals"] = rules.collect_test_literals(candidate)
    # Untracked files present before any candidate code runs.  Anything that
    # appears afterwards was written at runtime and is quarantined between
    # runs so later pytest invocations cannot load it.
    runtime_baseline = snapshot.untracked_files(candidate)

    baseline = runner.run_tests(
        base, task.test_command, out / "baseline.xml", args.timeout, args.sandbox
    )
    cand_result = runner.run_tests(
        candidate, task.test_command, out / "candidate.xml", args.timeout, args.sandbox
    )
    runtime_reports = [snapshot.quarantine_runtime_files(candidate, runtime_baseline)]

    hidden_result = None
    hidden_expected = None
    hidden_dir_path = None
    hidden_check = {
        "name": "hidden_tests_passed",
        "passed": True,
        "detail": "no hidden tests configured",
    }
    hidden_nodeid_check = {
        "name": "hidden_tests_expected",
        "passed": True,
        "detail": "no hidden tests configured",
    }
    if task.hidden_tests_dir:
        hidden_dir = (repo / task.hidden_tests_dir).resolve()
        if hidden_dir.is_dir():
            allowed_roots = _allowed_roots()
            if not any(hidden_dir.is_relative_to(root) for root in allowed_roots):
                raise ValueError(
                    "hidden_tests_dir resolves outside the allowed roots "
                    f"({', '.join(str(r) for r in allowed_roots)}): {hidden_dir}"
                )
            if hidden_dir == repo or hidden_dir.is_relative_to(repo):
                raise ValueError(
                    f"hidden_tests_dir must not live inside the candidate repo: {hidden_dir}"
                )
            hidden_dir_path = hidden_dir
            hidden_files = {}
            for path in sorted(hidden_dir.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(hidden_dir).as_posix()
                    hidden_files[rel] = snapshot.sha256_file(path)
            # Expected hidden nodeids are collected on the pristine base, not
            # on the candidate, so candidate code cannot invent test names.
            collection = runner.collect_nodeids(
                base, hidden_dir, args.timeout, args.sandbox
            )
            hidden_expected = {
                "dir": str(hidden_dir),
                "files": hidden_files,
                "nodeids": collection["nodeids"],
                "collect_exit": collection["exit_code"],
                "command": collection["command"],
            }
            hidden_result = runner.run_tests(
                candidate,
                f"python -m pytest -q {hidden_dir.as_posix()}",
                out / "hidden.xml",
                args.timeout,
                args.sandbox,
                hidden_dir=hidden_dir,
            )
            hidden_check = rules.check_hidden_tests(
                hidden_result["stats"], hidden_result["exit_code"]
            )
            hidden_nodeid_check = rules.check_hidden_nodeids(
                collection["nodeids"], hidden_result["stats"], collection["exit_code"]
            )
        else:
            hidden_check = {
                "name": "hidden_tests_passed",
                "passed": False,
                "detail": f"hidden tests dir not found: {hidden_dir}",
            }
            hidden_nodeid_check = {
                "name": "hidden_tests_expected",
                "passed": False,
                "detail": f"hidden tests dir not found: {hidden_dir}",
            }

    runtime_reports.append(snapshot.quarantine_runtime_files(candidate, runtime_baseline))

    diff = frozen["diff_text"]
    changed = frozen["changed_files"]

    checks = [
        rules.check_baseline_reproduced(baseline["stats"], baseline["exit_code"]),
        rules.check_candidate_tests(
            cand_result["stats"], cand_result["exit_code"], baseline["stats"]
        ),
        rules.check_visible_tests_match(baseline["stats"], cand_result["stats"]),
        hidden_check,
        hidden_nodeid_check,
        rules.check_candidate_tree_frozen(frozen["tree"], candidate),
        rules.check_test_integrity(base_blobs, candidate_blobs, task),
        rules.check_scope(frozen, task),
        rules.check_noop(frozen),
        rules.check_hardcode(frozen),
        rules.check_runtime_tamper(frozen),
        rules.check_runtime_file_injection(runtime_reports),
    ]

    if not checks[0]["passed"]:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif all(c["passed"] for c in checks[1:]):
        verdict = "PASS"
    else:
        verdict = "FAIL"

    mutation_result = None
    prior_ok = checks[0]["passed"] and all(c["passed"] for c in checks[1:])
    if task.mutation_threshold > 0 and prior_ok:
        focus_py = [f for f in changed if f.endswith(".py")]
        mutation_result = mutation.run_mutation(
            candidate,
            task,
            out,
            args.timeout,
            args.sandbox,
            focus_files=focus_py,
            hidden_dir=hidden_dir_path,
        )
        checks.append(
            rules.check_mutation(
                mutation_result, task.mutation_threshold, enforce=task.mutation_enforce
            )
        )
        if not checks[-1]["passed"]:
            verdict = "FAIL"
    else:
        checks.append(
            {
                "name": "mutation_score",
                "passed": True,
                "detail": "skipped (threshold=0 or prior checks failed)",
            }
        )

    coverage_result = None
    # Hidden/mutation runs may have planted files too; quarantine again and
    # refresh the check before coverage executes on the candidate worktree.
    runtime_reports.append(snapshot.quarantine_runtime_files(candidate, runtime_baseline))
    runtime_check = rules.check_runtime_file_injection(runtime_reports)
    checks = [
        runtime_check if c["name"] == "runtime_file_injection" else c for c in checks
    ]
    if not runtime_check["passed"]:
        verdict = "FAIL"
    prior_ok = checks[0]["passed"] and all(c["passed"] for c in checks[1:])
    if task.coverage_threshold > 0 and prior_ok:
        coverage_result = runner.run_coverage(
            candidate, task, args.timeout, args.sandbox, hidden_dir=hidden_dir_path
        )
        checks.append(
            rules.check_coverage(
                coverage_result, task.coverage_threshold, task.coverage_enforce
            )
        )
        if not checks[-1]["passed"]:
            verdict = "FAIL"
    else:
        checks.append(
            {
                "name": "coverage",
                "passed": True,
                "detail": "skipped (threshold=0 or prior checks failed)",
            }
        )

    patch_sha = snapshot.sha256_file(patch)
    try:
        patch_rel = patch.relative_to(ROOT).as_posix()
    except ValueError:
        patch_rel = str(patch)
    executor = {"kind": "patch", "patch": patch_rel, "sha256": patch_sha}

    commands = {
        "baseline": baseline.get("command"),
        "candidate": cand_result.get("command"),
        "hidden": hidden_result.get("command") if hidden_result else None,
        "mutation": (mutation_result or {}).get("command"),
        "coverage": (coverage_result or {}).get("command"),
    }

    advisory_checks = [
        c["name"] for c in checks if "advisory" in str(c["detail"]).lower()
    ]
    skipped_checks = [
        c["name"] for c in checks if str(c["detail"]).startswith("skipped")
    ]
    unverified = [
        "acceptance criteria are not mapped one-by-one to tests; only the aggregate gates are enforced",
        "no human code review was performed",
    ]
    if task.mutation_threshold > 0 and not task.mutation_enforce:
        unverified.append("mutation_score is advisory for this task (not enforced)")
    if task.coverage_threshold > 0 and not task.coverage_enforce:
        unverified.append("coverage is advisory for this task (not enforced)")
    if advisory_checks:
        unverified.append("advisory checks not enforced: " + ", ".join(advisory_checks))
    if skipped_checks:
        unverified.append("skipped checks: " + ", ".join(skipped_checks))

    if baseline.get("sandbox") == "docker":
        tool_versions = sandbox.image_tool_versions()
        docker_image_id = sandbox.image_id()
    else:
        tool_versions = {
            "pytest": _package_version("pytest"),
            "coverage": _package_version("coverage"),
        }
        docker_image_id = None

    data = {
        "run_id": out.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "task": asdict(task),
        "repo": str(repo),
        "base_commit": base_commit,
        "base_tree": base_tree,
        "candidate_tree": candidate_tree,
        "changed_files": changed,
        "executor": executor,
        "acceptance_sha256": evidence.acceptance_sha256(task.acceptance),
        "commands": commands,
        "unverified": unverified,
        "patch_sha256": patch_sha,
        "diff_sha256": frozen["diff_sha256"],
        "checks": checks,
        "runtime_files": runtime_reports,
        "baseline": {
            "exit_code": baseline["exit_code"],
            "stats": baseline["stats"].to_dict(),
            "stdout": baseline["stdout"],
            "stderr": baseline["stderr"],
        },
        "candidate": {
            "exit_code": cand_result["exit_code"],
            "stats": cand_result["stats"].to_dict(),
            "stdout": cand_result["stdout"],
            "stderr": cand_result["stderr"],
        },
        "hidden": (
            {
                "exit_code": hidden_result["exit_code"],
                "stats": hidden_result["stats"].to_dict(),
                "stdout": hidden_result["stdout"],
                "stderr": hidden_result["stderr"],
            }
            if hidden_result
            else None
        ),
        "hidden_expected": hidden_expected,
        "mutation": mutation_result,
        "coverage": coverage_result,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "plugin": "proofloop-mvp",
            "sandbox": baseline.get("sandbox", "unknown"),
            "pytest": tool_versions["pytest"],
            "coverage": tool_versions["coverage"],
            "docker_image_id": docker_image_id,
            "proofloop": _proofloop_version(),
        },
    }
    evidence.write_evidence(out, data)

    print(f"ProofLoop: {task.name}")
    print(f"verdict: {verdict}")
    print(f"baseline: exit={baseline['exit_code']} stats={baseline['stats'].to_dict()}")
    print(f"candidate: exit={cand_result['exit_code']} stats={cand_result['stats'].to_dict()}")
    if hidden_result:
        print(f"hidden: exit={hidden_result['exit_code']} stats={hidden_result['stats'].to_dict()}")
    if mutation_result:
        print(
            "mutation: killed={}/{} score={:.2f} skipped={}".format(
                mutation_result["killed"],
                mutation_result["total"],
                mutation_result["score"],
                mutation_result["skipped"],
            )
        )
    if coverage_result:
        print(
            "coverage: {:.2f}% source={} skipped={}".format(
                coverage_result["percent"],
                coverage_result["source"],
                coverage_result["skipped"],
            )
        )
    for c in checks:
        mark = "PASS" if c["passed"] else "FAIL"
        print(f"[{mark}] {c['name']}: {c['detail']}")
    print(f"evidence: {out / 'evidence.json'}")
    print(f"report:   {out / 'report.html'}")
    return 0 if verdict == "PASS" else (2 if verdict == "INSUFFICIENT_EVIDENCE" else 1)


def _evidence_summary(data):
    baseline = (data.get("baseline") or {}).get("stats") or {}
    candidate = (data.get("candidate") or {}).get("stats") or {}
    hidden = ((data.get("hidden") or {}).get("stats"))
    mutation = data.get("mutation") or {}
    coverage = data.get("coverage") or {}
    return {
        "baseline": baseline,
        "candidate": candidate,
        "hidden": hidden,
        "mutation": f"{mutation.get('killed')}/{mutation.get('total')} "
        f"score={mutation.get('score')}",
        "coverage": coverage.get("percent"),
    }


def replay(args):
    evidence_path = Path(args.evidence).resolve()
    if not evidence_path.is_file():
        print(f"evidence not found: {evidence_path}", file=sys.stderr)
        return 2
    try:
        old = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read evidence: {exc}", file=sys.stderr)
        return 2

    patch = Path(args.patch).resolve()
    if not patch.is_file():
        print(f"patch not found: {patch}", file=sys.stderr)
        return 2
    patch_sha = snapshot.sha256_file(patch)
    if patch_sha != old.get("patch_sha256"):
        print("replay refused: patch sha256 does not match the evidence sheet", file=sys.stderr)
        print(f"  evidence: {old.get('patch_sha256')}", file=sys.stderr)
        print(f"  actual:   {patch_sha}", file=sys.stderr)
        return 2

    task = load_task(args.task)
    if old.get("acceptance_sha256"):
        actual_acceptance = evidence.acceptance_sha256(task.acceptance)
        if actual_acceptance != old["acceptance_sha256"]:
            print(
                "replay refused: task acceptance criteria do not match the evidence sheet",
                file=sys.stderr,
            )
            print(f"  evidence: {old['acceptance_sha256']}", file=sys.stderr)
            print(f"  actual:   {actual_acceptance}", file=sys.stderr)
            return 2
    if not task.source:
        recorded_source = (old.get("task") or {}).get("source")
        if isinstance(recorded_source, dict) and recorded_source:
            # Carry the provenance recorded in the evidence sheet forward.
            task.source = recorded_source

    out = Path(args.out).resolve()
    new_ev_path = out / "evidence.json"
    ns = argparse.Namespace(
        repo=args.repo,
        task=args.task,
        patch=args.patch,
        out=str(out),
        timeout=args.timeout,
        sandbox=args.sandbox,
        task_override=task,
    )
    verify(ns)
    if not new_ev_path.is_file():
        print("replay failed: the verification produced no evidence", file=sys.stderr)
        return 2
    new = json.loads(new_ev_path.read_text(encoding="utf-8"))

    old_verdict = old.get("verdict")
    new_verdict = new.get("verdict")
    consistent = old_verdict == new_verdict

    print("replay comparison:")
    print(f"  old verdict: {old_verdict}")
    print(f"  new verdict: {new_verdict}")
    old_summary = _evidence_summary(old)
    new_summary = _evidence_summary(new)
    for label in ("baseline", "candidate", "hidden", "mutation", "coverage"):
        print(f"  old {label}: {old_summary[label]}")
        print(f"  new {label}: {new_summary[label]}")
    print(f"  consistent: {consistent}")
    print(f"  new evidence: {new_ev_path}")
    return 0 if consistent else 1


def verify_evidence(args):
    path = Path(args.evidence).resolve()
    if not path.is_file():
        print(f"evidence not found: {path}", file=sys.stderr)
        return 1
    sidecar = path.parent / "evidence.sha256"
    if not sidecar.is_file():
        print(f"evidence.sha256 sidecar missing: {sidecar}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = sidecar.read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read evidence: {exc}", file=sys.stderr)
        return 1
    actual = evidence.canonical_json_digest(data)
    if not expected or not hmac.compare_digest(expected, actual):
        print("evidence hash mismatch: evidence.json has been modified", file=sys.stderr)
        print(f"  sidecar:  {expected}", file=sys.stderr)
        print(f"  computed: {actual}", file=sys.stderr)
        return 1
    embedded = data.get("evidence_sha256")
    if embedded and not hmac.compare_digest(str(embedded), actual):
        print("evidence hash mismatch: embedded evidence_sha256 is wrong", file=sys.stderr)
        return 1
    print(f"evidence hash OK: {actual}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofloop", description="AI delivery acceptance engine")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("verify", help="verify a candidate patch against a task")
    p.add_argument("--repo", required=True, help="path to the repository")
    p.add_argument("--task", required=True, help="path to task.json")
    p.add_argument("--patch", required=True, help="path to candidate patch")
    p.add_argument("--out", default=None, help="output directory (default: ./runs/<timestamp>)")
    p.add_argument("--timeout", type=int, default=300, help="test timeout seconds")
    p.add_argument(
        "--sandbox",
        choices=["auto", "docker", "none"],
        default=os.environ.get("PROOFLOOP_SANDBOX", "auto"),
        help="run tests in the Docker sandbox (auto uses it when available)",
    )

    r = sub.add_parser(
        "replay",
        help="re-run a verification from an evidence sheet and compare verdicts",
    )
    r.add_argument("--evidence", required=True, help="path to runs/<run>/evidence.json")
    r.add_argument("--repo", required=True, help="path to the repository")
    r.add_argument("--task", required=True, help="path to task.json")
    r.add_argument("--patch", required=True, help="path to the candidate patch")
    r.add_argument("--out", required=True, help="new output directory (must not exist)")
    r.add_argument("--timeout", type=int, default=300, help="test timeout seconds")
    r.add_argument(
        "--sandbox",
        choices=["auto", "docker", "none"],
        default=os.environ.get("PROOFLOOP_SANDBOX", "auto"),
        help="run tests in the Docker sandbox (auto uses it when available)",
    )

    v = sub.add_parser(
        "verify-evidence",
        help="verify evidence.json against its evidence.sha256 sidecar",
    )
    v.add_argument("--evidence", required=True, help="path to runs/<run>/evidence.json")

    lp = sub.add_parser(
        "loop",
        help="drive a CLI coding agent in a private workspace and verify each round",
    )
    lp.add_argument("--repo", required=True, help="path to the repository")
    lp.add_argument("--task", required=True, help="path to task.json")
    lp.add_argument("--out", required=True, help="new output directory under runs/")
    lp.add_argument(
        "--agent", default="claude", choices=["claude"], help="CLI coding agent"
    )
    lp.add_argument("--max-rounds", type=int, default=3, help="maximum supervisor rounds")
    lp.add_argument("--max-turns", type=int, default=40, help="passed to claude --max-turns")
    lp.add_argument("--agent-timeout", type=int, default=600, help="agent timeout seconds")
    lp.add_argument("--timeout", type=int, default=300, help="verify test timeout seconds")
    lp.add_argument(
        "--sandbox",
        choices=["auto", "docker", "none"],
        default=os.environ.get("PROOFLOOP_SANDBOX", "auto"),
        help="run tests in the Docker sandbox (auto uses it when available)",
    )

    args = parser.parse_args(argv)
    if args.command == "verify":
        return verify(args)
    if args.command == "replay":
        return replay(args)
    if args.command == "verify-evidence":
        return verify_evidence(args)
    if args.command == "loop":
        from . import supervisor

        try:
            report = supervisor.run_loop(
                repo=args.repo,
                task=args.task,
                out=args.out,
                agent=args.agent,
                max_rounds=args.max_rounds,
                max_turns=args.max_turns,
                agent_timeout=args.agent_timeout,
                timeout=args.timeout,
                sandbox=args.sandbox,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"loop failed: {exc}", file=sys.stderr)
            return 2
        return 0 if report.get("final_verdict") == "PASS" else 1
    parser.print_help()
    return 2
