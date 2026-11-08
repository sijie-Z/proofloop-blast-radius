import pytest

from agent.react_loop import ReactAgent, SandboxedRepo, _parse_json_action, _text_of


def test_parse_json_action():
    action = _parse_json_action(
        'before {"tool": "read_file", "args": {"path": "app/x.py"}} after'
    )
    assert action["tool"] == "read_file"
    assert _parse_json_action("no json here") is None


def test_text_of_normalizes_parts():
    assert _text_of("abc") == "abc"
    assert (
        _text_of(
            [{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]
        )
        == "abcd"
    )
    assert _text_of(None) == ""


def _repo(tmp_path):
    return SandboxedRepo(
        tmp_path / "repo", allowed_paths=["app/"], scratch_dir=tmp_path / "scratch"
    )


def test_path_guards(tmp_path):
    repo = _repo(tmp_path)
    assert repo._relative("app/discount.py") == "app/discount.py"
    with pytest.raises(ValueError):
        repo._relative("../escape.py")
    with pytest.raises(ValueError):
        repo._safe_write("tests/test_discount.py")
    with pytest.raises(ValueError):
        repo._safe_write("other/file.py")
    assert repo._safe_write("app/new.py") == "app/new.py"
    with pytest.raises(ValueError):
        repo._safe_read(".git/config")


def test_unknown_tool_is_reported(tmp_path):
    agent = ReactAgent(
        client=object(),
        repo=_repo(tmp_path),
        task={"allowed_paths": ["app/"]},
    )
    assert agent._execute_tool("nope", {}) == "ERROR: unknown tool 'nope'"
