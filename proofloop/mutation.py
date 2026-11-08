from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import sandbox
from .runner import pytest_args_from_command, pytest_args_with_hidden

MUTABLE_OPS = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}


class _Mutator(ast.NodeTransformer):
    def __init__(self, target: int):
        self.target = target
        self.index = -1
        self.hit = False

    def _next(self) -> bool:
        self.index += 1
        return self.index == self.target

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if self._next():
                node.value = node.value + 1 if node.value != 0 else 1
                self.hit = True
        return node

    def visit_BinOp(self, node):
        if type(node.op) in MUTABLE_OPS and self._next():
            node.op = MUTABLE_OPS[type(node.op)]()
            self.hit = True
        return self.generic_visit(node)

    def visit_Compare(self, node):
        for i, op in enumerate(node.ops):
            if type(op) in MUTABLE_OPS and self._next():
                node.ops[i] = MUTABLE_OPS[type(op)]()
                self.hit = True
        return self.generic_visit(node)


def _count_mutations(tree) -> int:
    counter = _Mutator(target=-1)
    counter.visit(tree)
    return counter.index + 1


def _mutated_source(source: str, index: int):
    tree = ast.parse(source)
    mutator = _Mutator(target=index)
    tree = mutator.visit(tree)
    if not mutator.hit:
        return None
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _iter_source_files(workdir: Path, task, focus_files=None):
    allowed = task.allowed_paths or ["."]
    excluded_dirs = {".git", "__pycache__", ".pytest_cache", "tests", "test"}

    def _inside_allowed(rel):
        return any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in allowed)

    if focus_files is not None:
        # Mutate only the files the candidate actually changed, in the order
        # they were supplied (still inside allowed_paths, still .py only).
        seen = set()
        for rel in focus_files:
            rel = str(rel).replace("\\", "/")
            if rel in seen or not rel.endswith(".py"):
                continue
            parts = rel.split("/")
            if any(part in excluded_dirs for part in parts[:-1]):
                continue
            if not _inside_allowed(rel):
                continue
            path = workdir / rel
            if not path.is_file():
                continue
            seen.add(rel)
            yield rel, path
        return

    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            rel = path.relative_to(workdir).as_posix()
            if _inside_allowed(rel):
                yield rel, path


def _pytest_available() -> bool:
    p = subprocess.run(
        [sys.executable, "-c", "import pytest"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return p.returncode == 0


def _rmtree(path):
    def onerror(func, p, excinfo):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def run_mutation(
    workdir,
    task,
    out_dir,
    timeout=60,
    sandbox_mode="auto",
    focus_files=None,
    hidden_dir=None,
):
    workdir = Path(workdir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = "changed_files" if focus_files is not None else "allowed_paths"
    use_docker = sandbox.use_docker(sandbox_mode)
    # Run the task's own acceptance tests plus the hidden suite (when
    # configured), so hidden tests participate in the kill oracle.
    pytest_args = pytest_args_from_command(task.test_command)
    hidden_path = Path(hidden_dir).resolve() if hidden_dir is not None else None
    if hidden_path is not None:
        pytest_args = pytest_args_with_hidden(
            pytest_args, "/hidden" if use_docker else str(hidden_path)
        )
    if not use_docker and not _pytest_available():
        return {
            "total": 0,
            "killed": 0,
            "survived": 0,
            "score": 1.0,
            "survivors": [],
            "skipped": True,
            "reason": "pytest not available in the current interpreter",
            "scope": scope,
            "files": [],
            "command": " ".join(pytest_args),
        }

    mutants = []
    for rel, path in _iter_source_files(workdir, task, focus_files):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        count = _count_mutations(tree)
        for index in range(count):
            if len(mutants) >= task.max_mutants:
                break
            mutants.append((rel, source, index))
        if len(mutants) >= task.max_mutants:
            break

    killed = 0
    survived = 0
    survivors = []
    exec_command = None
    for n, (rel, source, index) in enumerate(mutants, 1):
        mutated = _mutated_source(source, index)
        if mutated is None:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="pl_mut_", dir=str(out_dir)))
        repo = tmp / "repo"
        try:
            shutil.copytree(
                workdir,
                repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc"),
            )
            (repo / rel).write_text(mutated, encoding="utf-8")
            if use_docker:
                p = sandbox.run_docker(
                    repo,
                    pytest_args,
                    "mut.xml",
                    hidden_dir=hidden_path,
                    timeout=timeout,
                    with_junit=False,
                )
                if exec_command is None:
                    exec_command = getattr(p, "command_text", None)
            else:
                env = os.environ.copy()
                env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
                host_cmd = [sys.executable, "-m"] + pytest_args
                if exec_command is None:
                    exec_command = " ".join(host_cmd)
                p = subprocess.run(
                    host_cmd,
                    cwd=str(repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
            if p.returncode != 0:
                killed += 1
            else:
                survived += 1
                survivors.append(f"{rel}#{index}")
        except subprocess.TimeoutExpired:
            killed += 1
        finally:
            _rmtree(tmp)

    total = killed + survived
    score = (killed / total) if total else 1.0
    files = list(dict.fromkeys(rel for rel, _, _ in mutants))
    return {
        "total": total,
        "killed": killed,
        "survived": survived,
        "score": round(score, 4),
        "survivors": survivors,
        "skipped": False,
        "reason": "",
        "sandbox": "docker" if use_docker else "subprocess",
        "scope": scope,
        "files": files,
        "command": exec_command or " ".join(pytest_args),
    }
