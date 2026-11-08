#!/usr/bin/env python3
"""Precondition smoke test: model catalog + one tool-calling round trip."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.vultr_llm import VultrClient, VultrError  # noqa: E402

PING_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Return the given text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    try:
        client = VultrClient(model=args.model)
    except VultrError as exc:
        print(f"BLOCKED: {exc}")
        print("Write the key into .env as VULTR_INFERENCE_API_KEY, then rerun.")
        return 2

    try:
        models = client.list_models()
    except VultrError as exc:
        print(f"FAILED: cannot list models: {exc}")
        print("If the endpoint is unreachable, set VULTR_PROXY in .env.")
        return 1
    model = client.pick_model()
    print(f"models: {len(models)} available; using {model}")

    messages = [
        {
            "role": "user",
            "content": "Call the ping tool with text 'hello', then report the result.",
        }
    ]
    tool_calls = []
    content = ""
    error = ""
    try:
        response = client.chat(
            messages,
            tools=PING_TOOL,
            tool_choice={"type": "function", "function": {"name": "ping"}},
            max_tokens=300,
            model=model,
        )
        message = (response.get("choices") or [{}])[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        if not tool_calls:
            response = client.chat(messages, tools=PING_TOOL, max_tokens=300, model=model)
            message = (response.get("choices") or [{}])[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            content = message.get("content") or ""
    except VultrError as exc:
        error = str(exc)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "models_available": len(models),
        "tool_calls": tool_calls,
        "content": content,
        "error": error,
        "tool_calling": bool(tool_calls),
    }
    out = Path(args.out) if args.out else ROOT / "runs" / (
        "smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tool calling: {'OK' if tool_calls else 'NOT OBSERVED'}")
    if tool_calls:
        print(json.dumps(tool_calls, ensure_ascii=False)[:800])
    elif error:
        print(f"error: {error[:800]}")
    else:
        print(f"model replied without tool calls: {content[:500]}")
    print(f"smoke: {out}")
    return 0 if tool_calls else 1


if __name__ == "__main__":
    raise SystemExit(main())
