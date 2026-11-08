import os

import pytest

from agent import vultr_llm
from agent.vultr_llm import VultrClient, VultrError, load_dotenv


def test_load_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROOFLOOP_TEST_KEY=abc\nQUOTED='xyz'\n# comment\n", encoding="utf-8"
    )
    monkeypatch.delenv("PROOFLOOP_TEST_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    try:
        values = load_dotenv(env_file)
        assert values["PROOFLOOP_TEST_KEY"] == "abc"
        assert values["QUOTED"] == "xyz"
        assert os.environ["PROOFLOOP_TEST_KEY"] == "abc"
    finally:
        os.environ.pop("PROOFLOOP_TEST_KEY", None)
        os.environ.pop("QUOTED", None)


def test_client_requires_key(monkeypatch):
    monkeypatch.setattr(vultr_llm, "load_dotenv", lambda path=None: {})
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    with pytest.raises(VultrError):
        VultrClient()


def _client(monkeypatch):
    monkeypatch.setattr(vultr_llm, "load_dotenv", lambda path=None: {})
    return VultrClient(api_key="test-key", model="m1")


def test_supports_tools():
    assert VultrClient._supports_tools(
        {
            "output_modalities": [{"type": "text"}],
            "input_modalities": [{"supported_parameters": {"tools": {}}}],
        }
    )
    assert not VultrClient._supports_tools(
        {"output_modalities": [{"type": "audio"}]}
    )


def test_pick_model_uses_catalog(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "list_models",
        lambda: [
            {
                "id": "tool-model",
                "output_modalities": [{"type": "text"}],
                "input_modalities": [{"supported_parameters": {"tools": {}}}],
            }
        ],
    )
    assert client.pick_model() == "tool-model"


def test_chat_retries_without_sampling_fields(monkeypatch):
    client = _client(monkeypatch)
    calls = []

    def fake_request(method, path, payload=None, auth=True):
        calls.append(payload)
        if len(calls) == 1:
            raise VultrError(400, "unknown parameter: temperature")
        return {"choices": []}

    monkeypatch.setattr(client, "_request", fake_request)
    client.chat([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=10)
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
    assert calls[1]["messages"][0]["content"] == "hi"


def test_chat_includes_tools(monkeypatch):
    client = _client(monkeypatch)
    seen = {}

    def fake_request(method, path, payload=None, auth=True):
        seen.update(payload)
        return {"choices": []}

    monkeypatch.setattr(client, "_request", fake_request)
    client.chat(
        [{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "ping"}}],
    )
    assert seen["tools"][0]["function"]["name"] == "ping"
    assert seen["tool_choice"] == "auto"
