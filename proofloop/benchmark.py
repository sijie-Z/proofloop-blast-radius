from __future__ import annotations

import argparse
import json
import platform
import sys
import tomllib
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from . import sandbox
from .service import verify_repo_impl

ROOT = Path(__file__).resolve().parent.parent


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _display_path(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return path


def _proofloop_version():
    try:
        with open(ROOT / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _pkg_version(name):
    try:
        return importlib_metadata.version(name)
    except Exception:
        return None


def _evidence_details(evidence_path: str):
    """Return (mutation summary, coverage percent, environment) for a run."""
    if not evidence_path:
        return "", "", None
    path = Path(evidence_path)
    if not path.is_file():
        return "", "", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", None
    mutation = data.get("mutation") or {}
    coverage = data.get("coverage") or {}
    mutation_text = (
        f"{mutation.get('killed')}/{mutation.get('total')}={mutation.get('score')}"
        if mutation
        else ""
    )
    coverage_text = f"{coverage.get('percent')}%" if coverage else ""
    return mutation_text, coverage_text, data.get("environment")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofloop.benchmark")
    parser.add_argument("--manifest", required=True, help="benchmark manifest JSON")
    args = parser.parse_args(argv)
    manifest_path = resolve(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    if not cases:
        print("no cases in manifest")
        return 2

    # Every benchmark run gets a unique output root; historical runs are kept.
    run_root = ROOT / "runs" / (
        "bench_"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    print(f"benchmark run root: {run_root}")
    results = []
    environment = None
    for case in cases:
        name = case["name"]
        out = run_root / name
        mutation_text = ""
        coverage_text = ""
        try:
            result = verify_repo_impl(
                str(resolve(case["repo"])),
                str(resolve(case["task"])),
                str(resolve(case["patch"])),
                str(out),
            )
            actual = result["verdict"]
            exit_code = result["exit_code"]
            mutation_text, coverage_text, case_environment = _evidence_details(
                result["evidence_path"]
            )
            if environment is None and case_environment:
                environment = case_environment
            evidence_path = _display_path(result["evidence_path"])
        except Exception as exc:
            actual = "ERROR"
            exit_code = -1
            evidence_path = ""
            print(f"[ERROR] {name}: {exc}")
        expected = case["expected"]
        ok = actual == expected
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "ok": ok,
                "exit_code": exit_code,
                "evidence_path": evidence_path,
                "mutation": mutation_text,
                "coverage": coverage_text,
            }
        )
        print(f"[{'OK' if ok else 'MISMATCH'}] {name}: expected={expected} actual={actual}")

    correct = sum(1 for r in results if r["ok"])
    false_accept = sum(1 for r in results if r["expected"] != "PASS" and r["actual"] == "PASS")
    false_reject = sum(1 for r in results if r["expected"] == "PASS" and r["actual"] != "PASS")
    total = len(results)

    expected_counts = Counter(case["expected"] for case in cases)
    actual_counts = Counter(r["actual"] for r in results)
    environment = environment or {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pytest": _pkg_version("pytest"),
        "coverage": _pkg_version("coverage"),
        "docker_image_id": sandbox.image_id(),
    }
    lines = [
        "# ProofLoop benchmark report",
        "",
        f"- generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- command: python -m proofloop.benchmark --manifest {args.manifest}",
        f"- proofloop version: {_proofloop_version()}",
        "- environment: "
        f"python={environment.get('python')}, "
        f"pytest={environment.get('pytest')}, "
        f"coverage={environment.get('coverage')}, "
        f"docker_image_id={environment.get('docker_image_id')}",
        "",
        f"- cases: {total}",
        f"- correct verdicts: {correct}/{total}",
        "- expected composition: "
        + ", ".join(
            f"{verdict}={expected_counts.get(verdict, 0)}"
            for verdict in ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE", "ERROR")
        ),
        "- actual composition: "
        + ", ".join(
            f"{verdict}={actual_counts.get(verdict, 0)}"
            for verdict in ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE", "ERROR")
        ),
        f"- false accept: {false_accept} (expected != PASS and actual == PASS)",
        f"- false reject: {false_reject} (expected == PASS and actual != PASS)",
        "",
        "| case | expected | actual | mutation | coverage | verdict | evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {r['expected']} | {r['actual']} | "
            f"{r['mutation'] or '-'} | {r['coverage'] or '-'} | "
            f"{'OK' if r['ok'] else 'MISMATCH'} | {r['evidence_path']} |"
        )
    lines += [
        "",
        f"自建对抗集，{correct}/{total} 表示判决与 manifest 期望一致，"
        "不代表通用准确率/统计显著性。",
        "",
    ]
    report = "\n".join(lines)
    report_path = manifest_path.parent / "last-report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\ncorrect: {correct}/{total}, false_accept={false_accept}, false_reject={false_reject}")
    print(f"report: {report_path}")
    return 0 if correct == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
