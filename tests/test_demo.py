import demo


def test_copy_demo_repo_excludes_answer_patches(tmp_path):
    dest = demo.copy_demo_repo(tmp_path / "repo")
    assert (dest / "app" / "discount.py").is_file()
    assert (dest / "tests" / "test_discount.py").is_file()
    assert (dest / "task.json").is_file()
    assert (dest / ".gitignore").is_file()
    assert not (dest / "patches").exists()
    assert not (dest / "README.md").exists()


def test_summarize_none():
    assert demo.summarize(None)["verdict"] == "NO_EVIDENCE"


def test_summarize_data():
    data = {
        "verdict": "FAIL",
        "checks": [{"name": "a", "passed": True}, {"name": "b", "passed": False}],
        "hidden": {"stats": {"total": 3, "passed": 2}},
        "mutation": None,
        "coverage": None,
    }
    summary = demo.summarize(data)
    assert summary["verdict"] == "FAIL"
    assert summary["checks_passed"] == 1
    assert summary["checks_total"] == 2
    assert summary["failed_checks"] == ["b"]
