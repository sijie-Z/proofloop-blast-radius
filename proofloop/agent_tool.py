from __future__ import annotations

from .service import verify_repo_impl

try:
    from openjiuwen.core.foundation.tool import tool
except Exception:  # openjiuwen is optional for the local demo
    tool = None


if tool is not None:
    verify_repo_tool = tool(
        name="verify_repo",
        description=(
            "Verify an AI coding agent's candidate patch against frozen acceptance criteria. "
            "Runs baseline/candidate tests, hidden tests, test-integrity checks and scope checks; "
            "returns a PASS/FAIL/INSUFFICIENT_EVIDENCE verdict and an evidence file path."
        ),
        input_params={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the repository to verify"},
                "task_path": {"type": "string", "description": "Path to task.json"},
                "patch_path": {"type": "string", "description": "Path to the candidate patch"},
                "out_dir": {
                    "type": "string",
                    "description": "Optional output directory under ProofLoop/runs",
                },
            },
            "required": ["repo_path", "task_path", "patch_path"],
        },
    )(verify_repo_impl)
else:
    verify_repo_tool = verify_repo_impl
