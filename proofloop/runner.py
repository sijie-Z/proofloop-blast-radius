import hashlib
import hmac
import json
import os
import secrets
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import sandbox
from .models import TestStats


def _junit_int(suite, attr):
    raw = suite.get(attr)
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"non-integer junit attribute {attr}={raw!r}")


def parse_junit(path):
    stats = TestStats()
    path = Path(path)
    if not path.exists():
        stats.parse_error = "junit file missing"
        return stats
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        stats.parse_error = f"invalid junit xml: {exc}"
        return stats
    # Only the root suite or the direct children of <testsuites> count.
    # Nested <testsuite> elements must not be counted twice.
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = root.findall("testsuite")
    else:
        stats.parse_error = f"unexpected junit root element <{root.tag}>"
        return stats
    try:
        for suite in suites:
            stats.total += _junit_int(suite, "tests")
            stats.failed += _junit_int(suite, "failures")
            stats.errors += _junit_int(suite, "errors")
            stats.skipped += _junit_int(suite, "skipped")
            for case in suite.findall("testcase"):
                if case.find("failure") is not None:
                    status = "failed"
                elif case.find("error") is not None:
                    status = "error"
                elif case.find("skipped") is not None:
                    status = "skipped"
                else:
                    status = "passed"
                stats.cases.append(
                    {
                        "classname": case.get("classname", "") or "",
                        "name": case.get("name", "") or "",
                        "status": status,
                    }
                )
    except ValueError as exc:
        stats.parse_error = str(exc)
        return stats
    stats.passed = max(0, stats.total - stats.failed - stats.errors - stats.skipped)
    return stats


def _env_for(workdir):
    env = os.environ.copy()
    paths = [str(workdir), str(Path(workdir) / "src"), str(Path(workdir) / "tests")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    # Host-mode attestation plugin lives in the proofloop package itself.
    package_root = str(Path(__file__).resolve().parent.parent)
    if package_root not in paths:
        paths.append(package_root)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    # Candidate repositories must not be able to inject pytest plugins via
    # entry points or packaging metadata.  Trusted deployments can still
    # allow specific plugin modules with PROOFLOOP_ALLOW_PYTEST_PLUGINS.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    allow_plugins = os.environ.get("PROOFLOOP_ALLOW_PYTEST_PLUGINS")
    if allow_plugins:
        env["PYTEST_PLUGINS"] = allow_plugins
    return env


def _verify_attestation(path, key, stats):
    """Cross-check the signed pytest result summary against the junit stats."""
    path = Path(path)
    if not path.is_file():
        return "attestation missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"attestation unreadable: {exc}"
    body = payload.get("body")
    signature = payload.get("sig") or ""
    if not isinstance(body, dict):
        return "attestation malformed"
    payload_text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(
        key.encode(), payload_text.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return "attestation signature mismatch"
    try:
        collected = int(body.get("collected", -1))
    except (TypeError, ValueError):
        return "attestation collected count invalid"
    if collected != stats.total:
        return f"attestation collected {collected} != junit total {stats.total}"
    return ""


_PYTEST_VALUE_OPTIONS = {
    "-k",
    "-m",
    "-p",
    "-o",
    "-c",
    "-W",
    "--maxfail",
    "--tb",
    "--rootdir",
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "--confcutdir",
    "--junitxml",
    "--junit-prefix",
    "--basetemp",
    "--timeout",
    "--import-mode",
    "--capture",
    "--log-level",
    "--color",
    "--durations",
    "--durations-min",
    "--code-highlight",
    "--verbosity",
    "--cov",
    "--cov-report",
}


def pytest_args_with_hidden(pytest_args, hidden_arg):
    """Return pytest args that run the task's visible tests AND hidden tests.

    pytest only honors testpaths when no positional path is given, so simply
    appending the hidden dir would silently drop the visible suite for
    commands like `pytest -q`.  When the task command has no explicit
    selection, the repo root ('.') keeps default collection and the hidden
    directory is added next to it.
    """
    args = list(pytest_args)
    has_selection = False
    skip_next = False
    for token in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in _PYTEST_VALUE_OPTIONS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        has_selection = True
        break
    if has_selection:
        return args + [hidden_arg]
    return args + [".", hidden_arg]


def pytest_args_from_command(command):
    """Return the ['pytest', ...] token list of a trusted pytest test_command."""
    tokens = shlex.split(command, posix=(os.name != "nt"))
    if not tokens:
        raise ValueError("empty test command")
    if tokens[0] in ("python", "python3"):
        tokens = tokens[1:]
    if tokens and tokens[0] == "-m":
        tokens = tokens[1:]
    if not tokens or tokens[0] != "pytest":
        raise ValueError(f"test_command is not a pytest command: {command!r}")
    return tokens


def run_tests(workdir, command, junit_path, timeout=300, sandbox_mode="auto", hidden_dir=None):
    workdir = Path(workdir).resolve()
    junit_path = Path(junit_path).resolve()
    cmd = shlex.split(command, posix=(os.name != "nt"))
    is_pytest = "pytest" in " ".join(cmd)
    used_docker = sandbox.use_docker(sandbox_mode)
    if used_docker:
        actual_junit = workdir / junit_path.name
    else:
        actual_junit = junit_path if is_pytest else None
        if is_pytest:
            cmd.append(f"--junitxml={junit_path}")
    # Freshness: remove any pre-existing report and require the file to be
    # (re)written by this run.
    if actual_junit is not None:
        try:
            if actual_junit.exists():
                actual_junit.unlink()
        except OSError:
            pass
    started = time.time()
    attest_key = secrets.token_hex(16) if is_pytest else None
    attest_name = None
    attest_path = None
    if is_pytest:
        if used_docker:
            attest_name = junit_path.name + ".attest.json"
            attest_path = workdir / attest_name
        else:
            attest_path = junit_path.with_suffix(".attest.json")
            cmd += ["-p", "proofloop._sandbox_attest"]
        try:
            if attest_path.exists():
                attest_path.unlink()
        except OSError:
            pass
    if used_docker:
        p = sandbox.run_docker(
            workdir,
            cmd,
            junit_path.name,
            hidden_dir=hidden_dir,
            timeout=timeout,
            with_junit=is_pytest,
            attest_name=attest_name,
            attest_key=attest_key,
        )
    else:
        env = _env_for(workdir)
        if is_pytest and attest_path is not None:
            env["PROOFLOOP_ATTEST_PATH"] = str(attest_path)
            env["PROOFLOOP_ATTEST_KEY"] = attest_key
        p = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    if actual_junit is not None:
        stats = parse_junit(actual_junit)
        try:
            fresh = (
                actual_junit.is_file()
                and actual_junit.stat().st_mtime >= started - 2.0
            )
        except OSError:
            fresh = False
        if not fresh and not stats.parse_error:
            stats.parse_error = "junit missing or not freshly written by this run"
        if is_pytest and attest_path is not None and not stats.parse_error:
            error = _verify_attestation(attest_path, attest_key, stats)
            if error:
                stats.parse_error = error
    else:
        stats = TestStats()
    command_text = getattr(p, "command_text", None) or " ".join(cmd)
    return {
        "command": command_text,
        "exit_code": p.returncode,
        "stdout": p.stdout[-12000:],
        "stderr": p.stderr[-12000:],
        "stats": stats,
        "sandbox": "docker" if used_docker else "subprocess",
    }


def collect_nodeids(workdir, hidden_dir, timeout=300, sandbox_mode="auto"):
    """Collect the hidden-test nodeids on a pristine checkout (base, no patch)."""
    workdir = Path(workdir).resolve()
    hidden_dir = Path(hidden_dir).resolve()
    cmd = ["python", "-m", "pytest", "--collect-only", "-q", str(hidden_dir)]
    used_docker = sandbox.use_docker(sandbox_mode)
    if used_docker:
        p = sandbox.run_docker(
            workdir,
            cmd,
            "collect.xml",
            hidden_dir=hidden_dir,
            timeout=timeout,
            with_junit=False,
        )
        command_text = getattr(p, "command_text", None) or " ".join(cmd)
    else:
        env = _env_for(workdir)
        p = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        command_text = " ".join(cmd)
    nodeids = []
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            line = line.strip()
            if line.startswith("=") or "test collected" in line or "tests collected" in line:
                break
            if "::" in line and not line.startswith(("FAILED", "ERROR")):
                nodeids.append(line)
    return {
        "command": command_text,
        "exit_code": p.returncode,
        "nodeids": nodeids,
        "stdout": p.stdout[-6000:],
        "stderr": p.stderr[-6000:],
        "sandbox": "docker" if used_docker else "subprocess",
    }


def run_coverage(workdir, task, timeout=300, sandbox_mode="auto", hidden_dir=None):
    workdir = Path(workdir).resolve()
    source = task.coverage_source or "."
    report = workdir / "coverage.json"
    if report.exists():
        report.unlink()
    used_docker = sandbox.use_docker(sandbox_mode)
    # Measure coverage over the acceptance tests plus the hidden suite.
    pytest_args = pytest_args_from_command(task.test_command)
    hidden_path = None
    if hidden_dir is not None:
        hidden_path = Path(hidden_dir).resolve()
        pytest_args = pytest_args_with_hidden(
            pytest_args, "/hidden" if used_docker else str(hidden_path)
        )
    run_cmd = [
        "python",
        "-m",
        "coverage",
        "run",
        f"--source={source}",
        "-m",
    ] + pytest_args
    json_cmd = ["python", "-m", "coverage", "json", "-o", "coverage.json"]
    if used_docker:
        p1 = sandbox.run_docker(
            workdir,
            run_cmd,
            "coverage.xml",
            hidden_dir=hidden_path,
            timeout=timeout,
            with_junit=False,
        )
        p2 = sandbox.run_docker(
            workdir, json_cmd, "coverage.xml", timeout=timeout, with_junit=False
        )
    else:
        env = _env_for(workdir)
        p1 = subprocess.run(
            run_cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        p2 = subprocess.run(
            json_cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    if not report.is_file():
        return {
            "percent": 0.0,
            "source": source,
            "skipped": True,
            "reason": f"coverage.json not produced (run={p1.returncode}, json={p2.returncode})",
            "sandbox": "docker" if used_docker else "subprocess",
            "command": getattr(p1, "command_text", None) or " ".join(run_cmd),
        }
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        percent = float(payload.get("totals", {}).get("percent_covered", 0.0))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "percent": 0.0,
            "source": source,
            "skipped": True,
            "reason": f"coverage.json parse failed: {exc}",
            "sandbox": "docker" if used_docker else "subprocess",
            "command": getattr(p1, "command_text", None) or " ".join(run_cmd),
        }
    return {
        "percent": round(percent, 2),
        "source": source,
        "skipped": False,
        "reason": "",
        "run_exit": p1.returncode,
        "json_exit": p2.returncode,
        "sandbox": "docker" if used_docker else "subprocess",
        "command": getattr(p1, "command_text", None) or " ".join(run_cmd),
    }
