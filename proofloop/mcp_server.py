from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .service import RUNS, verify_repo_impl

LOG = Path(__file__).resolve().parent.parent / "runs" / "mcp_server.log"
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
_EVIDENCE_NAMES = {"evidence.json", "evidence.sha256"}

# At most two verifications run concurrently (each may start containers).
_VERIFY_SEMAPHORE = asyncio.Semaphore(2)


def _log(message: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.time():.3f} {message}\n")


mcp = FastMCP("ProofLoop")


@mcp.tool()
def ping() -> str:
    """Health check for the MCP transport."""
    _log("ping")
    return "pong"


@mcp.tool()
async def verify_repo(repo_path: str, task_path: str, patch_path: str, out_dir: str = "") -> dict:
    """Verify a candidate patch against acceptance criteria and return the verdict."""
    _log("verify start")
    async with _VERIFY_SEMAPHORE:
        try:
            result = await asyncio.to_thread(
                verify_repo_impl, repo_path, task_path, patch_path, out_dir
            )
            _log("verify done " + str(result.get("verdict")))
            return result
        except Exception as exc:
            _log("verify error " + repr(exc))
            raise


@mcp.tool()
async def get_evidence(evidence_path: str) -> dict:
    """Read runs/<run>/evidence.json or evidence.sha256 (max 2MB)."""
    path = Path(evidence_path).resolve()
    runs = RUNS.resolve()
    if path.name not in _EVIDENCE_NAMES:
        raise ValueError("only evidence.json or evidence.sha256 may be read")
    if path.parent.parent != runs or path.parent == runs:
        raise ValueError("evidence path must be runs/<run>/<evidence file>")
    if not path.is_file():
        raise ValueError("evidence file not found")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence file exceeds the 2MB limit")
    if path.name == "evidence.sha256":
        digest = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return {"sha256": digest.strip()}
    return await asyncio.to_thread(
        lambda: json.loads(path.read_text(encoding="utf-8"))
    )


if __name__ == "__main__":
    mcp.run()
