"""Minimal OpenAI-compatible client for Vultr Serverless Inference.

The API lives at https://api.vultrinference.com/v1 and exposes
GET /models and POST /chat/completions.  Only the standard library is used so
the demo runs on a clean Python installation.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.vultrinference.com/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"

# Models that advertise tool calling in the live catalog.  Used only as a
# fallback when the configured model has disappeared from the catalog.
TOOL_MODEL_FALLBACKS = (
    "deepseek-v4.1-flash-1017",
    "glm-5.3",
    "glm-5.3-flash",
    "qwen3.8-27b",
    "mimo-v2.6-pro",
)


class VultrError(RuntimeError):
    """HTTP or transport error from the Vultr inference API."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Vultr API error {status}: {message}")
        self.status = status
        self.message = message


def load_dotenv(path=None) -> dict:
    """Load KEY=VALUE lines from .env without overriding real environment vars."""
    path = Path(path or Path(__file__).resolve().parent.parent / ".env")
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            if key not in os.environ:
                os.environ[key] = value
    return values


class VultrClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 180,
        proxy: str | None = None,
    ):
        load_dotenv()
        self.api_key = api_key or os.environ.get("VULTR_INFERENCE_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("VULTR_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.environ.get("VULTR_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.proxy = proxy or os.environ.get("VULTR_PROXY") or None
        if not self.api_key:
            raise VultrError(
                0,
                "VULTR_INFERENCE_API_KEY is not set. Copy .env.example to .env "
                "and add the Serverless Inference key (do not share it in chat).",
            )

    def _open(self, request, timeout=None):
        if self.proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {"http": self.proxy, "https": self.proxy}
                )
            )
            return opener.open(request, timeout=timeout or self.timeout)
        return urllib.request.urlopen(request, timeout=timeout or self.timeout)

    def _request(self, method: str, path: str, payload=None, auth=True) -> dict:
        url = self.base_url + path
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "proofloop-blast-radius/0.1",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise VultrError(exc.code, body[:2000]) from exc
        except urllib.error.URLError as exc:
            raise VultrError(0, f"connection failed: {exc.reason}") from exc
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise VultrError(0, f"invalid JSON response: {body[:500]}") from exc

    def list_models(self) -> list:
        return self._request("GET", "/models", auth=False).get("data", [])

    @staticmethod
    def _supports_tools(model: dict) -> bool:
        outputs = model.get("output_modalities") or []
        if not any(item.get("type") == "text" for item in outputs):
            return False
        params = {}
        for modality in (model.get("input_modalities") or []) + outputs:
            params.update(modality.get("supported_parameters") or {})
        return "tools" in params or "tool_choice" in params

    def pick_model(self) -> str:
        """Return the configured model if present, else a tool-capable one."""
        try:
            models = self.list_models()
        except VultrError:
            return self.model
        by_id = {m.get("id"): m for m in models if m.get("id")}
        if self.model in by_id:
            return self.model
        for candidate in TOOL_MODEL_FALLBACKS:
            if candidate in by_id:
                return candidate
        for model_id, model in by_id.items():
            if self._supports_tools(model):
                return model_id
        return self.model

    def chat(
        self,
        messages: list,
        tools: list | None = None,
        tool_choice=None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> dict:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        try:
            return self._request("POST", "/chat/completions", payload)
        except VultrError as exc:
            # Some catalog models reject sampling fields they do not implement.
            # Retry once with the minimal OpenAI payload before giving up.
            message = exc.message.lower()
            if exc.status in (400, 422) and any(
                token in message
                for token in ("temperature", "max_tokens", "max_completion_tokens",
                              "unknown parameter", "unrecognized")
            ):
                minimal = {"model": payload["model"], "messages": messages}
                if tools:
                    minimal["tools"] = tools
                    minimal["tool_choice"] = tool_choice or "auto"
                return self._request("POST", "/chat/completions", minimal)
            raise
