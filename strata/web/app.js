"use strict";

// ---- auth: the dashboard link carries a token in the #fragment (never sent to the network).
let TOKEN = "";
try { TOKEN = localStorage.getItem("strata-token") || ""; } catch (e) {}
const m = location.hash.match(/t=([0-9a-f]+)/);
if (m) {
  TOKEN = m[1];
  try { localStorage.setItem("strata-token", TOKEN); } catch (e) {}
  history.replaceState(null, "", location.pathname);
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const gb = b => (b / 1024 ** 3).toFixed(1) + " GB";

async function api(method, path, body) {
  const r = await fetch("/api/" + path, {
    method, headers: { "X-Strata-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Request failed");
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 7000 : 3000);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); refresh(); return r; } catch (e) { toast(e.message, true); }
}

// ---- state
let STATE = null;
let selected = null;    // VM name, or "new"
let rendered = null;    // what the main area currently shows
const terms = {};       // per-VM terminal history

const STATE_TEXT = { running: "Running", stopped: "Stopped", installing: "Installing Windows", starting: "Starting Windows", creating: "Preparing" };

async function refresh() {
  try { STATE = await api("GET", "state"); }
  catch (e) {
    $("#main").innerHTML = `<div class="center"><div><h1>Can't reach Strata</h1><p class="muted">${esc(e.message)}</p>
      <p>Open the dashboard from Terminal with <code>strata dashboard</code>.</p></div></div>`;
    rendered = null; return;
  }
  const names = [...STATE.vms.map(v => v.name), ...Object.keys(STATE.jobs)];
  if (!selected || (selected !== "new" && !names.includes(selected))) selected = names[0] || "new";
  renderSidebar();
  renderMain();
}

function renderSidebar() {
  const items = STATE.vms.map(v => ({ name: v.name, state: v.state }))
    .concat(Object.keys(STATE.jobs).filter(n => !STATE.vms.some(v => v.name === n)).map(n => ({ name: n, state: "creating" })));
  $("#vm-list").innerHTML = items.map(v => `<a data-name="${esc(v.name)}" class="${v.name === selected ? "active" : ""}">
      <span class="dot ${v.state}"></span>${esc(v.name)}<small>${STATE_TEXT[v.state] === "Running" ? "" : esc(STATE_TEXT[v.state] || "")}</small></a>`).join("")
    || `<div class="side-label" style="text-transform:none">No PCs yet</div>`;
  const h = STATE.host;
  const missing = STATE.doctor.filter(d => !d.ok && !d.hint.startsWith("Optional"));
  $("#host-info").innerHTML = (missing.length ? `<div style="color:var(--red)">⚠ Missing: ${missing.map(d => esc(d.check)).join(", ")}<br>${missing.map(d => esc(d.hint)).join("<br>")}</div><br>` : "")
    + `This Mac: ${h.cpus} cores · ${h.memory_gb} GB<br>${gb(h.free_disk)} free · Strata ${esc(STATE.version)}`;
}
$("#vm-list").addEventListener("click", e => {
  const a = e.target.closest("a[data-name]"); if (a) { selected = a.dataset.name; renderSidebar(); renderMain(); }
});
$("#new-btn").addEventListener("click", () => { selected = "new"; renderSidebar(); renderMain(); });

function renderMain() {
  const main = $("#main");
  const job = STATE.jobs[selected];
  const vm = STATE.vms.find(v => v.name === selected);
  if (selected === "new") { if (rendered !== "new") { main.replaceChildren(buildNew()); rendered = "new"; } return; }
  if (job && !vm) { main.innerHTML = renderJob(selected, job); rendered = "job:" + selected; return; }
  if (!vm) return;
  if (rendered !== "vm:" + vm.name) { main.replaceChildren(buildVm(vm)); rendered = "vm:" + vm.name; }
  updateVm(vm);
}

// ---- creation progress
function renderJob(name, j) {
  const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
  const stages = ["Asking Microsoft for Windows 11", "Downloading Windows 11 from Microsoft", "Downloading Windows drivers", "Preparing Windows setup disk"];
  const at = stages.indexOf(j.stage);
  const detail = j.total ? `${pct}% · ${gb(j.done)} of ${gb(j.total)}` : "";
  return `<section class="page narrow"><h1>Setting up “${esc(name)}”</h1>
    ${j.error ? `<div class="note err" style="margin-top:16px">${esc(j.error)}</div>
      <button class="btn" onclick="api('POST','jobs/${encodeURIComponent(name)}/dismiss').then(refresh)">Dismiss</button>`
      : `<p class="lead">${esc(j.stage)}… ${detail}</p>
      <div class="progress ${j.total ? "" : "indeterminate"}"><div style="width:${pct}%"></div></div>
      <p class="fineprint">You can close this page; setup continues in the background. Downloads resume if interrupted.</p>
      <ul class="steps">${stages.map((s, i) => `<li class="${i < at ? "done" : i === at ? "now" : ""}">${i < at ? "✓" : "•"} ${s}</li>`).join("")}
      <li>• Windows installs itself in its own window (about 15–30 minutes)</li></ul>`}</section>`;
}

// ---- new VM form
function bindRange(form, name, suffix) {
  const input = form.elements[name], out = $(`.val[data-for="${name}"]`, form);
  const upd = () => (out.textContent = input.value + suffix);
  input.addEventListener("input", upd); upd();
}
async function chooseFolder(input) {
  const r = await act(() => api("POST", "pick-folder"));
  if (r && r.path) input.value = r.path;
}

function buildNew() {
  const node = $("#tpl-new").content.cloneNode(true).firstElementChild;
  const f = $("#new-form", node), h = STATE.host;
  f.cpus.max = h.cpus; f.cpus.value = h.default_cpus;
  f.memory_gb.max = Math.max(4, h.memory_gb - 2); f.memory_gb.value = h.default_memory_gb;
  bindRange(f, "cpus", " cores"); bindRange(f, "memory_gb", " GB");
  let n = "windows", i = 2;
  while (STATE.vms.some(v => v.name === n)) n = "windows" + i++;
  f.name.value = n;
  $("#pick-share", node).onclick = () => chooseFolder(f.share);
  $("#clear-share", node).onclick = () => (f.share.value = "");
  f.addEventListener("submit", async e => {
    e.preventDefault();
    const body = { name: f.name.value.trim(), cpus: +f.cpus.value, memory_gb: +f.memory_gb.value, disk_gb: +f.disk_gb.value,
      share: f.share.value || null, username: f.username.value.trim(), password: f.password.value, start: true };
    const r = await act(() => api("POST", "vms", body));
    if (r) { selected = r.name; rendered = null; refresh(); }
  });
  return node;
}

// ---- VM page
function buildVm(vm) {
  const node = $("#tpl-vm").content.cloneNode(true).firstElementChild;
  const name = vm.name, enc = encodeURIComponent(name);
  $('[data-f="name"]', node).textContent = name;
  $('[data-f="name2"]', node).textContent = name;

  $$(".tabs button", node).forEach(b => b.onclick = () => {
    $$(".tabs button", node).forEach(x => x.classList.toggle("active", x === b));
    $$(".tab", node).forEach(p => (p.hidden = p.dataset.pane !== b.dataset.tab));
    if (b.dataset.tab === "snapshots") loadSnaps(node, name);
    if (b.dataset.tab === "terminal") $(".term-in input", node).focus();
  });

  // quick actions
  const open = target => act(() => api("POST", `vms/${enc}/open`, { target }), "Opened in Windows");
  node.addEventListener("click", e => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const a = b.dataset.act;
    if (a === "rdp") act(() => api("POST", `vms/${enc}/rdp`), "Opening in Windows App…");
    if (a === "open-explorer") open("explorer.exe");
    if (a === "open-share") open("Z:\\");
    if (a === "reveal") act(() => api("POST", `vms/${enc}/reveal`));
    if (a === "term-clear") { terms[name].lines = []; drawTerm(node, name); }
    if (a === "pick") chooseFolder($(".settings-form", node).share);
    if (a === "unshare") $(".settings-form", node).share.value = "";
    if (a === "delete") {
      const typed = prompt(`Type “${name}” to permanently delete this PC and everything in it.`);
      if (typed === name) act(() => api("POST", `vms/${enc}/delete`), "Deleted").then(() => { selected = null; rendered = null; });
    }
  });
  $(".open-form", node).addEventListener("submit", e => {
    e.preventDefault(); const t = e.target.target.value.trim(); if (t) open(t);
  });

  // terminal
  terms[name] ||= { shell: "cmd", cwd: "", lines: [], history: [], hi: 0 };
  const T = terms[name];
  $$(".seg button", node).forEach(b => {
    b.classList.toggle("active", b.dataset.shell === T.shell);
    b.onclick = () => { T.shell = b.dataset.shell; $$(".seg button", node).forEach(x => x.classList.toggle("active", x === b)); drawTerm(node, name); };
  });
  const tin = $(".term-in input", node);
  $(".term-in", node).addEventListener("submit", async e => {
    e.preventDefault();
    const cmd = tin.value; if (!cmd.trim()) return;
    tin.value = ""; T.history.push(cmd); T.hi = T.history.length;
    if (["cls", "clear"].includes(cmd.trim().toLowerCase())) { T.lines = []; drawTerm(node, name); return; }
    T.lines.push({ c: "cmd", t: promptText(T) + cmd + "\n" });
    T.lines.push({ c: "dim", t: "…\n" }); drawTerm(node, name);
    try {
      const r = await api("POST", `vms/${enc}/run`, { cmd, shell: T.shell, cwd: T.cwd });
      T.lines.pop();
      if (r.stdout) T.lines.push({ c: "", t: r.stdout });
      if (r.stderr) T.lines.push({ c: "err", t: r.stderr });
      if (r.timed_out) T.lines.push({ c: "err", t: "(stopped: took longer than 2 minutes)\n" });
      if (r.cwd) T.cwd = r.cwd;
    } catch (err) { T.lines.pop(); T.lines.push({ c: "err", t: err.message + "\n" }); }
    drawTerm(node, name);
  });
  tin.addEventListener("keydown", e => {
    if (e.key === "ArrowUp" && T.hi > 0) { tin.value = T.history[--T.hi]; e.preventDefault(); }
    if (e.key === "ArrowDown") { T.hi = Math.min(T.history.length, T.hi + 1); tin.value = T.history[T.hi] || ""; e.preventDefault(); }
  });
  drawTerm(node, name);

  // snapshots
  $(".snap-form", node).addEventListener("submit", async e => {
    e.preventDefault();
    const tag = e.target.tag.value.trim();
    toast("Taking snapshot… this can take a minute.");
    await act(() => api("POST", `vms/${enc}/snapshots`, { tag }), "Snapshot saved");
    e.target.reset(); loadSnaps(node, name);
  });
  $('[data-f="snaps"]', node).addEventListener("click", async e => {
    const b = e.target.closest("button[data-snap]"); if (!b) return;
    const tag = b.dataset.snap, path = `vms/${enc}/snapshots/${encodeURIComponent(tag)}`;
    if (b.dataset.do === "restore" && confirm(`Restore “${tag}”? Anything since that snapshot will be lost.`)) {
      toast("Restoring…"); await act(() => api("POST", path + "/restore"), "Restored");
    }
    if (b.dataset.do === "delete" && confirm(`Delete snapshot “${tag}”?`)) await act(() => api("DELETE", path), "Snapshot deleted");
    loadSnaps(node, name);
  });

  // settings
  const sf = $(".settings-form", node), h = STATE.host;
  sf.cpus.max = h.cpus; sf.cpus.value = vm.cpus;
  sf.memory_gb.max = Math.max(4, h.memory_gb - 2); sf.memory_gb.value = vm.memory_gb;
  sf.share.value = vm.share || ""; sf.clipboard.checked = vm.clipboard; sf.hd.checked = vm.display === "hd";
  bindRange(sf, "cpus", " cores"); bindRange(sf, "memory_gb", " GB");
  sf.addEventListener("submit", async e => {
    e.preventDefault();
    const r = await act(() => api("POST", `vms/${enc}/settings`, { cpus: +sf.cpus.value, memory_gb: +sf.memory_gb.value,
      share: sf.share.value || null, clipboard: sf.clipboard.checked, display: sf.hd.checked ? "hd" : "basic" }));
    if (r) toast(r.restart_needed ? "Saved. Shut down and start again to apply." : "Saved");
  });
  return node;
}

function promptText(T) { return T.shell === "powershell" ? `PS ${T.cwd || "C:\\"}> ` : `${T.cwd || "C:\\"}>`; }
function drawTerm(node, name) {
  const T = terms[name], out = $('[data-f="term-out"]', node);
  out.innerHTML = T.lines.length ? T.lines.map(l => `<span class="${l.c}">${esc(l.t)}</span>`).join("")
    : `<span class="dim">Windows ${T.shell === "cmd" ? "Command Prompt" : "PowerShell"}. Commands run inside Windows as ${esc(STATE.vms.find(v => v.name === name)?.username || "your user")}.\n</span>`;
  out.scrollTop = out.scrollHeight;
  $('[data-f="prompt"]', node).textContent = promptText(T);
}

async function loadSnaps(node, name) {
  const box = $('[data-f="snaps"]', node);
  try {
    const { snapshots } = await api("GET", `vms/${encodeURIComponent(name)}/snapshots`);
    box.innerHTML = snapshots.length ? snapshots.map(s => `<div class="snap"><div><div class="name">${esc(s.tag)}</div>
      <div class="date">${esc(s.date)}${s.size && s.size !== "0 B" ? " · includes open apps" : ""}</div></div>
      <div class="btns"><button class="btn small" data-snap="${esc(s.tag)}" data-do="restore">Restore</button>
      <button class="btn small ghost" data-snap="${esc(s.tag)}" data-do="delete">Delete</button></div></div>`).join("")
      : `<p class="muted">No snapshots yet.</p>`;
  } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

function updateVm(vm) {
  const node = $("#main"), enc = encodeURIComponent(vm.name);
  const pill = $('[data-f="state"]', node);
  pill.textContent = STATE_TEXT[vm.state]; pill.className = "pill " + vm.state;
  const on = vm.state !== "stopped";
  const acts = $('[data-f="actions"]', node);
  const key = vm.state;
  if (acts.dataset.key !== key) {
    acts.dataset.key = key;
    acts.innerHTML = on
      ? `<button class="btn" data-x="stop">Shut Down</button><button class="btn ghost" data-x="force">Force Off</button>`
      : `<button class="btn primary big" data-x="start">▶ Start</button>`;
    acts.onclick = e => {
      const x = e.target.dataset.x;
      if (x === "start") act(() => api("POST", `vms/${enc}/start`), "Starting… the Windows window will open");
      if (x === "stop") act(() => api("POST", `vms/${enc}/stop`), "Asking Windows to shut down…");
      if (x === "force" && confirm("Switch off immediately? Unsaved work in Windows will be lost."))
        act(() => api("POST", `vms/${enc}/stop`, { force: true }), "Turned off");
    };
  }
  const note = $('[data-f="note"]', node);
  const notes = {
    installing: "Windows is installing itself in its own window. It restarts a few times. This takes about 15–30 minutes and needs no clicks. Terminal, clipboard and shared folders turn on when it finishes.",
    starting: "Windows is starting up…",
  };
  note.hidden = !notes[vm.state]; note.textContent = notes[vm.state] || "";
  $('[data-f="cpus"]', node).textContent = vm.cpus + " cores";
  $('[data-f="mem"]', node).textContent = vm.memory_gb + " GB";
  $('[data-f="disk"]', node).textContent = gb(vm.disk_used) + " / " + vm.disk_gb + " GB";
  $('[data-f="share"]', node).textContent = vm.share ? vm.share.replace(/^\/Users\/[^/]+/, "~") + (vm.share_mapped ? "  → Z:" : "") : "None";
  $('[data-f="clip"]', node).textContent = vm.clipboard ? (vm.state === "running" ? "Synced" : "On") : "Off";
  $('[data-f="agent"]', node).textContent = vm.state === "running" ? "Connected" : vm.state === "stopped" ? "—" : "Waiting for Windows…";
  $('[data-f="display"]', node).textContent = vm.display === "hd" ? "High resolution" : "Basic";
  $$("[data-needs=running]", node).forEach(b => (b.disabled = vm.state !== "running"));
  $$("[data-needs-form=running] input, [data-needs-form=running] button", node).forEach(b => (b.disabled = vm.state !== "running"));
  $(".term-in input", node).disabled = vm.state !== "running";
}

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 2000);
