import re
from pathlib import Path

from .snapshot import (
    sha256_file,
    tracked_worktree_changes,
    write_tree,
)

TEST_DIR_NAMES = {"tests", "test", "__tests__"}

# Files that can change how pytest/coverage are collected or imported even
# though they are not test files themselves (plugin shadowing, .pth hooks,
# config tweaks).  A patch touching any of these can fake a green run.
HARNESS_FILENAMES = {
    "conftest.py",
    "pytest.py",
    "sitecustomize.py",
    "usercustomize.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "pyproject.toml",
    "noxfile.py",
    ".coveragerc",
    ".env",
    ".gitattributes",
    ".gitignore",
}


_TAMPER_PATTERNS = (
    ("os._exit", "os._exit"),
    ("atexit", "atexit"),
    ("--junitxml", "--junitxml"),
    ("junitxml", "junitxml"),
    ("cov.save", "cov.save"),
    ("coverage.coverage", "coverage.Coverage"),
    ("coverage.json", "coverage.json"),
    (".coverage", ".coverage data file"),
    ("sys.modules[", "sys.modules[...] assignment"),
    ("pytest_plugins", "pytest_plugins"),
)
_PYTEST_HOOK_RE = re.compile(r"^\s*(?:async\s+)?def\s+pytest_[a-z0-9_]+\s*\(")


def is_test_file(rel_path):
    rel = rel_path.replace("\\", "/")
    parts = rel.split("/")
    name = parts[-1]
    return (
        any(p in TEST_DIR_NAMES for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def is_test_harness_file(rel_path):
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    if name in HARNESS_FILENAMES:
        return True
    if name.endswith(".pth"):
        return True
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    if name.startswith("pytest_") and name.endswith(".py"):
        return True
    return False


def iter_test_files(root):
    root = Path(root)
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if is_test_file(rel):
            yield rel, path


def test_file_hashes(root):
    return {rel: sha256_file(path) for rel, path in iter_test_files(root)}


def collect_test_literals(root):
    literals = set()
    for _, path in iter_test_files(root):
        try:
            literals.update(
                _numeric_literals(path.read_text(encoding="utf-8", errors="replace"))
            )
        except OSError:
            pass
    literals.discard("0")
    literals.discard("1")
    return literals


def check_baseline_reproduced(stats, exit_code):
    reasons = []
    if stats.parse_error:
        reasons.append(f"junit unreadable: {stats.parse_error}")
    real_failures = [
        case
        for case in stats.cases
        if case["status"] in ("failed", "error") and case["classname"]
    ]
    if stats.failed + stats.errors == 0:
        reasons.append(
            "no failing tests in junit (exit-code-only failures do not count)"
        )
    elif not real_failures:
        reasons.append("only collection/setup errors, no test-level failure")
    ok = not reasons
    detail = (
        f"baseline exit={exit_code}, total={stats.total}, "
        f"failed={stats.failed}, errors={stats.errors}"
    )
    if reasons:
        detail += "; failed: " + "; ".join(reasons)
    return {"name": "baseline_reproduced", "passed": ok, "detail": detail}


def check_candidate_tests(stats, exit_code, baseline_stats):
    baseline_executed = max(0, baseline_stats.total - baseline_stats.skipped)
    reasons = []
    if stats.parse_error:
        reasons.append(f"junit unreadable: {stats.parse_error}")
    if exit_code != 0:
        reasons.append(f"exit={exit_code} (expected 0)")
    if stats.failed > 0:
        reasons.append(f"{stats.failed} test(s) failed")
    if stats.errors > 0:
        reasons.append(f"{stats.errors} test error(s)")
    if stats.total == 0:
        reasons.append("no tests were collected")
    if stats.skipped > baseline_stats.skipped:
        reasons.append(
            f"skipped grew from baseline {baseline_stats.skipped} to {stats.skipped}"
        )
    if stats.passed < baseline_executed:
        reasons.append(
            f"passed {stats.passed} < baseline executed {baseline_executed} "
            f"(baseline total={baseline_stats.total}, skipped={baseline_stats.skipped})"
        )
    ok = not reasons
    detail = (
        f"candidate exit={exit_code}, total={stats.total}, passed={stats.passed}, "
        f"failed={stats.failed}, errors={stats.errors}, skipped={stats.skipped}; "
        f"baseline total={baseline_stats.total}, passed={baseline_stats.passed}, "
        f"failed={baseline_stats.failed}, skipped={baseline_stats.skipped}, "
        f"executed={baseline_executed}"
    )
    if reasons:
        detail += "; failed: " + "; ".join(reasons)
    return {"name": "candidate_tests_passed", "passed": ok, "detail": detail}


def _visible_case_set(stats):
    # Canonical module.py::test-id keys: junit classnames include the
    # rootdir-relative path, which differs between the base and candidate
    # checkouts on host runs (base/... vs candidate/...).
    return {junit_case_nodeid(case) for case in stats.cases} - {None}


def check_visible_tests_match(baseline_stats, candidate_stats):
    base = _visible_case_set(baseline_stats)
    cand = _visible_case_set(candidate_stats)
    missing = sorted(base - cand)
    extra = sorted(cand - base)
    reasons = []
    if not base:
        reasons.append("baseline junit has no testcases")
    if baseline_stats.parse_error:
        reasons.append(f"baseline junit unreadable: {baseline_stats.parse_error}")
    if candidate_stats.parse_error:
        reasons.append(f"candidate junit unreadable: {candidate_stats.parse_error}")
    if missing:
        reasons.append("missing in candidate: " + ", ".join(missing[:8]))
    if extra:
        reasons.append("unexpected in candidate: " + ", ".join(extra[:8]))
    ok = not reasons
    detail = f"baseline testcases={len(base)}, candidate testcases={len(cand)}"
    if reasons:
        detail += "; failed: " + "; ".join(reasons)
    return {"name": "visible_tests_match_baseline", "passed": ok, "detail": detail}


def check_hidden_tests(stats, exit_code):
    reasons = []
    if stats.parse_error:
        reasons.append(f"junit unreadable: {stats.parse_error}")
    if exit_code != 0:
        reasons.append(f"exit={exit_code} (expected 0)")
    if stats.failed > 0:
        reasons.append(f"{stats.failed} test(s) failed")
    if stats.errors > 0:
        reasons.append(f"{stats.errors} test error(s)")
    if stats.total == 0:
        reasons.append("no hidden tests collected")
    if stats.skipped > 0:
        reasons.append(f"{stats.skipped} hidden test(s) skipped")
    ok = not reasons
    detail = (
        f"hidden exit={exit_code}, total={stats.total}, passed={stats.passed}, "
        f"failed={stats.failed}, errors={stats.errors}, skipped={stats.skipped}"
    )
    if reasons:
        detail += "; failed: " + "; ".join(reasons)
    return {"name": "hidden_tests_passed", "passed": ok, "detail": detail}


def canonical_nodeid(nodeid):
    """Reduce a collect-only nodeid to 'module.py::test-id' form."""
    nodeid = str(nodeid).replace("\\", "/")
    path, sep, rest = nodeid.partition("::")
    if not sep or not rest:
        return None
    module = path.rsplit("/", 1)[-1]
    if not module.endswith(".py"):
        return None
    return f"{module}::{rest}"


def junit_case_nodeid(case):
    """Reduce a junit testcase to the same 'module.py::test-id' form."""
    classname = (case.get("classname") or "").replace("\\", "/")
    name = case.get("name") or ""
    if not classname or not name:
        return None
    module = classname.split(".")[-1]
    return f"{module}.py::{name}"


def check_hidden_nodeids(expected_nodeids, stats, collect_exit):
    expected = [canonical_nodeid(n) for n in (expected_nodeids or [])]
    expected_set = {n for n in expected if n}
    actual_set = {junit_case_nodeid(c) for c in stats.cases}
    actual_set.discard(None)
    reasons = []
    if collect_exit != 0 or not expected_set:
        reasons.append(
            f"hidden collection failed (exit={collect_exit}, expected={len(expected_set)})"
        )
    if stats.parse_error:
        reasons.append(f"hidden junit unreadable: {stats.parse_error}")
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing:
        reasons.append("missing hidden tests: " + ", ".join(missing[:8]))
    if extra:
        reasons.append("unexpected hidden tests: " + ", ".join(extra[:8]))
    bad_status = [
        f"{junit_case_nodeid(c)}={c['status']}"
        for c in stats.cases
        if c["status"] != "passed"
    ]
    if bad_status:
        reasons.append("hidden tests not passed: " + ", ".join(bad_status[:8]))
    ok = not reasons
    detail = (
        f"expected nodeids={len(expected_set)}, candidate nodeids={len(actual_set)}"
    )
    if reasons:
        detail += "; failed: " + "; ".join(reasons)
    return {"name": "hidden_tests_expected", "passed": ok, "detail": detail}


def check_mutation(result, threshold, enforce=True):
    if not result or result.get("skipped"):
        reason = (result or {}).get("reason", "mutation testing skipped")
        ok = not enforce
        detail = f"mutation skipped: {reason}"
        if not enforce:
            detail = "advisory (not enforced): " + detail
        return {"name": "mutation_score", "passed": ok, "detail": detail}
    total = result.get("total", 0)
    if total == 0:
        ok = not enforce
        detail = "no mutants generated"
        if not enforce:
            detail = "advisory (not enforced): " + detail
        return {"name": "mutation_score", "passed": ok, "detail": detail}
    score = result.get("score", 0.0)
    below = score < threshold
    ok = not (below and enforce)
    detail = (
        f"killed={result['killed']}/{total}, survived={result['survived']}, "
        f"score={score:.2f}, threshold={threshold:.2f}"
    )
    if below and not enforce:
        detail = "advisory (not enforced): " + detail
    if result.get("survivors"):
        detail += "; survivors: " + ", ".join(result["survivors"][:5])
    return {"name": "mutation_score", "passed": ok, "detail": detail}


def check_coverage(result, threshold, enforce=True):
    if not result or result.get("skipped"):
        reason = (result or {}).get("reason", "coverage skipped")
        ok = not enforce
        detail = f"coverage not measured: {reason}"
        if not enforce:
            detail = "advisory (not enforced): " + detail
        return {"name": "coverage", "passed": ok, "detail": detail}
    percent = float(result.get("percent", 0.0))
    threshold_percent = float(threshold) * 100.0
    below = percent < threshold_percent
    ok = not (below and enforce)
    detail = (
        f"source={result.get('source', '?')}, coverage={percent:.2f}%, "
        f"threshold={threshold_percent:.2f}% ({threshold:.2f})"
    )
    if below and not enforce:
        detail = "advisory (not enforced): " + detail
    return {"name": "coverage", "passed": ok, "detail": detail}


def check_test_integrity(base_blobs, candidate_blobs, task):
    """Compare base HEAD blobs with the staged candidate index blobs.

    Taking the comparison from the frozen index (captured before tests run)
    means candidate code cannot restore the weakened test files at import
    time to hide its changes.
    """
    if not task.forbid_test_changes:
        return {
            "name": "test_integrity",
            "passed": True,
            "detail": "test changes are allowed by task policy",
        }
    relevant = {
        path
        for path in set(base_blobs) | set(candidate_blobs)
        if is_test_file(path) or is_test_harness_file(path)
    }
    changed = sorted(
        path for path in relevant if base_blobs.get(path) != candidate_blobs.get(path)
    )
    harness = [path for path in changed if is_test_harness_file(path)]
    tests = [path for path in changed if path not in set(harness)]
    problems = []
    if tests:
        problems.append("test files changed: " + ", ".join(tests))
    if harness:
        problems.append("test harness files changed: " + ", ".join(harness))
    ok = not problems
    detail = (
        "test files and test harness unchanged (base vs staged candidate blobs)"
        if ok
        else "; ".join(problems)
    )
    return {"name": "test_integrity", "passed": ok, "detail": detail}


def check_scope(frozen, task):
    if not task.allowed_paths:
        return {"name": "scope", "passed": True, "detail": "no allowed_paths configured"}
    files = frozen["changed_files"]
    bad = [
        f
        for f in files
        if not any(f == p or f.startswith(p.rstrip("/") + "/") for p in task.allowed_paths)
    ]
    deleted = [
        entry["path"]
        for entry in frozen["entries"]
        if entry["status"].startswith("D")
    ]
    renamed = [
        f"{entry['old']} -> {entry['new']}"
        for entry in frozen["entries"]
        if "old" in entry
    ]
    ok = not bad
    parts = []
    if ok:
        parts.append("all changes inside allowed paths")
    else:
        parts.append("out-of-scope changes: " + ", ".join(bad))
    if deleted:
        parts.append("deleted paths: " + ", ".join(deleted))
    if renamed:
        parts.append("renamed paths: " + ", ".join(renamed))
    return {"name": "scope", "passed": ok, "detail": "; ".join(parts)}


def check_noop(frozen):
    source_changes = [f for f in frozen["changed_files"] if not is_test_file(f)]
    ok = bool(source_changes)
    detail = (
        "source files changed: " + ", ".join(source_changes)
        if ok
        else "no source changes (test-only or empty patch)"
    )
    return {"name": "noop", "passed": ok, "detail": detail}


def _numeric_literals(text):
    return re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", text)


def check_hardcode(frozen):
    test_literals = frozen.get("test_literals") or set()
    matches = []
    total = 0
    max_per_line = 0
    for rel, lines in (frozen.get("added_lines") or {}).items():
        if is_test_file(rel) or not rel.endswith(".py"):
            continue
        for line in lines:
            found = [n for n in _numeric_literals(line) if n in test_literals]
            if found:
                total += len(found)
                max_per_line = max(max_per_line, len(found))
                if len(found) >= 2:
                    matches.append(f"{rel}: {line.strip()} -> {found}")
    # Legitimate fixes often add a single boundary constant (100, 2, 0.0).
    # A hardcoded lookup table concentrates several test literals on one line
    # and repeats them across the patch.
    ok = max_per_line < 3 and total < 5
    detail = (
        "no suspicious literal reuse found"
        if ok
        else "suspicious literal reuse (single line >=3 or total >=5 test literals): "
        + " | ".join(matches[:5])
    )
    return {"name": "hardcode_suspicion", "passed": ok, "detail": detail}


def check_runtime_tamper(frozen):
    """Heuristic: added source lines that can meddle with the test runtime.

    This raises the bar for in-process junit/coverage forging; it does not
    make in-process forgery impossible (see docs residual-risk notes).
    """
    hits = []
    for rel, lines in (frozen.get("added_lines") or {}).items():
        if is_test_file(rel) or not rel.endswith(".py"):
            continue
        for line in lines:
            lowered = line.lower()
            for needle, label in _TAMPER_PATTERNS:
                if needle in lowered:
                    hits.append(f"{rel}: {label} :: {line.strip()[:120]}")
                    break
            else:
                if _PYTEST_HOOK_RE.match(line):
                    hits.append(f"{rel}: pytest hook :: {line.strip()[:120]}")
    ok = not hits
    detail = (
        "no runtime-tamper patterns in added source lines"
        if ok
        else "suspicious runtime tampering in added source lines: " + " | ".join(hits[:6])
    )
    return {"name": "runtime_tamper_suspicion", "passed": ok, "detail": detail}


def check_candidate_tree_frozen(frozen_tree, workdir):
    """The staged tree and tracked worktree must still match the frozen view."""
    current_tree = write_tree(workdir)
    dirty = tracked_worktree_changes(workdir)
    problems = []
    if current_tree != frozen_tree:
        problems.append(
            f"staged tree changed during tests: {frozen_tree} -> {current_tree}"
        )
    if dirty:
        problems.append(
            "tracked worktree modified during tests: " + ", ".join(dirty[:10])
        )
    ok = not problems
    detail = (
        "candidate tree and tracked worktree unchanged by tests"
        if ok
        else "; ".join(problems)
    )
    return {"name": "candidate_tree_frozen", "passed": ok, "detail": detail}


def check_runtime_file_injection(reports):
    """Candidate code must not plant executable/config files while tests run.

    A file written during the visible run (conftest.py, sitecustomize.py,
    pytest.ini, .pth, a shared library, ...) would be loaded by the later
    hidden/mutation/coverage pytest invocations, letting the candidate tamper
    with suites it never saw.  Quarantine removes such files; this check
    records that the attempt happened and fails the verdict.
    """
    suspicious = []
    removed = []
    for report in reports or []:
        suspicious.extend(report.get("suspicious") or [])
        removed.extend(report.get("removed") or [])
    ok = not suspicious
    if ok:
        detail = "no executable or config files appeared in the worktree during test runs"
    else:
        detail = (
            "runtime-written files that later pytest runs could import or obey: "
            + ", ".join(sorted(set(suspicious))[:8])
        )
    return {"name": "runtime_file_injection", "passed": ok, "detail": detail}
