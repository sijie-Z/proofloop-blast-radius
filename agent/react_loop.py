"""Minimal ReAct loop whose file and test tools all run inside Docker.

The agent never executes candidate code on the host: read_file, write_file and
run_tests are each one hardened container invocation (no network, 512 MB,
1 CPU, all capabilities dropped).  The ProofLoop verifier later re-runs the
same patch under its own sandbox and freezes the evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path, PurePosixPath

from proofloop import sandbox as pl_sandbox
from proofloop.sandbox import _mount_source, _remove_container, _run_detached

IMAGE = "proofloop-sandbox:latest"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file from the repository. "
                "Path is relative to the repository root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "relative path, e.g. app/discount.py",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write a UTF-8 text file inside the allowed source paths. "
                "Parent directories are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "relative path under allowed_paths"},
                    "content": {"type": "string", "description": "full new file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run the repository's pytest suite inside the sandbox and return "
                "the exit code and output."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_READ_CODE = (
    "import sys\n"
    "from pathlib import Path\n"
    "root = Path('/work').resolve()\n"
    "p = (root / sys.argv[1]).resolve()\n"
    "if root not in p.parents:\n"
    "    raise SystemExit('path escapes workspace')\n"
    "data = p.read_bytes()\n"
    "if len(data) > 65536:\n"
    "    raise SystemExit('file too large to read')\n"
    "sys.stdout.write(data.decode('utf-8', 'replace'))\n"
)

_WRITE_CODE = (
    "import shutil\n"
    "import sys\n"
    "from pathlib import Path\n"
    "root = Path('/work').resolve()\n"
    "p = (root / sys.argv[1]).resolve()\n"
    "if root not in p.parents:\n"
    "    raise SystemExit('path escapes workspace')\n"
    "p.parent.mkdir(parents=True, exist_ok=True)\n"
    "shutil.copyfile('/payload', str(p))\n"
    "print('wrote', p.stat().st_size, 'bytes to', sys.argv[1])\n"
)

_DENIED_WRITE_NAMES = {
    "conftest.py",
    "task.json",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "pyproject.toml",
    ".gitignore",
    ".gitattributes",
}


class SandboxedRepo:
    """File and test operations for one repository, executed in Docker."""

    def __init__(
        self,
        root,
        allowed_paths=None,
        scratch_dir=None,
        image: str = IMAGE,
        timeout: int = 180,
    ):
        self.root = Path(root).resolve()
        self.allowed = [
            str(item).replace("\\", "/").rstrip("/") or "."
            for item in (allowed_paths or ["."])
        ]
        self.scratch = Path(
            scratch_dir or (self.root.parent / ".agent-scratch")
        ).resolve()
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.image = image
        self.timeout = timeout

    def _relative(self, rel) -> str:
        rel = str(rel or "").replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        path = PurePosixPath(rel)
        if not rel or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"path must stay inside the repository: {rel!r}")
        return path.as_posix()

    def _inside_allowed(self, rel: str) -> bool:
        return any(
            rel == base or base == "." or rel.startswith(base + "/")
            for base in self.allowed
        )

    def _safe_read(self, rel) -> str:
        rel = self._relative(rel)
        if rel.split("/")[0] == ".git":
            raise ValueError("cannot read .git")
        return rel

    def _safe_write(self, rel) -> str:
        rel = self._relative(rel)
        if not self._inside_allowed(rel):
            raise ValueError(
                f"path is outside allowed_paths {self.allowed}: {rel}"
            )
        name = PurePosixPath(rel).name.lower()
        if (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name in _DENIED_WRITE_NAMES
        ):
            raise ValueError(f"the agent may not modify tests or harness files: {rel}")
        return rel

    def _run(self, args, extra_mounts=(), timeout=None):
        name = f"proofloop-agent-{uuid.uuid4().hex[:12]}"
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
            "-v",
            f"{_mount_source(self.root)}:/work",
            "-w",
            "/work",
            "-e",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        ]
        for host, container in extra_mounts:
            cmd += ["-v", f"{_mount_source(Path(host))}:{container}:ro"]
        cmd += [self.image] + list(args)
        try:
            if os.environ.get("PROOFLOOP_DOCKER_DETACH") == "1":
                result = _run_detached(cmd, name, timeout or self.timeout)
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdin=subprocess.DEVNULL,
                    timeout=timeout or self.timeout,
                )
        except subprocess.TimeoutExpired:
            _remove_container(name)
            raise
        result.command_text = " ".join(cmd)
        return result

    def read_file(self, rel) -> str:
        rel = self._safe_read(rel)
        result = self._run(["python", "-c", _READ_CODE, rel], timeout=60)
        if result.returncode != 0:
            return f"ERROR: {(result.stderr or result.stdout).strip()[:2000]}"
        text = result.stdout or ""
        if len(text) > 12000:
            text = text[:12000] + "\n... [truncated]"
        return text

    def write_file(self, rel, content) -> str:
        rel = self._safe_write(rel)
        payload = self.scratch / f"payload-{uuid.uuid4().hex}.txt"
        payload.write_text(str(content), encoding="utf-8", newline="\n")
        result = self._run(
            ["python", "-c", _WRITE_CODE, rel],
            extra_mounts=[(payload, "/payload")],
            timeout=60,
        )
        if result.returncode != 0:
            return f"ERROR: {(result.stderr or result.stdout).strip()[:2000]}"
        return (result.stdout or f"wrote {rel}").strip()

    def run_tests(self) -> dict:
        args = pl_sandbox.transform_command(["python", "-m", "pytest", "-q"])
        result = self._run(args, timeout=self.timeout)
        output = result.stdout or ""
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        return {"exit_code": result.returncode, "output": output[-8000:]}


def _parse_json_action(text: str):
    """Return the first JSON object embedded in the model output."""
    start = text.find("{")
    while start != -1:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def _text_of(content) -> str:
    """Normalize a message content field (string or list of parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


class ReactAgent:
    def __init__(self, client, repo: SandboxedRepo, task: dict, max_steps: int = 12, log_path=None):
        self.client = client
        self.repo = repo
        self.task = task or {}
        self.max_steps = max_steps
        self.log_path = Path(log_path) if log_path else None
        self.transcript = []
        self.tool_calls = 0

    def _system_prompt(self) -> str:
        allowed = ", ".join(self.task.get("allowed_paths") or ["."])
        return (
            "You are a coding agent working inside a hardened Docker sandbox "
            "(no network, 512 MB RAM, 1 CPU, all capabilities dropped).\n"
            "Your job is to make the repository's tests pass without weakening them.\n"
            "Use the tools read_file, write_file and run_tests; every tool operation "
            "executes inside the sandbox.\n"
            f"Only modify files under: {allowed}\n"
            "Never modify tests, conftest.py, task.json, or any test harness file. "
            "Prefer a general, correct implementation over special-casing test values.\n"
            "When the fix is complete, reply with DONE and a short summary."
        )

    def _user_prompt(self) -> str:
        acceptance = "\n".join(
            f"- {item}" for item in (self.task.get("acceptance") or [])
        )
        return (
            f"# Task: {self.task.get('name', 'unnamed')}\n\n"
            f"{self.task.get('description', '')}\n\n"
            f"Acceptance criteria:\n{acceptance or '- make the tests pass'}\n\n"
            "Start by reading the source files and the failing tests, then edit the "
            "source and run the tests. Reply DONE when you believe the acceptance "
            "tests and the hidden tests will pass."
        )

    def _record(self, kind: str, **fields):
        entry = {"kind": kind}
        entry.update(fields)
        self.transcript.append(entry)

    def _save(self):
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps(
                {
                    "tool_calls": self.tool_calls,
                    "steps": len(self.transcript),
                    "transcript": self.transcript,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _execute_tool(self, name: str, arguments) -> str:
        try:
            if isinstance(arguments, str):
                args = json.loads(arguments or "{}")
            else:
                args = arguments or {}
        except json.JSONDecodeError:
            return "ERROR: tool arguments are not valid JSON"
        if not isinstance(args, dict):
            return "ERROR: tool arguments must be a JSON object"
        self.tool_calls += 1
        try:
            if name == "read_file":
                return self.repo.read_file(args.get("path", ""))
            if name == "write_file":
                return self.repo.write_file(args.get("path", ""), args.get("content", ""))
            if name == "run_tests":
                result = self.repo.run_tests()
                return f"exit_code={result['exit_code']}\n{result['output']}"
            return f"ERROR: unknown tool {name!r}"
        except Exception as exc:  # tool errors are reported back to the model
            return f"ERROR: {type(exc).__name__}: {exc}"

    def run(self) -> dict:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_prompt()},
        ]
        try:
            return self._run_with_api_tools(messages)
        except Exception as exc:
            if self._tools_unsupported(exc):
                self._record("fallback", reason=str(exc)[:300])
                messages = [
                    {
                        "role": "system",
                        "content": self._system_prompt()
                        + "\nReply with exactly one JSON object per turn, no markdown:\n"
                        '{"tool": "read_file", "args": {"path": "app/discount.py"}}\n'
                        '{"tool": "write_file", "args": {"path": "...", "content": "..."}}\n'
                        '{"tool": "run_tests", "args": {}}\n'
                        '{"done": true, "message": "..."}',
                    },
                    {"role": "user", "content": self._user_prompt()},
                ]
                return self._run_json_protocol(messages)
            self._save()
            raise

    @staticmethod
    def _tools_unsupported(exc: Exception) -> bool:
        status = getattr(exc, "status", 0)
        message = str(exc).lower()
        return status in (400, 404, 422) and (
            "tool" in message or "function" in message
        )

    def _run_with_api_tools(self, messages) -> dict:
        for step in range(1, self.max_steps + 1):
            response = self.client.chat(messages, tools=TOOLS)
            choices = response.get("choices") or []
            if not choices:
                raise RuntimeError(f"no choices in response: {json.dumps(response)[:500]}")
            message = choices[0].get("message") or {}
            content = _text_of(message.get("content"))
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                messages.append(
                    {"role": "assistant", "content": content, "tool_calls": tool_calls}
                )
                for index, call in enumerate(tool_calls):
                    function = call.get("function") or {}
                    name = function.get("name", "")
                    result = self._execute_tool(name, function.get("arguments", "{}"))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"call_{step}_{index}",
                            "content": result,
                        }
                    )
                    self._record(
                        "tool", step=step, name=name,
                        arguments=function.get("arguments", "")[:1000],
                        result=result[:2000],
                    )
                continue
            messages.append({"role": "assistant", "content": content})
            self._record("assistant", step=step, content=content[:2000])
            if "DONE" in content.upper():
                self._save()
                return {
                    "protocol": "tools",
                    "steps": step,
                    "tool_calls": self.tool_calls,
                    "final_message": content,
                }
            if step == 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Use the tools to inspect and edit the code, then run "
                            "the tests. Reply DONE only when the fix is complete."
                        ),
                    }
                )
                continue
            self._save()
            return {
                "protocol": "tools",
                "steps": step,
                "tool_calls": self.tool_calls,
                "final_message": content,
            }
        self._save()
        return {
            "protocol": "tools",
            "steps": self.max_steps,
            "tool_calls": self.tool_calls,
            "final_message": "max steps reached",
        }

    def _run_json_protocol(self, messages) -> dict:
        for step in range(1, self.max_steps + 1):
            response = self.client.chat(messages)
            choices = response.get("choices") or []
            if not choices:
                raise RuntimeError(f"no choices in response: {json.dumps(response)[:500]}")
            message = choices[0].get("message") or {}
            content = _text_of(message.get("content"))
            messages.append({"role": "assistant", "content": content})
            self._record("assistant", step=step, content=content[:2000], protocol="json")
            action = _parse_json_action(content)
            if action is None:
                messages.append(
                    {"role": "user", "content": "Reply with one JSON object as instructed."}
                )
                continue
            if action.get("done"):
                self._save()
                return {
                    "protocol": "json",
                    "steps": step,
                    "tool_calls": self.tool_calls,
                    "final_message": str(action.get("message", "")),
                }
            name = action.get("tool", "")
            result = self._execute_tool(name, action.get("args", {}))
            messages.append({"role": "user", "content": f"Tool result:\n{result}"})
            self._record("tool", step=step, name=name, result=result[:2000], protocol="json")
        self._save()
        return {
            "protocol": "json",
            "steps": self.max_steps,
            "tool_calls": self.tool_calls,
            "final_message": "max steps reached",
        }
