import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", "runs", "*.pyc", ".venv", "venv"
)


def _rmtree(path):
    def onerror(func, p, excinfo):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def _run(cmd, cwd, check=True, timeout=120):
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} failed ({p.returncode})\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_no_links(src):
    """Refuse to copy symlinks or Windows reparse points (junctions).

    Otherwise a repository tree could smuggle host directories into
    ProofLoop/runs (and from there into the Docker mount).
    """
    src = Path(src)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    for root, dirs, files in os.walk(src, followlinks=False):
        for name in dirs + files:
            path = Path(root) / name
            try:
                st = os.lstat(path)
            except OSError as exc:
                raise RuntimeError(f"cannot stat {path}: {exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                raise RuntimeError(f"refusing to copy symbolic link: {path}")
            if reparse_flag and (getattr(st, "st_file_attributes", 0) & reparse_flag):
                raise RuntimeError(f"refusing to copy reparse point/junction: {path}")


def copy_repo(src, dst):
    if dst.exists():
        _rmtree(dst)
    _assert_no_links(src)
    shutil.copytree(src, dst, ignore=IGNORE)


def git_init_commit(workdir):
    _run(["git", "init"], workdir)
    _run(["git", "add", "-A"], workdir)
    _run(
        [
            "git",
            "-c",
            "user.email=proofloop@local",
            "-c",
            "user.name=ProofLoop",
            "commit",
            "-m",
            "base",
        ],
        workdir,
    )
    return _run(["git", "rev-parse", "HEAD"], workdir).stdout.strip()


def prepare_base(repo, out_dir):
    base = Path(out_dir) / "base"
    copy_repo(Path(repo), base)
    commit = git_init_commit(base)
    return base, commit


def prepare_candidate(base, out_dir, patch_path):
    cand = Path(out_dir) / "candidate"
    if cand.exists():
        _rmtree(cand)
    shutil.copytree(
        base,
        cand,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    p = Path(patch_path).resolve()
    if p.stat().st_size == 0:
        # An empty diff is a valid "no change" candidate; let the rules judge it.
        return cand
    # --index stages the applied changes (including newly created files) so
    # that `git diff HEAD` sees everything, even paths matched by .gitignore.
    # The copied .git/index still carries the base checkout's stat data, which
    # makes `git apply --index` intermittently fail with "does not match
    # index" on Windows; refresh the stat cache first (content is unchanged).
    _run(["git", "update-index", "--refresh"], cand, check=False)
    # --ignore-whitespace lets CRLF-context patches apply against the LF blobs
    # `git apply --index` works on (this machine's git has core.autocrlf=true,
    # and several benchmark patches were generated from CRLF checkouts).  It
    # only loosens context matching; the applied postimage is unchanged.
    result = _run(
        [
            "git",
            "apply",
            "-p1",
            "--index",
            "--whitespace=nowarn",
            "--ignore-whitespace",
            str(p),
        ],
        cand,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git apply failed for {patch_path}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return cand


def write_tree(workdir):
    """Return the tree object id of the staged candidate state (no commit)."""
    return _run(["git", "write-tree"], workdir).stdout.strip()


def changed_files(workdir):
    out = _run(["git", "diff", "HEAD", "--name-only"], workdir).stdout
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def diff_text(workdir):
    return _run(["git", "diff", "--text", "--binary", "HEAD"], workdir).stdout


def added_lines(workdir, rel_path):
    out = _run(
        ["git", "diff", "--text", "HEAD", "--unified=0", "--", rel_path],
        workdir,
    ).stdout
    lines = []
    for line in out.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return lines


def rev_parse_tree(workdir):
    return _run(["git", "rev-parse", "HEAD^{tree}"], workdir).stdout.strip()


def cached_changes(workdir):
    """Staged change entries (index vs HEAD) with rename detection."""
    out = _run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "diff",
            "--cached",
            "--name-status",
            "-M",
            "HEAD",
        ],
        workdir,
    ).stdout
    entries = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        if status[:1] in ("R", "C") and len(parts) >= 3:
            entries.append({"status": status, "old": parts[1], "new": parts[2]})
        elif len(parts) >= 2:
            entries.append({"status": status, "path": parts[1]})
    return entries


def changed_paths(entries):
    """All paths touched by change entries (both sides of renames/removals)."""
    paths = []
    for entry in entries:
        entry_paths = (
            [entry["old"], entry["new"]] if "old" in entry else [entry["path"]]
        )
        for path in entry_paths:
            if path not in paths:
                paths.append(path)
    return paths


def cached_diff_text(workdir):
    # --text defeats a candidate-provided `.gitattributes` with `-diff`,
    # which would otherwise hide added lines from this diff.
    return _run(
        ["git", "diff", "--cached", "--text", "HEAD"], workdir
    ).stdout


def cached_added_lines(workdir, paths):
    out = _run(
        ["git", "diff", "--cached", "--text", "--unified=0", "HEAD", "--"]
        + [str(path) for path in paths],
        workdir,
    ).stdout
    lines = []
    for line in out.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return lines


def freeze_view(workdir):
    """Snapshot the staged candidate exactly as the patch left it.

    This must be captured before any test runs: candidate code may restore
    weakened test files at import time, so post-run worktree reads would lie.
    """
    workdir = Path(workdir).resolve()
    entries = cached_changes(workdir)
    diff = cached_diff_text(workdir)
    added = {}
    for entry in entries:
        spec = [entry["old"], entry["new"]] if "old" in entry else [entry["path"]]
        key = entry.get("new") or entry.get("path")
        added[key] = cached_added_lines(workdir, spec)
    return {
        "tree": write_tree(workdir),
        "entries": entries,
        "changed_files": changed_paths(entries),
        "diff_text": diff,
        "diff_sha256": sha256_text(diff),
        "added_lines": added,
    }


def tree_blobs(workdir):
    """path -> blob hash for every file in HEAD (the base side)."""
    out = _run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "HEAD"], workdir
    ).stdout
    blobs = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            blobs[path] = parts[2]
    return blobs


def index_blobs(workdir):
    """path -> blob hash for every staged file in the candidate index."""
    out = _run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-s"], workdir
    ).stdout
    blobs = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 2:
            blobs[path] = parts[1]
    return blobs


def tracked_worktree_changes(workdir):
    """Tracked files modified in the worktree relative to the index."""
    out = _run(
        ["git", "-c", "core.quotepath=false", "diff", "--name-only"], workdir
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


# Run artifacts the harness itself writes into the worktree: junit reports,
# the attestation sidecar, coverage.json and interpreter caches.
_RUNTIME_ARTIFACT_SUFFIXES = (".xml", ".json", ".pyc")
_RUNTIME_ARTIFACT_DIRS = {"__pycache__", ".pytest_cache"}

# Files that pytest, the interpreter or the OS would later import or obey.
_RUNTIME_EXECUTABLE_SUFFIXES = (
    ".py",
    ".pyi",
    ".pyx",
    ".pth",
    ".so",
    ".dll",
    ".dylib",
    ".ini",
    ".cfg",
    ".toml",
    ".sh",
    ".bat",
    ".cmd",
    ".ps1",
)


def untracked_files(workdir):
    """Repo-relative files present in the worktree but absent from the index.

    Ignored files are included on purpose: .gitignore lives in the candidate
    diff, so a candidate could hide a planted conftest.py from a plain
    `git status` by ignoring it there.
    """
    workdir = Path(workdir).resolve()
    paths = set()
    for extra in (
        ["--others", "--exclude-standard"],
        ["--others", "--ignored", "--exclude-standard"],
    ):
        out = _run(
            ["git", "-c", "core.quotepath=false", "ls-files"] + extra, workdir
        ).stdout
        for line in out.splitlines():
            rel = line.strip()
            if rel:
                paths.add(rel)
    return paths


def _runtime_artifact(rel):
    if rel.endswith(_RUNTIME_ARTIFACT_SUFFIXES):
        return True
    return any(part in _RUNTIME_ARTIFACT_DIRS for part in rel.split("/")[:-1])


def quarantine_runtime_files(workdir, baseline=None):
    """Delete files created in the worktree after the frozen view.

    Candidate code runs in the sandbox before the hidden, mutation and
    coverage runs.  Anything it writes into the repository (an untracked
    conftest.py, sitecustomize.py, pytest.ini, .pth, ...) would be loaded by
    those later pytest invocations, so it is removed here.  Files present in
    the frozen baseline are kept, and run artifacts (junit/attestation xml,
    coverage json, caches) are allow-listed.
    """
    workdir = Path(workdir).resolve()
    current = untracked_files(workdir)
    created = sorted(current - set(baseline or ()))
    report = {"created": created, "removed": [], "failed": [], "suspicious": []}
    for rel in created:
        if _runtime_artifact(rel):
            continue
        if rel.lower().endswith(_RUNTIME_EXECUTABLE_SUFFIXES):
            report["suspicious"].append(rel)
        target = workdir / rel
        try:
            target.resolve().relative_to(workdir)
        except ValueError:
            report["failed"].append(rel)
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
                report["removed"].append(rel)
        except OSError:
            report["failed"].append(rel)
    return report
