"""Background job manager for the web demo.

One demo run at a time.  The manager is the trusted control plane: it holds the
Vultr key in the server process and only hands sandbox mounts to Docker.  The
sandbox containers never receive the key.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from demo import copy_demo_repo, copy_hidden, make_patch, run_verify, summarize
from proofloop import snapshot

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(__file__).resolve().parent / "runs"
DEFAULT_MAX_RUNS_PER_HOUR = 20
DEFAULT_TIMEOUT = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunBusy(RuntimeError):
    pass


class MissingKey(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


@dataclass
class RunState:
    id: str
    mode: str
    status: str = "queued"
    stage: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    events: list = field(default_factory=list)
    pass_scene: dict = field(default_factory=dict)
    fail_scene: dict = field(default_factory=dict)
    containment: dict = field(default_factory=dict)
    agent: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RunManager:
    def __init__(self, root=ROOT, runs_dir=RUNS_DIR):
        self.root = Path(root).resolve()
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.max_runs_per_hour = int(
            os.environ.get("PROOFLOOP_DEMO_MAX_RUNS_PER_HOUR", DEFAULT_MAX_RUNS_PER_HOUR)
        )
        self.timeout = int(os.environ.get("PROOFLOOP_DEMO_TIMEOUT", DEFAULT_TIMEOUT))
        self._lock = threading.RLock()
        self._runs: dict[str, RunState] = {}
        self._active: str | None = None
        self._starts: list[float] = []
        self._probe_lock = threading.Lock()
        self._probe_cache: dict | None = None
        self._probe_at = 0.0

    @property
    def active_id(self):
        with self._lock:
            return self._active

    def _set(self, run: RunState, **fields):
        with self._lock:
            for key, value in fields.items():
                setattr(run, key, value)
            run.updated_at = _now()

    def _event(self, run: RunState, message: str):
        with self._lock:
            run.events.append({"at": _now(), "message": message})
            run.updated_at = _now()
            run.events[:] = run.events[-200:]

    def start(self, mode: str) -> RunState:
        if mode not in ("vultr", "fixed"):
            raise ValueError("mode must be 'vultr' or 'fixed'")
        with self._lock:
            if self._active:
                raise RunBusy("another demo run is already active")
            now = time.time()
            self._starts = [stamp for stamp in self._starts if now - stamp < 3600]
            if len(self._starts) >= self.max_runs_per_hour:
                raise RateLimited(
                    f"hourly run limit reached ({self.max_runs_per_hour} runs/hour)"
                )
            if mode == "vultr":
                from agent.vultr_llm import load_dotenv

                load_dotenv()
                if not os.environ.get("VULTR_INFERENCE_API_KEY"):
                    raise MissingKey(
                        "VULTR_INFERENCE_API_KEY is not configured on the server"
                    )
            run = RunState(
                id=uuid.uuid4().hex[:12],
                mode=mode,
                status="running",
                stage="starting",
            )
            self._runs[run.id] = run
            self._active = run.id
            self._starts.append(now)
        threading.Thread(target=self._execute, args=(run,), daemon=True).start()
        return run

    def get(self, run_id: str):
        with self._lock:
            run = self._runs.get(run_id)
            return run.to_dict() if run else None

    def list_runs(self, limit: int = 20) -> list:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)
            return [
                {
                    "id": run.id,
                    "mode": run.mode,
                    "status": run.status,
                    "stage": run.stage,
                    "created_at": run.created_at,
                    "verdict": (run.pass_scene or {}).get("verdict"),
                }
                for run in runs[:limit]
            ]

    def _valid_id(self, run_id: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{12}", run_id or ""))

    def run_dir(self, run_id: str):
        if not self._valid_id(run_id):
            return None
        return self.runs_dir / run_id

    def report_path(self, run_id: str, scene: str):
        directory = self.run_dir(run_id)
        name = {"pass": "evidence-pass", "fail": "evidence-fail"}.get(scene)
        if directory is None or name is None:
            return None
        path = directory / name / "report.html"
        return path if path.is_file() else None

    def evidence_path(self, run_id: str, scene: str):
        directory = self.run_dir(run_id)
        name = {"pass": "evidence-pass", "fail": "evidence-fail"}.get(scene)
        if directory is None or name is None:
            return None
        path = directory / name / "evidence.json"
        return path if path.is_file() else None

    @staticmethod
    def _agent_events(path: Path) -> list:
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        events = []
        for item in data.get("transcript", []):
            if item.get("kind") != "tool":
                continue
            events.append(
                {
                    "step": item.get("step"),
                    "name": item.get("name", ""),
                    "result": str(item.get("result", ""))[:400],
                }
            )
        return events[-40:]

    def _execute(self, run: RunState):
        try:
            self._event(run, "preparing workspace")
            run_dir = self.runs_dir / run.id
            run_dir.mkdir(parents=True, exist_ok=False)
            scene1 = run_dir / "scene1"
            repo1 = copy_demo_repo(scene1 / "repo")
            verify_repo1 = copy_demo_repo(scene1 / "verify-repo")
            copy_hidden(scene1 / "discount_hidden")
            task_data = json.loads(
                (verify_repo1 / "task.json").read_text(encoding="utf-8")
            )

            from agent.react_loop import SandboxedRepo

            self._set(run, stage="containment probe")
            probe = SandboxedRepo(
                repo1,
                allowed_paths=task_data.get("allowed_paths"),
                scratch_dir=run_dir / "probe-scratch",
            ).attest()
            self._set(run, containment=probe)
            self._event(
                run,
                "containment: network={} secret_env={}".format(
                    probe.get("network"), probe.get("secret_env")
                ),
            )

            if run.mode == "vultr":
                from agent.react_loop import ReactAgent
                from agent.vultr_llm import VultrClient

                self._set(run, stage="starting Vultr agent")
                client = VultrClient()
                client.model = client.pick_model()
                self._event(run, f"model: {client.model}")
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
                    max_steps=12,
                    log_path=run_dir / "agent_transcript.json",
                )
                agent_result = agent.run()
                self._set(
                    run,
                    agent={
                        "mode": "vultr",
                        "model": client.model,
                        "protocol": agent_result["protocol"],
                        "steps": agent_result["steps"],
                        "tool_calls": agent_result["tool_calls"],
                        "final_message": str(agent_result["final_message"])[:2000],
                        "events": self._agent_events(
                            run_dir / "agent_transcript.json"
                        ),
                    },
                )
                patch1 = run_dir / "scene1-agent.patch"
                patch_bytes = make_patch(repo1, base_commit, patch1)
            else:
                patch1 = (
                    self.root
                    / "examples"
                    / "discount_demo"
                    / "patches"
                    / "fixed.patch"
                )
                patch_bytes = patch1.read_bytes()
                self._set(run, agent={"mode": "fixed self-test"})

            self._event(run, f"candidate patch: {len(patch_bytes)} bytes")
            self._set(run, stage="verifying agent fix")
            _, data1, _ = run_verify(
                verify_repo1,
                verify_repo1 / "task.json",
                patch1,
                run_dir / "evidence-pass",
                self.timeout,
            )
            self._set(run, pass_scene=summarize(data1))
            self._event(run, f"scene 1: {run.pass_scene.get('verdict')}")

            self._set(run, stage="verifying cheat patch")
            scene2 = run_dir / "scene2"
            repo2 = copy_demo_repo(scene2 / "repo")
            copy_hidden(scene2 / "discount_hidden")
            _, data2, _ = run_verify(
                repo2,
                repo2 / "task.json",
                self.root
                / "examples"
                / "discount_demo"
                / "patches"
                / "cheat_hardcode.patch",
                run_dir / "evidence-fail",
                self.timeout,
            )
            self._set(run, fail_scene=summarize(data2))
            self._event(run, f"scene 2: {run.fail_scene.get('verdict')}")

            ok = (
                run.pass_scene.get("verdict") == "PASS"
                and run.fail_scene.get("verdict") == "FAIL"
            )
            self._set(run, status="succeeded" if ok else "failed", stage="done")
            self._event(
                run,
                "demo finished" if ok else "demo finished with unexpected verdicts",
            )
        except Exception as exc:
            self._set(
                run,
                status="failed",
                stage="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            self._event(run, f"error: {type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._active = None

    def probe(self) -> dict:
        with self._probe_lock:
            now = time.time()
            if self._probe_cache is not None and now - self._probe_at < 60:
                return self._probe_cache
            from agent.react_loop import SandboxedRepo

            repo = self.root / "examples" / "discount_demo"
            result = SandboxedRepo(
                repo,
                allowed_paths=["app/"],
                scratch_dir=self.runs_dir / ".probe",
            ).attest()
            self._probe_cache = result
            self._probe_at = now
            return result
