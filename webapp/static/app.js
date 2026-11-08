const $ = (id) => document.getElementById(id);
let activeId = null;
let timer = null;

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

async function startRun() {
  const mode = $("mode").value;
  $("run").disabled = true;
  $("notice").textContent = "Starting…";
  try {
    const data = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    activeId = data.id;
    $("events").innerHTML = "";
    $("latest-link").hidden = false;
    $("notice").textContent = `Run ${data.id} started (${mode}).`;
    poll();
  } catch (error) {
    $("notice").textContent = `Error: ${error.message}`;
  } finally {
    $("run").disabled = false;
  }
}

async function poll() {
  if (!activeId) return;
  if (timer) clearTimeout(timer);
  try {
    const run = await api(`/api/runs/${activeId}`);
    renderRun(run);
    if (run.status === "running" || run.status === "queued") {
      timer = setTimeout(poll, 2000);
    } else {
      $("notice").textContent = `Run ${run.id}: ${run.status}.`;
    }
  } catch (error) {
    $("notice").textContent = `Error: ${error.message}`;
  }
}

function renderRun(run) {
  if (run.mode && ["vultr", "fixed"].includes(run.mode)) $("mode").value = run.mode;
  $("events").innerHTML = (run.events || [])
    .map((event) => `<li><span>${esc(event.at)}</span>${esc(event.message)}</li>`)
    .join("");
  renderScene("pass", run.pass_scene || {});
  renderScene("fail", run.fail_scene || {});
  const checkScene = (run.pass_scene && run.pass_scene.checks && run.pass_scene.checks.length)
    ? run.pass_scene
    : (run.fail_scene || {});
  renderChecks(checkScene);
  renderAgent(run.agent || {});
  if (run.containment && Object.keys(run.containment).length) renderProbe(run.containment);
  if (run.status === "failed" && run.error) {
    $("notice").textContent = `Run ${run.id} failed: ${run.error}`;
  }
}

function renderScene(prefix, scene) {
  const verdict = scene.verdict || "—";
  const verdictEl = $(`${prefix}-verdict`);
  verdictEl.textContent = verdict;
  const card = $(`${prefix}-card`);
  card.classList.toggle("pass", verdict === "PASS");
  card.classList.toggle("fail", verdict === "FAIL" || verdict === "INSUFFICIENT_EVIDENCE");
  const hidden = scene.hidden || {};
  const mutation = scene.mutation || {};
  const stats = [
    `<span>checks <b>${scene.checks_passed ?? 0}/${scene.checks_total ?? 14}</b></span>`,
    `<span>hidden <b>${hidden.passed ?? 0}/${hidden.total ?? 0}</b></span>`,
    `<span>mutation <b>${mutation.killed ?? "—"}/${mutation.total ?? "—"}</b></span>`,
    `<span>coverage <b>${scene.coverage ?? "—"}%</b></span>`,
  ];
  $(`${prefix}-stats`).innerHTML = stats.join("");
  const links = [];
  if (scene.report_url) links.push(`<a href="${esc(scene.report_url)}" target="_blank" rel="noreferrer">Open evidence report</a>`);
  if (scene.evidence_url) links.push(`<a href="${esc(scene.evidence_url)}" target="_blank" rel="noreferrer">evidence.json</a>`);
  if (scene.failed_checks && scene.failed_checks.length) {
    links.push(`<span class="muted">failed: ${scene.failed_checks.map(esc).join(", ")}</span>`);
  }
  $(`${prefix}-links`).innerHTML = links.join("");
}

function renderChecks(scene) {
  const body = $("checks").querySelector("tbody");
  const checks = scene.checks || [];
  if (!checks.length) {
    body.innerHTML = "";
    $("checks-note").hidden = false;
    return;
  }
  $("checks-note").hidden = true;
  body.innerHTML = checks.map((check) => {
    const cls = check.passed ? "ok" : "bad";
    const label = check.passed ? "PASS" : "FAIL";
    return `<tr><td class="mono">${esc(check.name)}</td><td><span class="badge ${cls}">${label}</span></td><td class="detail">${esc(check.detail)}</td></tr>`;
  }).join("");
}

function renderAgent(agent) {
  if (!agent || !Object.keys(agent).length) {
    $("agent").innerHTML = `<p class="muted">No agent run yet.</p>`;
    return;
  }
  const parts = [];
  if (agent.model) parts.push(`<p class="muted">Model: <code>${esc(agent.model)}</code>${agent.protocol ? ` · protocol: ${esc(agent.protocol)} · steps: ${esc(agent.steps)} · tool calls: ${esc(agent.tool_calls)}` : ""}</p>`);
  if (agent.final_message) parts.push(`<p class="muted">Final message: ${esc(agent.final_message)}</p>`);
  const events = agent.events || [];
  if (events.length) {
    parts.push(events.map((event) =>
      `<div class="tool-call"><div class="tool-head"><span class="mono">step ${esc(event.step)}</span><b>${esc(event.name)}</b></div><pre>${esc(event.result)}</pre></div>`
    ).join(""));
  } else if (!agent.final_message && !agent.model) {
    parts.push(`<p class="muted">Agent mode: ${esc(agent.mode || "unknown")}.</p>`);
  }
  $("agent").innerHTML = parts.join("");
}

function renderProbe(probe) {
  const network = probe.network || "unknown";
  const secrets = probe.secret_env || [];
  const networkBadge = network === "blocked"
    ? `<span class="badge ok">BLOCKED</span>`
    : `<span class="badge bad">${esc(network.toUpperCase())}</span>`;
  const secretBadge = secrets.length
    ? `<span class="badge bad">${esc(secrets.join(", "))}</span>`
    : `<span class="badge ok">NONE VISIBLE</span>`;
  const rows = [
    ["Network", networkBadge],
    ["Secret-looking env", secretBadge],
    ["UID / user", `<code>${esc(probe.uid)}</code> / <code>${esc(probe.user)}</code>`],
    ["Memory limit", `<code>${esc(probe.memory_max ?? "n/a")}</code>`],
    ["CPU quota", `<code>${esc(probe.cpu_max ?? "n/a")}</code>`],
  ];
  $("probe-result").innerHTML = rows.map(([label, value]) =>
    `<div class="probe-row"><span>${esc(label)}</span><span>${value}</span></div>`
  ).join("") + `<details><summary>Raw probe JSON</summary><pre>${esc(JSON.stringify(probe, null, 2))}</pre></details>`;
}

async function probeSandbox() {
  $("probe").disabled = true;
  $("probe-result").innerHTML = `<p class="muted">Starting a fresh container…</p>`;
  try {
    const probe = await api("/api/containment");
    renderProbe(probe);
  } catch (error) {
    $("probe-result").innerHTML = `<p class="badge bad">PROBE FAILED</p><p class="muted">${esc(error.message)}</p>`;
  } finally {
    $("probe").disabled = false;
  }
}

async function loadLatest() {
  try {
    const data = await api("/api/runs");
    if (data.runs && data.runs.length) {
      activeId = data.runs[0].id;
      $("latest-link").hidden = false;
      poll();
    }
  } catch { /* service may still be starting */ }
}

async function checkService() {
  try {
    await api("/health");
    $("service-state").textContent = "service online";
  } catch {
    $("service-state").textContent = "service offline";
  }
}

$("run").addEventListener("click", startRun);
$("probe").addEventListener("click", probeSandbox);
$("latest-link").addEventListener("click", (event) => {
  event.preventDefault();
  poll();
});

checkService();
loadLatest();
