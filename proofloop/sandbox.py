from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

IMAGE = "proofloop-sandbox:latest"
SHELL_META = set(";&|><`$")

# Launchers are used with `python -P -c` so the interpreter starts without
# /work (the repo checkout) on sys.path.  Real pytest/coverage are imported
# from site-packages first; only afterwards are the repo paths inserted for
# the tests themselves.  This defeats shadowing via a repo-root pytest.py,
# coverage.py, sitecustomize.py, ...
#
# os._exit() after flushing is deliberate: it skips atexit handlers, so a
# candidate cannot rewrite the junit report from an atexit hook after pytest
# finished.
PYTEST_LAUNCHER = (
    "import os\n"
    "import sys\n"
    "import pytest\n"
    'sys.path[:0] = ["/work", "/work/src", "/work/tests"]\n'
    "_plugins = []\n"
    "if os.path.exists('/opt/proofloop_attest.py'):\n"
    "    import importlib.util\n"
    "    _spec = importlib.util.spec_from_file_location('proofloop_attest', '/opt/proofloop_attest.py')\n"
    "    _attest = importlib.util.module_from_spec(_spec)\n"
    "    _spec.loader.exec_module(_attest)\n"
    "    _plugins.append(_attest)\n"
    "rc = pytest.main(sys.argv[1:], plugins=_plugins)\n"
    "sys.stdout.flush()\n"
    "sys.stderr.flush()\n"
    "os._exit(rc)\n"
)

COVERAGE_LAUNCHER = (
    "import os\n"
    "import sys\n"
    "import coverage\n"
    "import pytest\n"
    'sys.path[:0] = ["/work", "/work/src", "/work/tests"]\n'
    "cov = coverage.Coverage(source=[sys.argv[1]])\n"
    "cov.start()\n"
    "try:\n"
    "    rc = pytest.main(sys.argv[2:])\n"
    "finally:\n"
    "    cov.stop()\n"
    "    cov.save()\n"
    "sys.stdout.flush()\n"
    "sys.stderr.flush()\n"
    "os._exit(rc)\n"
)


def _is_python(token: str) -> bool:
    return token in ("python", "python3")


def _mount_source(path: Path) -> str:
    """Host path for a bind mount.

    A Linux daemon running inside WSL (PROOFLOOP_WSL_MOUNTS=1) needs the
    /mnt/<drive>/... form instead of a Windows drive path.
    """
    raw = path.as_posix()
    if os.environ.get("PROOFLOOP_WSL_MOUNTS") == "1" and len(raw) > 2 and raw[1] == ":":
        return f"/mnt/{raw[0].lower()}{raw[2:]}"
    return raw


def transform_command(args: list[str]) -> list[str]:
    """Rewrite pytest/coverage invocations to shadow-proof launchers.

    The transformation must run AFTER host paths have been mapped to
    /work and /hidden.  Any other command is returned unchanged.
    """
    a = list(args)
    if not a:
        return a

    # python -m pytest <args>  ->  python -P -c LAUNCHER <args>
    if _is_python(a[0]) and a[1:3] == ["-m", "pytest"]:
        return [a[0], "-P", "-c", PYTEST_LAUNCHER] + a[3:]

    # pytest <args>  ->  python -P -c LAUNCHER <args>
    if a[0] == "pytest":
        return ["python", "-P", "-c", PYTEST_LAUNCHER] + a[1:]

    if _is_python(a[0]) and a[1:3] == ["-m", "coverage"] and len(a) > 3:
        # python -m coverage json ...  ->  python -P -m coverage json ...
        if a[3] == "json":
            return [a[0], "-P"] + a[1:]

        # python -m coverage run --source=<src> -m pytest <args>
        #   ->  python -P -c LAUNCHER <src> <args>
        if a[3] == "run":
            rest = a[4:]
            source = None
            i = 0
            while i < len(rest):
                token = rest[i]
                if token.startswith("--source="):
                    source = token.split("=", 1)[1]
                    i += 1
                elif token == "--source" and i + 1 < len(rest):
                    source = rest[i + 1]
                    i += 2
                elif token == "-m" and i + 1 < len(rest) and rest[i + 1] == "pytest":
                    i += 2
                    break
                else:
                    # Unknown coverage options: leave the command untouched.
                    return a
            else:
                return a
            if source is None:
                return a
            return [a[0], "-P", "-c", COVERAGE_LAUNCHER, source] + rest[i:]

    return a


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    p = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    return p.returncode == 0


def use_docker(mode: str) -> bool:
    if mode == "none":
        return False
    if mode == "docker":
        if not docker_ready():
            raise RuntimeError(
                f"Docker sandbox requested but image {IMAGE} is not available. "
                "Run: docker build -t proofloop-sandbox:latest docker"
            )
        return True
    if not docker_ready():
        raise RuntimeError(
            "Docker sandbox is unavailable; refusing to run agent-supplied code on the host. "
            "Build the image or pass --sandbox none only for trusted local runs."
        )
    return True


def image_id():
    """Docker image id used by the sandbox, or None when unavailable."""
    if not shutil.which("docker"):
        return None
    try:
        p = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = p.stdout.strip()
    return out if p.returncode == 0 and out else None


def image_tool_versions():
    """pytest/coverage versions inside the sandbox image (None when unavailable)."""
    versions = {"pytest": None, "coverage": None}
    if not shutil.which("docker"):
        return versions
    code = (
        "import importlib.metadata as m\n"
        "print(m.version('pytest'))\n"
        "print(m.version('coverage'))\n"
    )
    name = f"proofloop-probe-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        "proofloop=1",
        "--network",
        "none",
        IMAGE,
        "python",
        "-c",
        code,
    ]
    try:
        p = _run_probe_container(cmd, name, 120)
    except subprocess.TimeoutExpired:
        _remove_container(name)
        return versions
    except (OSError, subprocess.SubprocessError):
        return versions
    if p.returncode != 0:
        return versions
    lines = [line.strip() for line in p.stdout.splitlines() if line.strip()]
    if lines:
        versions["pytest"] = lines[0]
    if len(lines) > 1:
        versions["coverage"] = lines[1]
    return versions


def validate_command(args: list[str]) -> None:
    for token in args:
        bad = [ch for ch in token if ch in SHELL_META]
        if bad:
            raise ValueError(f"unsafe shell metacharacters {bad!r} in argument: {token!r}")


def _remove_container(name):
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class _DetachedResult:
    def __init__(self, returncode, stdout, stderr, command_text):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.command_text = command_text


def _run_detached(cmd, name, timeout):
    """Run a container detached and reconstruct its streams.

    Some remote/WSL daemons cannot stream an attached container's stdout back
    to a Windows client; ``run -d`` + ``wait`` + ``logs`` still works.
    ``--rm`` must be dropped so the container survives long enough to be polled.
    """
    run_cmd = [token for token in cmd if token != "--rm"]
    run_cmd.insert(run_cmd.index("run") + 1, "-d")
    p = subprocess.run(
        run_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )
    command_text = " ".join(run_cmd)
    if p.returncode != 0:
        return _DetachedResult(p.returncode, p.stdout or "", p.stderr or "", command_text)
    try:
        wait = subprocess.run(
            ["docker", "wait", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        try:
            code = int((wait.stdout or "").strip() or "1")
        except ValueError:
            code = 1
    except subprocess.TimeoutExpired:
        _remove_container(name)
        # Re-raise against the run command (not `docker wait`): callers parse
        # the container name out of exc.cmd to verify no container leaked.
        raise subprocess.TimeoutExpired(run_cmd, timeout)
    logs = subprocess.run(
        ["docker", "logs", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    stdout = (logs.stdout or "") + (logs.stderr or "")
    _remove_container(name)
    return _DetachedResult(code, stdout, "", command_text)


def _run_probe_container(cmd, name, timeout):
    """Run a one-shot probe container, honouring PROOFLOOP_DOCKER_DETACH.

    The attached form cannot stream stdout back to a Windows client against a
    WSL2 daemon; ``run -d`` + ``wait`` + ``logs`` still works there, so the
    probe must take the same branch as :func:`run_docker`.  Failures are left
    to the caller: this helper only dispatches.
    """
    if os.environ.get("PROOFLOOP_DOCKER_DETACH") == "1":
        return _run_detached(cmd, name, timeout)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def run_docker(
    workdir,
    args,
    junit_name,
    hidden_dir=None,
    timeout=300,
    with_junit=True,
    attest_name=None,
    attest_key=None,
):
    workdir = Path(workdir).resolve()
    validate_command(args)
    name = f"proofloop-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        "proofloop=1",
        "--network",
        "none",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    runtime = os.environ.get("PROOFLOOP_DOCKER_RUNTIME")
    if runtime:
        cmd += ["--runtime", runtime]
    if os.name != "nt":
        # --cap-drop ALL removes CAP_DAC_OVERRIDE, so container root cannot
        # write into a host workspace owned by another UID.  Run as the host
        # UID/GID on POSIX so JUnit reports and caches land in the mount.
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
    cmd += [
        "-v",
        f"{_mount_source(workdir)}:/work",
        "-w",
        "/work",
        "-e",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "-v",
        f"{_mount_source(Path(__file__).resolve().parent / '_sandbox_attest.py')}:/opt/proofloop_attest.py:ro",
    ]
    allow_plugins = os.environ.get("PROOFLOOP_ALLOW_PYTEST_PLUGINS")
    if allow_plugins:
        cmd += ["-e", f"PYTEST_PLUGINS={allow_plugins}"]
    if attest_name and attest_key:
        cmd += [
            "-e",
            f"PROOFLOOP_ATTEST_PATH=/work/{attest_name}",
            "-e",
            f"PROOFLOOP_ATTEST_KEY={attest_key}",
        ]
    hidden_path = None
    hidden_forms = set()
    if hidden_dir:
        hidden_path = Path(hidden_dir).resolve()
        hidden_forms = {hidden_path.as_posix(), str(hidden_path)}
        cmd += ["-v", f"{_mount_source(hidden_path)}:/hidden:ro"]
    work_forms = {workdir.as_posix(), str(workdir)}
    mapped = []
    for arg in args:
        for form in hidden_forms:
            arg = arg.replace(form, "/hidden")
        for form in work_forms:
            arg = arg.replace(form, "/work")
        mapped.append(arg)
    mapped = transform_command(mapped)
    cmd += [IMAGE] + mapped
    if with_junit:
        cmd.append(f"--junitxml=/work/{junit_name}")
    try:
        if os.environ.get("PROOFLOOP_DOCKER_DETACH") == "1":
            p = _run_detached(cmd, name, timeout)
        else:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        # Killing the docker client does not stop the container; remove it
        # explicitly so timed-out runs never leak containers.
        _remove_container(name)
        raise
    # Record the argv that actually ran (after path mapping and launcher
    # rewriting) so evidence can show it.
    p.command_text = " ".join(cmd)
    return p
