"use strict";

let TOKEN = "";
try { TOKEN = localStorage.getItem("strata-token") || ""; } catch (e) {}
const tm = location.hash.match(/t=([0-9a-f]+)/);
if (tm) {
  TOKEN = tm[1];
  try { localStorage.setItem("strata-token", TOKEN); } catch (e) {}
  history.replaceState(null, "", location.pathname);
}

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mb = b => (b / 1e6).toFixed(0) + " MB";

async function api(method, path, body) {
  const r = await fetch("/api/" + path, { method, headers: { "X-Strata-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || "Request failed");
  return d;
}
let toastT;
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), err ? 7000 : 3000);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); refresh(); return r; } catch (e) { toast(e.message, true); }
}

let S = null, view = "library", lastHtml = "";

async function refresh() {
  try { S = await api("GET", "state"); }
  catch (e) { $("#main").innerHTML = `<div class="center"><div><h1>Can't reach Strata</h1><p class="muted">${esc(e.message)}</p><p>Run <code>strata dashboard</code> in Terminal.</p></div></div>`; lastHtml = ""; return; }
  $("#mac-info").innerHTML = `${esc(S.mac.chip)} · ${S.mac.memory_gb} GB<br>Strata ${esc(S.version)}`;
  const html = render();
  if (html !== lastHtml) { $("#main").innerHTML = html; lastHtml = html; }
}

function jobsHtml() {
  return Object.entries(S.jobs).map(([k, j]) => {
    const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
    return `<div class="card job">${j.error
      ? `<b>${esc(j.label)} failed</b><p class="muted">${esc(j.error)}</p><button class="btn small" data-dismiss="${esc(k)}">Dismiss</button>`
      : `<b>${esc(j.label)}</b><div class="muted">${esc(j.stage)}${j.total ? ` · ${pct}% (${mb(j.done)} / ${mb(j.total)})` : "…"}</div>
         <div class="progress ${j.total ? "" : "ind"}"><div style="width:${pct}%"></div></div>`}</div>`;
  }).join("");
}

function render() {
  const bad = S.doctor.filter(d => !d.ok && !d.check.startsWith("Game engine"));
  const warn = bad.length ? `<div class="card warn">⚠️ ${bad.map(d => `<b>${esc(d.check)}</b> missing. ${esc(d.hint)}`).join("<br>")}</div>` : "";
  if (!S.ready) {
    return `<section class="page narrow hero">${warn}<img src="icon.svg" class="hero-icon" alt="">
      <h1>Welcome to Strata</h1><p class="lead">Play Windows games on your Mac using Apple's Game Porting Toolkit. It translates DirectX 11 and 12 to Metal, so games run on your Mac's GPU. No Windows needed.</p>
      ${S.jobs.setup ? jobsHtml() : `<button class="btn primary big" data-act="setup">Set up Strata</button>
      <p class="fine">Downloads the game engine once (about 240 MB) and creates a Windows environment. Takes a few minutes.</p>`}</section>`;
  }
  if (view === "stores") return storesHtml(warn);
  if (view === "help") return helpHtml();
  return libraryHtml(warn);
}

function libraryHtml(warn) {
  const cards = S.games.map(g => `<div class="game">
      <div class="cover" style="${g.cover ? `background-image:url('${esc(g.cover)}')` : ""}">${g.cover ? "" : `<span>${esc(g.name.slice(0, 1))}</span>`}</div>
      <div class="gbody"><div class="gname" title="${esc(g.name)}">${esc(g.name)}</div><div class="gsrc">${esc(g.source)}</div>
      <div class="gacts"><button class="btn primary" data-play="${esc(g.id)}">▶ Play</button>
      <button class="btn icon" title="Settings" data-settings="${esc(g.id)}">⚙︎</button>
      <button class="btn icon" title="${g.dock ? "In Dock" : "Add to Dock"}" data-dock="${esc(g.id)}">${g.dock ? "✓" : "⬇︎"}</button></div></div></div>`).join("");
  return `<section class="page">${warn}${jobsHtml()}
    <div class="head"><h1>Library</h1><div class="row">
      <button class="btn" data-act="run">Run installer (.exe)</button>
      <button class="btn primary" data-act="add">＋ Add game</button></div></div>
    ${S.games.length ? `<div class="grid">${cards}</div>` : `<div class="card empty"><h3>No games yet</h3>
      <p class="muted">Install Steam, Epic or GOG from <a data-view="stores">Stores</a> and download games there; they show up here automatically.
      Or run a game installer you downloaded, then click <b>Add game</b> and choose its .exe.</p></div>`}</section>`;
}

function storesHtml(warn) {
  const logos = { steam: "🟦", epic: "⬛", gog: "🟪" };
  return `<section class="page">${warn}${jobsHtml()}<div class="head"><h1>Stores</h1></div>
    <p class="lead">Nothing downloads until you click Install. Games with kernel anti-cheat (Fortnite, Valorant…) can't run on a Mac this way.</p>
    <div class="stores">${S.stores.map(s => `<div class="card store"><div class="slogo">${logos[s.key]}</div>
      <div><h3>${esc(s.name)}</h3><p class="muted">${esc(s.about)}</p></div>
      ${s.installed ? `<button class="btn primary" data-open="${s.key}">Open</button>`
        : S.jobs[s.key] && !S.jobs[s.key].error ? `<button class="btn" disabled>Installing…</button>`
        : `<button class="btn" data-install="${s.key}">Install</button>`}</div>`).join("")}
    <div class="card store"><div class="slogo">📦</div><div><h3>Any Windows game</h3>
      <p class="muted">itch.io, game websites, discs. Run its installer, then add the game's .exe to your library.</p></div>
      <button class="btn" data-act="run">Run installer</button></div></div>
    <p class="fine"><a data-act="folder">Open the Windows C: drive in Finder</a></p></section>`;
}

function helpHtml() {
  return `<section class="page narrow"><h1>Help</h1><div class="card help">
    <h3>A game won't start</h3><p>Open its ⚙︎ settings and try turning <b>AVX support</b> or <b>ESync</b> off. Logs are in <code>~/.strata/logs</code>.</p>
    <h3>Low FPS</h3><p>Keep <b>Retina</b> off, lower the in-game resolution, and turn on <b>MetalFX</b> for games that support DLSS. Turn on the <b>FPS overlay</b> to measure.</p>
    <h3>Steam shows a black window</h3><p>Close everything with “Close all games” and open Steam again. The first start can take a minute.</p>
    <h3>Controllers</h3><p>Connect Xbox, PlayStation or most USB/Bluetooth controllers in macOS first; games see them automatically.</p>
    <h3>Terminal</h3><p><code>strata games</code>, <code>strata play "game name"</code>, <code>strata settings "game" hud=on</code>, <code>strata --help</code></p>
    </div></section>`;
}

function settingsModal(id) {
  const g = S.games.find(x => x.id === id); if (!g) return;
  const m = $("#modal");
  m.innerHTML = `<div class="sheet"><h2>${esc(g.name)}</h2><p class="muted">Settings apply the next time you press Play.</p>
    ${Object.entries(S.settings).map(([k, v]) => `<label class="sw"><div><b>${esc(v.label)}</b><small>${esc(v.help)}</small></div>
      <input type="checkbox" data-k="${k}" ${g.settings[k] ? "checked" : ""}><span class="track"></span></label>`).join("")}
    <div class="row end"><button class="btn red ghost" data-remove="${esc(g.id)}">Remove from library</button><button class="btn primary" data-close>Done</button></div></div>`;
  m.hidden = false;
  m.onchange = e => { const k = e.target.dataset.k; if (k) act(() => api("POST", `games/${encodeURIComponent(id)}/settings`, { [k]: e.target.checked })); };
}

document.addEventListener("click", e => {
  const t = e.target.closest("[data-view],[data-act],[data-play],[data-settings],[data-dock],[data-install],[data-open],[data-dismiss],[data-remove],[data-close],#stop-all");
  if (!t) { if (e.target.id === "modal") $("#modal").hidden = true; return; }
  const d = t.dataset;
  if (d.view) { view = d.view; document.querySelectorAll("nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view)); lastHtml = ""; refresh(); }
  if (d.act === "setup") act(() => api("POST", "setup"));
  if (d.act === "add") act(() => api("POST", "add")).then(r => r && r.id && toast("Game added"));
  if (d.act === "run") act(() => api("POST", "run")).then(r => r && r.ok && toast("Installer started. Add the game's .exe when it finishes."));
  if (d.act === "folder") act(() => api("POST", "open-folder"));
  if (d.play) act(() => api("POST", `games/${encodeURIComponent(d.play)}/play`), "Starting… the first launch can take a minute");
  if (d.settings) settingsModal(d.settings);
  if (d.dock) act(() => api("POST", `games/${encodeURIComponent(d.dock)}/dock`), "Added to your Dock");
  if (d.install) act(() => api("POST", `stores/${d.install}/install`));
  if (d.open) act(() => api("POST", `stores/${d.open}/open`), "Opening… the first start can take a minute");
  if (d.dismiss) act(() => api("POST", `jobs/${d.dismiss}/dismiss`));
  if (d.remove && confirm("Remove this game from your library? (Store games stay installed.)")) { $("#modal").hidden = true; act(() => api("POST", `games/${encodeURIComponent(d.remove)}/remove`)); }
  if ("close" in d) $("#modal").hidden = true;
  if (t.id === "stop-all") act(() => api("POST", "stop"), "Closed all games");
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 2000);
