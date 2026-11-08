import json
import time

import pytest
from fastapi.testclient import TestClient

import webapp.app as app_module
from proofloop import evidence as evidence_mod
from webapp import jobs
from webapp.sanitize import sanitize_obj, sanitize_text


def test_sanitize():
    root = r"D:\Desktop\proj"
    text = r"repo D:\Desktop\proj\runs\x and C:\Users\me\secret"
    out = sanitize_text(text, root)
    assert "D:\\Desktop" not in out
    assert "C:\\Users" not in out
    data = sanitize_obj({"p": r"D:\Desktop\proj\a"}, root)
    assert data["p"] == "[project]\\a"


def test_health_and_index():
    client = TestClient(app_module.app)
    assert client.get("/health").json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "ProofLoop" in page.text


def test_invalid_mode():
    client = TestClient(app_module.app)
    response = client.post("/api/runs", json={"mode": "nope"})
    assert response.status_code == 400


def test_vultr_mode_requires_key(monkeypatch, tmp_path):
    import agent.vultr_llm as vultr_llm

    monkeypatch.setattr(vultr_llm, "load_dotenv", lambda path=None: {})
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    manager = jobs.RunManager(root=app_module.ROOT, runs_dir=tmp_path / "runs")
    with pytest.raises(jobs.MissingKey):
        manager.start("vultr")


def test_run_lifecycle_with_fake_executor(monkeypatch, tmp_path):
    manager = jobs.RunManager(root=app_module.ROOT, runs_dir=tmp_path / "runs")

    def fake_execute(self, run):
        self._set(
            run,
            status="succeeded",
            stage="done",
            pass_scene={"verdict": "PASS"},
            fail_scene={"verdict": "FAIL"},
        )
        with self._lock:
            self._active = None

    monkeypatch.setattr(jobs.RunManager, "_execute", fake_execute)
    run = manager.start("fixed")
    deadline = time.time() + 3
    while time.time() < deadline:
        data = manager.get(run.id)
        if data and data["status"] == "succeeded":
            break
        time.sleep(0.02)
    data = manager.get(run.id)
    assert data["status"] == "succeeded"
    assert data["pass_scene"]["verdict"] == "PASS"


def test_run_dir_traversal_rejected(tmp_path):
    manager = jobs.RunManager(root=app_module.ROOT, runs_dir=tmp_path / "runs")
    assert manager.run_dir("../etc") is None
    assert manager.report_path("../../x", "pass") is None
    assert manager.evidence_path("not-a-run-id", "pass") is None


def test_verify_evidence_hash(tmp_path):
    run_id = "a" * 12
    manager = jobs.RunManager(root=app_module.ROOT, runs_dir=tmp_path / "runs")
    directory = tmp_path / "runs" / run_id / "evidence-pass"
    directory.mkdir(parents=True)
    data = {
        "verdict": "PASS",
        "task": {"name": "x", "description": "", "acceptance": []},
        "checks": [],
        "repo": "[project]",
        "base_commit": "b",
        "base_tree": "b",
        "candidate_tree": "c",
        "patch_sha256": "p",
        "diff_sha256": "d",
        "environment": {},
    }
    digest = evidence_mod.canonical_json_digest(data)
    data["evidence_sha256"] = digest
    (directory / "evidence.json").write_text(json.dumps(data), encoding="utf-8")
    (directory / "evidence.sha256").write_text(digest + "\n", encoding="utf-8")
    with manager._lock:
        manager._runs[run_id] = jobs.RunState(
            id=run_id, mode="fixed", status="succeeded"
        )
    assert manager.verify_evidence(run_id)["ok"] is True
    data["verdict"] = "FAIL"
    (directory / "evidence.json").write_text(json.dumps(data), encoding="utf-8")
    assert manager.verify_evidence(run_id)["ok"] is False


def test_replay_lifecycle_with_fake_worker(monkeypatch, tmp_path):
    run_id = "b" * 12
    manager = jobs.RunManager(root=app_module.ROOT, runs_dir=tmp_path / "runs")
    (tmp_path / "runs" / run_id).mkdir(parents=True)
    with manager._lock:
        manager._runs[run_id] = jobs.RunState(
            id=run_id, mode="fixed", status="succeeded"
        )

    def fake_worker(self, run, directory):
        self._set(
            run,
            replay={
                "status": "done",
                "ok": True,
                "consistent": True,
                "new_verdict": "PASS",
            },
        )

    monkeypatch.setattr(jobs.RunManager, "_replay_worker", fake_worker)
    manager.start_replay(run_id)
    deadline = time.time() + 3
    while time.time() < deadline:
        replay = manager.get(run_id)["replay"]
        if replay.get("status") == "done":
            break
        time.sleep(0.02)
    assert manager.get(run_id)["replay"]["ok"] is True
