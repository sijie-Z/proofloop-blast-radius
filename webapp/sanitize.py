"""Remove local filesystem paths from anything the demo UI can serve."""
from __future__ import annotations

import re

_DRIVE_PATH = re.compile(r"(?i)\b[a-z]:[\\/][^<\s\"']{0,240}")


def sanitize_text(text: str, root) -> str:
    text = str(text).replace(str(root), "[project]")
    return _DRIVE_PATH.sub("[local path]", text)


def sanitize_obj(value, root):
    if isinstance(value, str):
        return sanitize_text(value, root)
    if isinstance(value, list):
        return [sanitize_obj(item, root) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_obj(item, root) for key, item in value.items()}
    return value
