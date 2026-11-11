"""The single-page review UI served by webui/review.py. No external requests."""

REVIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Review incoming changes — contextzip</title>
<style>
:root {
  --bg: #0b0d12; --panel: #10131a; --panel-2: #151922; --line: rgba(255,255,255,.08); --line-2: rgba(255,255,255,.15);
  --fg: #eef1f5; --muted: #9aa3b2; --dim: #6b7383;
  --signal: #5ee0b5; --signal-ink: #062a1f; --beam: #8ab4ff; --amber: #f2c46d; --red: #ff8a7a; --green: #7ee2a0;
  --add-bg: rgba(126,226,160,.10); --add-hl: rgba(126,226,160,.28); --del-bg: rgba(255,138,122,.10); --del-hl: rgba(255,138,122,.30);
  --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f7f8fa; --panel: #ffffff; --panel-2: #f1f3f6; --line: rgba(15,20,30,.10); --line-2: rgba(15,20,30,.18);
    --fg: #141821; --muted: #5a6273; --dim: #8a92a2;
    --signal: #0f8a65; --signal-ink: #ffffff; --beam: #2f5fd0; --amber: #9a6a00; --red: #c2402e; --green: #1d7a45;
    --add-bg: rgba(29,122,69,.08); --add-hl: rgba(29,122,69,.22); --del-bg: rgba(194,64,46,.08); --del-hl: rgba(194,64,46,.22);
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--fg); font: 14px/1.5 var(--sans); -webkit-font-smoothing: antialiased; }
button { font: inherit; color: inherit; cursor: pointer; }
button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
.app { display: grid; grid-template-rows: 60px 1fr; height: 100vh; }
header { display: flex; align-items: center; gap: 16px; padding: 0 20px; border-bottom: 1px solid var(--line); background: color-mix(in oklab, var(--bg) 85%, transparent); }
.mark { width: 28px; height: 28px; border-radius: 8px; border: 1px solid var(--line-2); background: var(--panel-2); display: grid; place-items: center; }
.title { font-weight: 600; letter-spacing: -.01em; }
.sub { color: var(--muted); font: 12px var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.grow { flex: 1; min-width: 0; }
.pill { font: 11px var(--mono); padding: 3px 9px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
.pill.warn { color: var(--amber); border-color: color-mix(in oklab, var(--amber) 40%, transparent); }
.btn { height: 34px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--line-2); background: transparent; font-size: 13px; font-weight: 500; }
.btn:hover { background: var(--panel-2); }
.btn.primary { background: var(--signal); color: var(--signal-ink); border-color: transparent; box-shadow: 0 6px 24px -10px var(--signal); }
.btn.primary:hover { filter: brightness(1.06); }
.btn:disabled { opacity: .45; cursor: default; }
.body { display: grid; grid-template-columns: 320px 1fr; min-height: 0; }
aside { border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; background: var(--panel); }
.search { padding: 12px; border-bottom: 1px solid var(--line); }
.search input { width: 100%; height: 34px; border-radius: 10px; border: 1px solid var(--line); background: var(--bg); color: var(--fg); padding: 0 12px; font-size: 13px; }
.list { overflow: auto; padding: 8px; flex: 1; }
.group { margin: 10px 4px 6px; font: 10.5px var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--dim); display: flex; justify-content: space-between; }
.item { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; padding: 8px 10px; border-radius: 10px; border: 1px solid transparent; background: transparent; }
.item:hover { background: var(--panel-2); }
.item.active { background: var(--panel-2); border-color: var(--line-2); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.names { min-width: 0; flex: 1; }
.fname { font: 12.5px var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fdir { font: 11px var(--mono); color: var(--dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tag { font: 10.5px var(--mono); padding: 2px 7px; border-radius: 999px; background: var(--panel-2); color: var(--muted); white-space: nowrap; }
.tag.ok { color: var(--signal); background: color-mix(in oklab, var(--signal) 14%, transparent); }
.tag.pending { color: var(--amber); background: color-mix(in oklab, var(--amber) 14%, transparent); }
main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.filehead { padding: 18px 24px 14px; border-bottom: 1px solid var(--line); }
.path { font: 15px var(--mono); word-break: break-all; }
.path .d { color: var(--dim); }
.meta { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: var(--muted); font-size: 13px; }
.chip { font: 11px var(--mono); padding: 2px 9px; border-radius: 999px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 24px; border-bottom: 1px solid var(--line); }
.seg { display: inline-flex; padding: 3px; gap: 3px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel); }
.seg button { border: 0; background: transparent; padding: 6px 12px; border-radius: 999px; font-size: 12.5px; color: var(--muted); }
.seg button.on { background: var(--panel-2); color: var(--fg); box-shadow: inset 0 0 0 1px var(--line-2); }
.seg button.on.choice { background: color-mix(in oklab, var(--signal) 16%, transparent); color: var(--signal); box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--signal) 40%, transparent); }
.spacer { flex: 1; }
.view { flex: 1; overflow: auto; min-height: 0; }
.notice { margin: 16px 24px; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--line); background: var(--panel); color: var(--muted); font-size: 13px; }
.notice.warn { border-color: color-mix(in oklab, var(--amber) 35%, transparent); color: var(--fg); background: color-mix(in oklab, var(--amber) 7%, var(--panel)); }
table.diff { width: 100%; border-collapse: collapse; table-layout: fixed; font: 12.5px/1.65 var(--mono); }
.diff col.n { width: 52px; } .diff col.c { width: calc(50% - 52px); }
.diff th { position: sticky; top: 0; z-index: 1; background: var(--panel); text-align: left; font: 10.5px var(--mono); text-transform: uppercase; letter-spacing: .12em; color: var(--dim); padding: 8px 12px; border-bottom: 1px solid var(--line); }
.diff td { vertical-align: top; padding: 0 12px; white-space: pre-wrap; word-break: break-all; }
.diff td.n { color: var(--dim); text-align: right; user-select: none; padding: 0 8px; border-right: 1px solid var(--line); }
.diff td.c + td.n { border-left: 1px solid var(--line); }
.diff tr.del td.l, .diff tr.mod td.l { background: var(--del-bg); }
.diff tr.add td.r, .diff tr.mod td.r { background: var(--add-bg); }
.diff mark.d { background: var(--del-hl); color: inherit; border-radius: 3px; }
.diff mark.a { background: var(--add-hl); color: inherit; border-radius: 3px; }
.diff tr.fold td { background: var(--panel-2); color: var(--dim); text-align: center; font-size: 11.5px; padding: 3px; }
.diff td.empty { background: repeating-linear-gradient(135deg, transparent 0 6px, var(--panel-2) 6px 7px); }
.edit { display: flex; flex-direction: column; height: 100%; }
.edit textarea { flex: 1; width: 100%; border: 0; resize: none; padding: 16px 24px; background: var(--bg); color: var(--fg); font: 12.5px/1.65 var(--mono); tab-size: 2; }
.editbar { display: flex; gap: 10px; align-items: center; padding: 10px 24px; border-top: 1px solid var(--line); background: var(--panel); font-size: 12.5px; color: var(--muted); }
.cands { padding: 16px 24px; display: grid; gap: 8px; max-width: 760px; }
.cand { display: flex; gap: 10px; align-items: center; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--line); background: var(--panel); text-align: left; font: 12.5px var(--mono); }
.cand.on { border-color: color-mix(in oklab, var(--signal) 50%, transparent); background: color-mix(in oklab, var(--signal) 8%, var(--panel)); }
footer.nav { display: flex; gap: 8px; align-items: center; padding: 10px 24px; border-top: 1px solid var(--line); color: var(--dim); font-size: 12px; }
.kbd { font: 10.5px var(--mono); border: 1px solid var(--line-2); border-radius: 5px; padding: 1px 5px; background: var(--panel-2); color: var(--muted); }
.empty-state { display: grid; place-items: center; height: 100%; color: var(--muted); text-align: center; padding: 40px; }
.done { position: fixed; inset: 0; display: none; place-items: center; background: color-mix(in oklab, var(--bg) 92%, transparent); backdrop-filter: blur(6px); z-index: 10; }
.done.show { display: grid; }
.done .card { text-align: center; padding: 36px 40px; border-radius: 18px; border: 1px solid var(--line-2); background: var(--panel); max-width: 440px; }
.done h2 { margin: 12px 0 6px; letter-spacing: -.02em; }
@media (max-width: 860px) { .body { grid-template-columns: 1fr; } aside { max-height: 38vh; border-right: 0; border-bottom: 1px solid var(--line); } }
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="mark" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 32 32" fill="none"><path d="M7 9h18" stroke="currentColor" stroke-opacity=".45" stroke-width="2.4" stroke-linecap="round"/><path d="M7 15h18" stroke="currentColor" stroke-opacity=".75" stroke-width="2.4" stroke-linecap="round"/><path d="M10 20l6 5 6-5" stroke="var(--signal)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </div>
    <div class="grow">
      <div class="title">Review incoming changes</div>
      <div class="sub" id="sub">Loading…</div>
    </div>
    <span class="pill" id="pending" hidden></span>
    <button class="btn" id="cancel">Cancel</button>
    <button class="btn primary" id="apply" disabled>Apply</button>
  </header>
  <div class="body">
    <aside>
      <div class="search"><input id="q" placeholder="Filter files…" aria-label="Filter files" autocomplete="off"></div>
      <div class="list" id="list" role="listbox" aria-label="Files"></div>
    </aside>
    <main id="main"><div class="empty-state">Loading…</div></main>
  </div>
</div>
<div class="done" id="done" role="dialog" aria-modal="true">
  <div class="card">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="11" stroke="var(--signal)" stroke-width="1.5"/><path d="M7 12.5l3.2 3.2L17 9" stroke="var(--signal)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <h2 id="doneTitle">Sent to your terminal</h2>
    <p class="sub" style="white-space:normal" id="doneText">contextzip is applying your decisions. You can close this tab.</p>
  </div>
</div>
<script>
(() => {
  const TOKEN = "__TOKEN__";
  const H = { "X-Contextzip-Token": TOKEN };
  const STATUS = {
    new: ["New", "var(--green)"], modified: ["Modified", "var(--beam)"], merged: ["Merged", "var(--beam)"],
    unchanged: ["Unchanged", "var(--dim)"], conflict: ["Conflict", "var(--red)"], drifted: ["Drifted", "var(--amber)"],
    deleted: ["Deleted locally", "var(--amber)"], untracked: ["Untracked", "var(--amber)"], partial: ["Partial file", "var(--amber)"],
    ambiguous: ["Ambiguous", "var(--amber)"], moved: ["Moved", "var(--beam)"],
    "delete-requested": ["Delete requested", "var(--amber)"],
  };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let plan = null, current = null, filter = "";
  const decisions = {};   // id -> "apply" | "theirs" | "keep" | {action, content?, target?}
  const view = {};        // id -> {left, right, mode: "diff"|"edit"}
  const drafts = {};      // id -> edited text

  const api = (path) => fetch(path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN), { headers: H }).then((r) => r.json());

  function decisionLabel(e) {
    const d = decisions[e.id];
    if (e.status === "unchanged") return ["up to date", ""];
    if (d === undefined) return ["needs a call", "pending"];
    if (d === "keep" || d === "skip") return [e.status === "deleted" ? "stays deleted" : e.status === "delete-requested" ? "keep it" : "keep mine", ""];
    if (d === "delete") return ["delete", "ok"];
    if (d && d.remove_old) return ["move", "ok"];
    if (d === "theirs") return ["take AI's", "ok"];
    if (d === "apply") return [e.status === "new" ? "add" : "apply", "ok"];
    if (d.action === "custom") return ["edited", "ok"];
    if (d.action === "apply") return ["→ " + d.target.split("/").pop(), "ok"];
    return ["", ""];
  }

  function writes(e) {
    const d = decisions[e.id];
    return d !== undefined && d !== "keep" && d !== "skip";
  }

  function refreshHeader() {
    const n = plan.entries.filter(writes).length;
    const pending = plan.entries.filter((e) => e.risky && decisions[e.id] === undefined).length;
    $("#apply").disabled = false;
    $("#apply").textContent = n ? `Apply ${n} change${n === 1 ? "" : "s"}` : "Done — write nothing";
    const p = $("#pending");
    p.hidden = !pending;
    p.className = "pill warn";
    p.textContent = `${pending} undecided — left as they are`;
  }

  function groups() {
    const q = filter.toLowerCase();
    const match = (e) => !q || e.id.toLowerCase().includes(q);
    const es = plan.entries.filter(match);
    return [
      ["Needs your call", es.filter((e) => e.risky)],
      ["Ready to apply", es.filter((e) => e.safe)],
      ["Already up to date", es.filter((e) => e.status === "unchanged")],
    ].filter(([, list]) => list.length);
  }

  function renderList() {
    const el = $("#list");
    el.innerHTML = "";
    for (const [title, list] of groups()) {
      const g = document.createElement("div");
      g.className = "group";
      g.innerHTML = `<span>${esc(title)}</span><span>${list.length}</span>`;
      el.appendChild(g);
      for (const e of list) {
        const b = document.createElement("button");
        b.className = "item" + (current && current.id === e.id ? " active" : "");
        b.setAttribute("role", "option");
        b.setAttribute("aria-selected", current && current.id === e.id ? "true" : "false");
        const parts = e.id.split("/");
        const name = parts.pop();
        const [label, kind] = decisionLabel(e);
        b.innerHTML = `<span class="dot" style="background:${STATUS[e.status][1]}"></span>
          <span class="names"><div class="fname">${esc(name)}</div><div class="fdir">${esc(parts.join("/") || "/")}</div></span>
          ${label ? `<span class="tag ${kind}">${esc(label)}</span>` : ""}`;
        b.onclick = () => select(e);
        el.appendChild(b);
      }
    }
  }

  function choices(e) {
    switch (e.status) {
      case "new": return [["apply", "Add file"], ["skip", "Skip"]];
      case "modified": case "merged": return [["apply", "Apply"], ["keep", "Keep mine"]];
      case "moved": return [["apply", "Add at new path"], ["skip", "Skip"]];
      case "delete-requested": return [["keep", "Keep it"], ["delete", "Delete it"]];
      case "conflict": return [["keep", "Keep mine"], ["theirs", "Take AI's"], ["edit", "Resolve by hand"]];
      case "deleted": return [["keep", "Keep it deleted"], ["theirs", "Restore AI's version"]];
      case "partial": return [["keep", "Keep mine"], ["theirs", "Take it anyway"], ["edit", "Edit"]];
      case "drifted": case "untracked": return [["keep", "Keep mine"], ["theirs", "Take AI's"], ["edit", "Edit"]];
      default: return [];
    }
  }

  function chosen(e) {
    const d = decisions[e.id];
    if (d && typeof d === "object" && d.action === "custom") return "edit";
    if (d && typeof d === "object" && d.action === "apply") return "apply";
    return d;
  }

  async function select(e) {
    current = e;
    if (!view[e.id]) view[e.id] = { left: "yours", right: e.safe ? "proposed" : "incoming", mode: "diff" };
    renderList();
    const v = view[e.id];
    const parts = e.id.split("/"); const base = parts.pop();
    const [label, color] = STATUS[e.status];
    const ch = choices(e);
    const m = $("#main");
    m.innerHTML = `
      <div class="filehead">
        <div class="path"><span class="d">${esc(parts.length ? parts.join("/") + "/" : "")}</span>${esc(base)}</div>
        <div class="meta">
          <span class="chip" style="color:${color};background:color-mix(in oklab, ${color} 14%, transparent)">${esc(label)}</span>
          <span>${esc(e.note || e.reason)}</span>
          ${e.moved ? `<span class="pill">from ${esc(e.source)}</span>` : ""}
          ${e.redacted ? `<span class="pill">secret file — your values are kept</span>` : ""}
        </div>
      </div>
      <div class="actions">
        ${ch.length ? `<div class="seg" role="radiogroup" aria-label="Decision">${ch.map(([k, t]) => `<button role="radio" data-choice="${k}" class="${chosen(e) === k || (k === "edit" && v.mode === "edit") ? "on choice" : ""}">${esc(t)}</button>`).join("")}</div>` : ""}
        <span class="spacer"></span>
        ${e.status !== "ambiguous" ? `<div class="seg" aria-label="Compare">
          <button data-cmp="yours" class="${v.mode === "diff" && v.left === "yours" ? "on" : ""}">Yours ↔ ${e.safe ? "Result" : "Incoming"}</button>
          ${e.hasBase ? `<button data-cmp="original" class="${v.mode === "diff" && v.left === "original" ? "on" : ""}">What the AI changed</button>` : ""}
        </div>` : ""}
      </div>
      ${e.status === "moved" && e.movedFrom ? `<label class="notice" style="display:flex;gap:10px;align-items:center;margin:12px 24px 0">
        <input type="checkbox" id="rmOld" ${e.removable ? "" : "disabled"} ${decisions[e.id] && decisions[e.id].remove_old ? "checked" : ""}>
        <span>${e.removable ? `Also remove the old file <b>${esc(e.movedFrom)}</b>` : `The old file <b>${esc(e.movedFrom)}</b> changed since you sent it, so it will be kept`}</span></label>` : ""}
      <div class="view" id="view"></div>
      <footer class="nav"><span class="kbd">J</span><span class="kbd">K</span> next / previous file <span class="spacer"></span>
        <span class="kbd">Ctrl</span><span class="kbd">Enter</span> apply</footer>`;

    m.querySelectorAll("[data-choice]").forEach((btn) => btn.onclick = () => choose(e, btn.dataset.choice));
    const rm = m.querySelector("#rmOld");
    if (rm) rm.onchange = () => { decisions[e.id] = rm.checked ? { action: "apply", remove_old: true } : "apply"; refreshHeader(); renderList(); };
    m.querySelectorAll("[data-cmp]").forEach((btn) => btn.onclick = () => {
      v.mode = "diff"; v.left = btn.dataset.cmp; v.right = btn.dataset.cmp === "original" ? "incoming" : (e.safe ? "proposed" : "incoming");
      select(e);
    });

    if (e.status === "ambiguous") return renderCandidates(e);
    if (v.mode === "edit") return renderEdit(e);
    return renderDiff(e, v);
  }

  function choose(e, k) {
    if (k === "edit") { view[e.id].mode = "edit"; return select(e); }
    decisions[e.id] = k;
    view[e.id].mode = "diff";
    if (k === "theirs") view[e.id].right = "incoming";
    refreshHeader();
    select(e);
  }

  async function renderDiff(e, v) {
    const box = $("#view");
    if (e.status === "unchanged") { box.innerHTML = `<div class="notice">This file already matches — nothing to do.</div>`; }
    const data = await api(`/api/diff?id=${encodeURIComponent(e.id)}&left=${v.left}&right=${v.right}`);
    if (current !== e) return;
    if (data.binary) { box.innerHTML = `<div class="notice">Binary or very large file — no preview. Your choice above still applies.</div>`; return; }
    if (data.error) { box.innerHTML = `<div class="notice warn">${esc(data.error)}</div>`; return; }
    const leftTitle = e.status === "moved" && v.left === "yours" ? `Before (${e.movedFrom})` : { yours: data.leftExists ? "Yours (on disk)" : "Yours — doesn't exist", original: "What you sent" }[v.left];
    const rightTitle = e.status === "delete-requested" ? "After deleting" : { proposed: "After applying", incoming: "Incoming (from the AI)" }[v.right];
    const rows = data.rows.map(rowHtml).join("");
    box.innerHTML = (e.status === "partial" ? `<div class="notice warn">This file contains a placeholder comment where code used to be. Applying it as-is would delete that code.</div>` : "")
      + (data.same ? `<div class="notice">No differences.</div>` : "")
      + `<table class="diff"><colgroup><col class="n"><col class="c"><col class="n"><col class="c"></colgroup>
      <thead><tr><th colspan="2">${esc(leftTitle)}</th><th colspan="2">${esc(rightTitle)}</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function marked(text, spans, cls) {
    if (!spans || !spans.length) return esc(text);
    let out = "", pos = 0;
    for (const [a, b] of spans) { out += esc(text.slice(pos, a)) + `<mark class="${cls}">${esc(text.slice(a, b))}</mark>`; pos = b; }
    return out + esc(text.slice(pos));
  }

  function rowHtml(r) {
    if (r.t === "fold") return `<tr class="fold"><td colspan="4">${r.n} unchanged line${r.n === 1 ? "" : "s"}</td></tr>`;
    const l = r.l ? `<td class="n">${r.l[0]}</td><td class="c l">${marked(r.l[1], r.ls, "d")}</td>` : `<td class="n"></td><td class="c l empty"></td>`;
    const rr = r.r ? `<td class="n">${r.r[0]}</td><td class="c r">${marked(r.r[1], r.rs, "a")}</td>` : `<td class="n"></td><td class="c r empty"></td>`;
    return `<tr class="${r.t}">${l}${rr}</tr>`;
  }

  async function renderEdit(e) {
    const box = $("#view");
    let text = drafts[e.id];
    if (text === undefined) {
      const data = await api(`/api/edit?id=${encodeURIComponent(e.id)}`);
      if (current !== e) return;
      if (data.binary) { box.innerHTML = `<div class="notice">Binary file — it can't be edited here.</div>`; return; }
      text = data.text;
    }
    box.innerHTML = `<div class="edit">
      <textarea id="ed" spellcheck="false" aria-label="Edit the result"></textarea>
      <div class="editbar"><span id="edMsg"></span><span class="spacer"></span>
        <button class="btn" id="edReset">Start over</button><button class="btn primary" id="edUse">Use this version</button></div></div>`;
    const ta = $("#ed"); ta.value = text;
    const check = () => {
      drafts[e.id] = ta.value;
      const markers = /^(<<<<<<<|=======|>>>>>>>)( |$)/m.test(ta.value);
      $("#edMsg").textContent = markers ? "Conflict markers are still in the text — remove them, keeping the lines you want." : "Ready — this exact text will be written.";
      $("#edMsg").style.color = markers ? "var(--amber)" : "var(--muted)";
      $("#edUse").disabled = markers;
    };
    ta.oninput = check; check();
    $("#edReset").onclick = () => { delete drafts[e.id]; renderEdit(e); };
    $("#edUse").onclick = () => { decisions[e.id] = { action: "custom", content: ta.value }; refreshHeader(); renderList(); $("#edMsg").textContent = "Saved — it will be written when you press Apply."; };
  }

  function renderCandidates(e) {
    const box = $("#view");
    const d = decisions[e.id];
    box.innerHTML = `<div class="notice">“${esc(e.source)}” fits more than one file. Pick where it belongs, or skip it.</div>
      <div class="cands">${e.candidates.map((c) => `<button class="cand ${d && d.target === c ? "on" : ""}" data-t="${esc(c)}">${esc(c)}</button>`).join("")}
      <button class="cand ${d === "skip" ? "on" : ""}" data-skip="1">Skip this file</button></div>`;
    box.querySelectorAll(".cand").forEach((b) => b.onclick = () => {
      decisions[e.id] = b.dataset.skip ? "skip" : { action: "apply", target: b.dataset.t };
      refreshHeader(); renderList(); renderCandidates(e);
    });
  }

  async function apply() {
    $("#apply").disabled = true;
    const res = await fetch("/api/apply?token=" + encodeURIComponent(TOKEN), {
      method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ decisions }),
    }).then((r) => r.json()).catch(() => ({ error: "Couldn't reach contextzip — is the terminal still running?" }));
    if (res.error) { alert(res.error); $("#apply").disabled = false; return; }
    $("#done").classList.add("show");
  }

  function move(delta) {
    const flat = groups().flatMap(([, list]) => list);
    if (!flat.length) return;
    const i = Math.max(0, flat.findIndex((e) => current && e.id === current.id));
    select(flat[(i + delta + flat.length) % flat.length]);
  }

  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") { ev.preventDefault(); apply(); return; }
    if (ev.target instanceof HTMLTextAreaElement || ev.target instanceof HTMLInputElement) return;
    if (ev.key === "j" || ev.key === "ArrowDown") { ev.preventDefault(); move(1); }
    if (ev.key === "k" || ev.key === "ArrowUp") { ev.preventDefault(); move(-1); }
  });
  $("#q").oninput = (ev) => { filter = ev.target.value; renderList(); };
  $("#apply").onclick = apply;
  $("#cancel").onclick = async () => {
    await api("/api/cancel").catch(() => {});
    $("#doneTitle").textContent = "Cancelled"; $("#doneText").textContent = "Nothing was written. You can close this tab.";
    $("#done").classList.add("show");
  };

  api("/api/plan").then((data) => {
    plan = data;
    for (const e of plan.entries) if (e.safe) decisions[e.id] = "apply";
    const bits = [plan.zip, plan.manifest ? "compared with " + plan.manifest : "no snapshot of what you sent", plan.rule ? "paths remapped " + plan.rule : ""].filter(Boolean);
    $("#sub").textContent = bits.join("  ·  ");
    refreshHeader();
    const first = plan.entries.find((e) => e.risky) || plan.entries[0];
    if (first) select(first); else { renderList(); $("#main").innerHTML = `<div class="empty-state">This zip has no files to apply.</div>`; }
  }).catch(() => { $("#main").innerHTML = `<div class="empty-state">Couldn't load the review. Re-run contextzip apply --review.</div>`; });
})();
</script>
</body>
</html>
"""
