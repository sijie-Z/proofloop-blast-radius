import hashlib
import html
import json
from pathlib import Path


def acceptance_sha256(acceptance):
    """Canonical sha256 over the normalized acceptance-criteria JSON."""
    canonical = json.dumps(
        acceptance, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_json_digest(data):
    """sha256 over the canonical JSON payload, excluding the self-hash field."""
    payload = {key: value for key, value in data.items() if key != "evidence_sha256"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_evidence(out_dir, data):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = canonical_json_digest(data)
    data["evidence_sha256"] = digest
    (out_dir / "evidence.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "evidence.sha256").write_text(digest + "\n", encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(data), encoding="utf-8")


def _esc(value):
    return html.escape(str(value), quote=True)


def render_html(data):
    verdict = data["verdict"]
    color = {"PASS": "#1a7f37", "FAIL": "#cf222e", "INSUFFICIENT_EVIDENCE": "#9a6700"}.get(
        verdict, "#57606a"
    )
    rows = []
    for c in data["checks"]:
        status = "PASS" if c["passed"] else "FAIL"
        rows.append(
            "<tr><td>{}</td><td class='{}'>{}</td><td>{}</td></tr>".format(
                _esc(c["name"]),
                "ok" if c["passed"] else "bad",
                status,
                _esc(c["detail"]),
            )
        )
    checks_html = "\n".join(rows)
    unverified_html = "\n".join(
        f"<li>{_esc(item)}</li>" for item in data.get("unverified", [])
    )
    commands_html = _esc(json.dumps(data.get("commands", {}), ensure_ascii=False, indent=2))
    executor = data.get("executor", {})
    baseline = data["baseline"]
    candidate = data["candidate"]
    stats = candidate["stats"]
    hidden = data.get("hidden")
    hidden_html = ""
    if hidden:
        hs = hidden["stats"]
        hidden_html = f"""<p>hidden：exit={_esc(hidden['exit_code'])}，total={_esc(hs['total'])}，passed={_esc(hs['passed'])}，failed={_esc(hs['failed'])}，errors={_esc(hs['errors'])}</p>
<details><summary><strong>hidden stdout</strong></summary><pre>{_esc(hidden['stdout'])}</pre></details>"""
    mutation = data.get("mutation")
    mutation_html = ""
    if mutation:
        mutation_html = (
            f"<p>mutation：killed={_esc(mutation['killed'])}/{_esc(mutation['total'])}，"
            f"survived={_esc(mutation['survived'])}，score={_esc(mutation['score'])}，"
            f"threshold={_esc(data['task'].get('mutation_threshold', 0))}</p>"
        )
    coverage = data.get("coverage")
    coverage_html = ""
    if coverage:
        coverage_html = (
            f"<p>coverage：source={_esc(coverage.get('source', ''))}，"
            f"percent={_esc(coverage.get('percent', 0))}%，"
            f"threshold={_esc(data['task'].get('coverage_threshold', 0))}，"
            f"skipped={_esc(coverage.get('skipped', False))}</p>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>ProofLoop Evidence - {_esc(data['run_id'])}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 32px; color: #1f2328; }}
h1 {{ margin: 0 0 4px 0; }}
.verdict {{ font-size: 22px; font-weight: 700; color: {color}; }}
.meta {{ color: #57606a; font-size: 13px; margin: 4px 0 18px 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0 20px 0; font-size: 13px; }}
th, td {{ border: 1px solid #d0d7de; padding: 8px 10px; text-align: left; vertical-align: top; }}
th {{ background: #f6f8fa; }}
.ok {{ color: #1a7f37; font-weight: 600; }}
.bad {{ color: #cf222e; font-weight: 600; }}
pre {{ background: #f6f8fa; border: 1px solid #d0d7de; padding: 12px; overflow: auto; font-size: 12px; }}
code {{ font-family: Consolas, monospace; }}
</style>
</head>
<body>
<h1>ProofLoop 交付证据单</h1>
<div class="verdict">{_esc(verdict)}</div>
<div class="meta">run_id: {_esc(data['run_id'])} · {_esc(data['generated_at'])} · task: {_esc(data['task']['name'])}</div>
<p><strong>任务：</strong>{_esc(data['task']['description'])}</p>
<p><strong>验收条件：</strong>{_esc('；'.join(data['task']['acceptance']))}</p>
<p><strong>仓库：</strong><code>{_esc(data['repo'])}</code><br>
<strong>base commit：</strong><code>{_esc(data['base_commit'])}</code><br>
<strong>base tree：</strong><code>{_esc(data.get('base_tree', ''))}</code><br>
<strong>candidate tree：</strong><code>{_esc(data.get('candidate_tree', ''))}</code><br>
<strong>executor：</strong><code>{_esc(executor.get('kind', ''))} {_esc(executor.get('patch', ''))}</code><br>
<strong>patch sha256：</strong><code>{_esc(data['patch_sha256'])}</code><br>
<strong>acceptance sha256：</strong><code>{_esc(data.get('acceptance_sha256', ''))}</code><br>
<strong>evidence sha256：</strong><code>{_esc(data.get('evidence_sha256', ''))}</code><br>
<strong>diff sha256：</strong><code>{_esc(data['diff_sha256'])}</code></p>

<h2>检查项</h2>
<table>
<thead><tr><th>检查</th><th>结果</th><th>说明</th></tr></thead>
<tbody>
{checks_html}
</tbody>
</table>

<h2>未验证边界</h2>
<ul>
{unverified_html}
</ul>

<h2>执行的命令</h2>
<pre>{commands_html}</pre>

<h2>测试结果</h2>
<p>baseline：exit={_esc(baseline['exit_code'])}，total={_esc(baseline['stats']['total'])}，failed={_esc(baseline['stats']['failed'])}，errors={_esc(baseline['stats']['errors'])}</p>
<p>candidate：exit={_esc(candidate['exit_code'])}，total={_esc(stats['total'])}，passed={_esc(stats['passed'])}，failed={_esc(stats['failed'])}，errors={_esc(stats['errors'])}，skipped={_esc(stats['skipped'])}</p>
{hidden_html}
{mutation_html}
{coverage_html}

<details><summary><strong>baseline stdout</strong></summary><pre>{_esc(baseline['stdout'])}</pre></details>
<details><summary><strong>candidate stdout</strong></summary><pre>{_esc(candidate['stdout'])}</pre></details>
<details><summary><strong>candidate stderr</strong></summary><pre>{_esc(candidate['stderr'])}</pre></details>

<h2>环境</h2>
<pre>{_esc(json.dumps(data['environment'], ensure_ascii=False, indent=2))}</pre>
</body>
</html>
"""
