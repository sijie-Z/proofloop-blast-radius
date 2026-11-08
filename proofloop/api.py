from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .service import RUNS, verify_repo_impl

MAX_BODY_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


def _api_token():
    return os.environ.get("PROOFLOOP_API_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "proofloop"})
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/evidence/"):
            run_id = unquote(parsed.path[len("/evidence/"):]).strip("/")
            if not run_id or "/" in run_id or "\\" in run_id:
                self._send(
                    400,
                    {"error": "evidence run_id must resolve to runs/<run_id>/evidence.json"},
                )
                return
            evidence_path = (RUNS / run_id / "evidence.json").resolve()
            if evidence_path.parent.parent != RUNS.resolve():
                self._send(
                    400,
                    {"error": "evidence run_id must resolve to runs/<run_id>/evidence.json"},
                )
                return
            if not evidence_path.is_file():
                self._send(404, {"error": f"no evidence for run_id: {run_id}"})
                return
            if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES:
                self._send(413, {"error": "evidence file exceeds the 2MB limit"})
                return
            self._send(200, json.loads(evidence_path.read_text(encoding="utf-8")))
            return
        if self.path.startswith("/evidence"):
            query = parse_qs(urlparse(self.path).query)
            raw = query.get("path", [""])[0]
            path = Path(raw).resolve()
            if not path.is_relative_to(RUNS.resolve()) or not path.is_file():
                self._send(400, {"error": "evidence path must be a file under ProofLoop/runs"})
                return
            self._send(200, json.loads(path.read_text(encoding="utf-8")))
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/verify":
            self._send(404, {"error": "not found"})
            return
        token = _api_token()
        if token and not hmac.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {token}"
        ):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._send(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "request body too large (max 1MB)"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        missing = [k for k in ("repo", "task", "patch") if not payload.get(k)]
        if missing:
            self._send(400, {"error": f"missing fields: {missing}"})
            return
        try:
            result = verify_repo_impl(
                payload["repo"],
                payload["task"],
                payload["patch"],
                payload.get("out", ""),
            )
        except Exception as exc:  # keep the demo server alive on bad input
            self._send(500, {"error": str(exc)})
            return
        self._send(200, result)

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser(prog="proofloop.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not _api_token() and args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error(
            "PROOFLOOP_API_TOKEN must be set to bind the local API beyond 127.0.0.1"
        )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ProofLoop API: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
