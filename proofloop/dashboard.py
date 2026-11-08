from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def collect():
    rows = []
    if not RUNS.is_dir():
        return rows
    for evidence in sorted(RUNS.glob("*/evidence.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_dir"] = evidence.parent.name
        rows.append(data)
    return rows


def render(rows):
    def esc(value):
        return html.escape(str(value), quote=True)

    body = []
    for data in rows:
        verdict = data.get("verdict", "UNKNOWN")
        cls = {"PASS": "ok", "FAIL": "bad", "INSUFFICIENT_EVIDENCE": "warn"}.get(verdict, "")
        checks = data.get("checks", [])
        passed = sum(1 for c in checks if c.get("passed"))
        candidate = data.get("candidate", {}).get("stats", {})
        hidden = (data.get("hidden") or {}).get("stats", {})
        body.append(
            f"""<tr>
<td><a href="{esc(data['_dir'])}/report.html">{esc(data.get('task', {}).get('name', data['_dir']))}</a></td>
<td class="{cls}">{esc(verdict)}</td>
<td>{esc(data.get('generated_at', ''))}</td>
<td>{passed}/{len(checks)}</td>
<td>visible {esc(candidate.get('passed', 0))}/{esc(candidate.get('total', 0))} · hidden {esc(hidden.get('passed', 0))}/{esc(hidden.get('total', 0))}</td>
</tr>"""
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>ProofLoop Runs</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 32px; color: #1f2328; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ color: #57606a; font-size: 13px; margin-bottom: 18px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #d0d7de; padding: 8px 10px; text-align: left; }}
th {{ background: #f6f8fa; }}
.ok {{ color: #1a7f37; font-weight: 700; }}
.bad {{ color: #cf222e; font-weight: 700; }}
.warn {{ color: #9a6700; font-weight: 700; }}
a {{ color: #0969da; text-decoration: none; }}
</style></head><body>
<h1>ProofLoop runs</h1>
<div class="meta">{len(rows)} runs · click a task to open its evidence report</div>
<table><thead><tr><th>task</th><th>verdict</th><th>generated</th><th>checks</th><th>tests</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
</body></html>
"""


def main():
    rows = collect()
    RUNS.mkdir(exist_ok=True)
    out = RUNS / "index.html"
    out.write_text(render(rows), encoding="utf-8")
    print(f"dashboard: {out}")
    print(f"runs: {len(rows)}")


if __name__ == "__main__":
    main()
