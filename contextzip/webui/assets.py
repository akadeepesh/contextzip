"""
webui/assets.py — The local config UI's entire frontend as one string.

Deliberately a single dependency-free HTML file (inline <style> + <script>,
no build step, no CDN fetches) rather than a directory of static assets:

  - Zero packaging risk — a Python string ships with the wheel/sdist
    automatically, no MANIFEST.in / package-data configuration to get
    wrong or forget.
  - Zero runtime dependency — no Node.js, no bundler, nothing beyond the
    stdlib http.server already used in server.py.
  - Zero network calls from the page itself — everything it needs (the
    tree, suggestions, save) comes from the same-origin local server;
    there are no <script src="https://...">, no web fonts, no images
    fetched from anywhere. This matters as much as the Python side of
    "nothing leaves this machine."

Colors/typography are hand-copied from the showcase Next.js app's
globals.css (OKLCH tokens) so the two feel like the same product, without
this CLI package depending on Next.js/Tailwind/React at all.
"""

from __future__ import annotations

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>contextzip — configure this project</title>
<meta name="robots" content="noindex, nofollow" />
<style>
  :root {
    --background: oklch(1 0 0);
    --foreground: oklch(0.148 0.004 228.8);
    --card: oklch(1 0 0);
    --muted: oklch(0.963 0.002 197.1);
    --muted-foreground: oklch(0.56 0.021 213.5);
    --border: oklch(0.925 0.005 214.3);
    --destructive: oklch(0.577 0.245 27.325);
    --signal: oklch(0.58 0.14 164);
    --beam: oklch(0.5 0.17 258);
    --diff-add: oklch(0.55 0.15 150);
    --surface-2: var(--muted);
    --radius: 0.625rem;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --background: oklch(0.148 0.004 228.8);
      --foreground: oklch(0.987 0.002 197.1);
      --card: oklch(0.218 0.008 223.9);
      --muted: oklch(0.275 0.011 216.9);
      --muted-foreground: oklch(0.723 0.014 214.4);
      --border: oklch(1 0 0 / 10%);
      --destructive: oklch(0.704 0.191 22.216);
      --signal: oklch(0.75 0.14 164);
      --beam: oklch(0.72 0.14 250);
      --diff-add: oklch(0.72 0.16 150);
      --surface-2: var(--muted);
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--background);
    color: var(--foreground);
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  code, .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }

  .shell {
    max-width: 1180px;
    margin: 0 auto;
    padding: 28px 20px 64px;
  }

  /* ---- terminal-window chrome, matches components/site/terminal-window.tsx ---- */
  .window {
    overflow: hidden;
    border-radius: 0.75rem;
    border: 1px solid var(--border);
    background: var(--card);
    box-shadow: 0 1px 0 0 rgba(255,255,255,0.04) inset, 0 30px 60px -30px rgba(0,0,0,0.5);
  }
  .window-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    background: color-mix(in oklch, var(--surface-2), transparent 40%);
    padding: 10px 16px;
  }
  .dots { display: flex; align-items: center; gap: 6px; }
  .dot { width: 10px; height: 10px; border-radius: 999px; }
  .dot.red { background: rgba(242,85,74,0.7); }
  .dot.yellow { background: rgba(255,182,72,0.7); }
  .dot.green { background: rgba(84,214,196,0.7); }
  .window-title {
    margin-left: 6px;
    font-size: 12px;
    color: var(--muted-foreground);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .window-body { padding: 0; }

  /* ---- header stats ---- */
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 20px 22px;
    border-bottom: 1px solid var(--border);
  }
  .head-left h1 {
    margin: 0 0 4px;
    font-size: 15px;
    font-weight: 600;
  }
  .head-left p {
    margin: 0;
    font-size: 13px;
    color: var(--muted-foreground);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 2px 10px 2px 8px;
    font-size: 11px;
    color: var(--muted-foreground);
    margin-left: 8px;
  }
  .badge .pulse {
    width: 6px; height: 6px; border-radius: 999px;
    background: var(--signal);
    box-shadow: 0 0 0 2px color-mix(in oklch, var(--signal), transparent 70%);
  }

  .stats {
    display: flex;
    align-items: baseline;
    gap: 22px;
  }
  .stat .num {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 26px;
    font-weight: 600;
    line-height: 1;
  }
  .stat .num.included { color: var(--diff-add); }
  .stat .num.excluded { color: var(--muted-foreground); }
  .stat .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted-foreground);
    margin-top: 4px;
  }

  /* ---- suggestions ---- */
  .suggestions {
    padding: 16px 22px;
    border-bottom: 1px solid var(--border);
  }
  .suggestions-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted-foreground);
    margin: 0 0 10px;
  }
  .chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 1px solid var(--border);
    background: var(--background);
    border-radius: 999px;
    padding: 6px 6px 6px 12px;
    font-size: 12.5px;
    transition: border-color 0.15s ease;
  }
  .chip:hover { border-color: color-mix(in oklch, var(--signal), transparent 55%); }
  .chip .chip-meta { color: var(--muted-foreground); }
  .chip button {
    border: none;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 600;
    cursor: pointer;
    background: color-mix(in oklch, var(--signal), transparent 88%);
    color: var(--signal);
  }
  .chip button:hover { background: color-mix(in oklch, var(--signal), transparent 78%); }
  .chip.applied {
    border-color: color-mix(in oklch, var(--diff-add), transparent 55%);
    background: color-mix(in oklch, var(--diff-add), transparent 94%);
  }
  .chip.applied button {
    background: transparent;
    color: var(--muted-foreground);
  }
  .chip.applied button:hover { color: var(--foreground); }
  .empty-suggestions {
    font-size: 12.5px;
    color: var(--muted-foreground);
  }

  /* ---- main grid ---- */
  .grid {
    display: grid;
    grid-template-columns: 1.5fr 1fr;
    min-height: 480px;
  }
  @media (max-width: 860px) {
    .grid { grid-template-columns: 1fr; }
  }

  .pane { padding: 16px 8px 16px 22px; }
  .pane.side { border-left: 1px solid var(--border); padding: 16px 22px; }
  @media (max-width: 860px) {
    .pane.side { border-left: none; border-top: 1px solid var(--border); }
  }

  .pane-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted-foreground);
    margin: 0 14px 10px 0;
  }
  .pane-title .hint { text-transform: none; letter-spacing: 0; font-size: 11.5px; }

  .search-box {
    display: flex;
    align-items: center;
    gap: 8px;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 10px;
    margin: 0 14px 10px 0;
    background: var(--background);
  }
  .search-box input {
    border: none;
    outline: none;
    background: transparent;
    color: var(--foreground);
    font-size: 13px;
    width: 100%;
  }
  .search-box svg { color: var(--muted-foreground); flex-shrink: 0; }

  .tree {
    max-height: 560px;
    overflow-y: auto;
    padding-right: 8px;
  }
  .node { user-select: none; }
  .node-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 6px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
  }
  .node-row:hover { background: var(--muted); }
  .node-row.excluded { opacity: 0.45; }
  .caret {
    width: 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--muted-foreground);
    flex-shrink: 0;
    transition: transform 0.12s ease;
  }
  .caret.open { transform: rotate(90deg); }
  .caret.leaf { visibility: hidden; }
  .checkbox {
    width: 15px; height: 15px;
    border-radius: 4px;
    border: 1.5px solid var(--border);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    background: var(--background);
  }
  .checkbox.checked { background: var(--signal); border-color: var(--signal); }
  .checkbox.partial { background: color-mix(in oklch, var(--signal), transparent 55%); border-color: var(--signal); }
  .checkbox svg { width: 10px; height: 10px; color: var(--card); }
  .node-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .node-name.dir::after { content: "/"; color: var(--muted-foreground); }
  .node-size { color: var(--muted-foreground); font-size: 11px; flex-shrink: 0; font-family: ui-monospace, monospace; }
  .children { margin-left: 20px; border-left: 1px dashed var(--border); padding-left: 2px; }
  .tree-empty { padding: 24px 8px; color: var(--muted-foreground); font-size: 13px; }

  /* ---- side panel: working config ---- */
  .pattern-group { margin-bottom: 18px; }
  .pattern-group h3 {
    margin: 0 0 8px;
    font-size: 12px;
    font-weight: 600;
  }
  .pattern-group h3.exclude { color: var(--destructive); }
  .pattern-group h3.include { color: var(--diff-add); }
  .pattern-list { display: flex; flex-wrap: wrap; gap: 6px; min-height: 20px; }
  .pattern-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--muted);
    border-radius: 6px;
    padding: 3px 4px 3px 8px;
    font-family: ui-monospace, monospace;
    font-size: 11.5px;
  }
  .pattern-pill button {
    border: none;
    background: transparent;
    color: var(--muted-foreground);
    cursor: pointer;
    padding: 2px;
    line-height: 0;
    border-radius: 4px;
  }
  .pattern-pill button:hover { color: var(--destructive); background: color-mix(in oklch, var(--destructive), transparent 88%); }
  .pattern-empty { font-size: 12px; color: var(--muted-foreground); }

  .add-row {
    display: flex;
    gap: 6px;
    margin-top: 8px;
  }
  .add-row select {
    border: 1px solid var(--border);
    background: var(--background);
    color: var(--foreground);
    border-radius: 6px;
    font-size: 12px;
    padding: 0 6px;
  }
  .add-row input {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--border);
    background: var(--background);
    color: var(--foreground);
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12.5px;
    font-family: ui-monospace, monospace;
  }
  .add-row input:focus, .search-box:focus-within { border-color: color-mix(in oklch, var(--signal), transparent 40%); outline: none; }
  .add-row button {
    border: 1px solid var(--border);
    background: var(--background);
    color: var(--foreground);
    border-radius: 6px;
    padding: 0 12px;
    font-size: 12.5px;
    cursor: pointer;
  }
  .add-row button:hover { background: var(--muted); }

  .json-toggle {
    margin-top: 20px;
    border-top: 1px solid var(--border);
    padding-top: 14px;
  }
  .json-toggle summary {
    cursor: pointer;
    font-size: 12px;
    color: var(--muted-foreground);
    list-style: none;
  }
  .json-toggle summary::-webkit-details-marker { display: none; }
  .json-toggle summary:hover { color: var(--foreground); }
  .json-block {
    margin-top: 10px;
    background: var(--muted);
    border-radius: 8px;
    padding: 12px;
    font-family: ui-monospace, monospace;
    font-size: 11.5px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 220px;
    overflow-y: auto;
  }

  /* ---- footer ---- */
  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    border-top: 1px solid var(--border);
    padding: 16px 22px;
    background: color-mix(in oklch, var(--surface-2), transparent 60%);
  }
  .foot-path {
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--muted-foreground);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .foot-path b { color: var(--foreground); font-weight: 500; }
  .foot-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 10px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.12s ease;
  }
  .btn-primary { background: var(--signal); color: var(--card); }
  .btn-primary:hover { filter: brightness(1.05); }
  .btn-primary:disabled { opacity: 0.5; cursor: default; filter: none; }
  .btn-ghost { background: transparent; color: var(--muted-foreground); border-color: var(--border); }
  .btn-ghost:hover { color: var(--foreground); background: var(--muted); }

  .below-note {
    text-align: center;
    font-size: 11.5px;
    color: var(--muted-foreground);
    margin-top: 14px;
  }
  .below-note .lock { color: var(--diff-add); }

  /* ---- success overlay ---- */
  .overlay {
    position: fixed; inset: 0;
    background: color-mix(in oklch, var(--background), transparent 8%);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 50;
    backdrop-filter: blur(2px);
  }
  .overlay.show { display: flex; }
  .overlay-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 32px 36px;
    text-align: center;
    max-width: 380px;
    box-shadow: 0 30px 60px -30px rgba(0,0,0,0.5);
  }
  .overlay-check {
    width: 44px; height: 44px;
    border-radius: 999px;
    background: color-mix(in oklch, var(--diff-add), transparent 85%);
    color: var(--diff-add);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 16px;
  }
  .overlay-card h2 { margin: 0 0 6px; font-size: 16px; }
  .overlay-card p { margin: 0; font-size: 13px; color: var(--muted-foreground); }
  .overlay-card code {
    display: block;
    margin-top: 10px;
    font-size: 12px;
    background: var(--muted);
    border-radius: 8px;
    padding: 8px 10px;
    word-break: break-all;
  }

  .toast {
    position: fixed;
    bottom: 20px; left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--foreground);
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 12.5px;
    opacity: 0;
    transition: all 0.2s ease;
    pointer-events: none;
    box-shadow: 0 20px 40px -20px rgba(0,0,0,0.4);
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>

<div class="shell">
  <div class="window">
    <div class="window-bar">
      <div class="dots">
        <span class="dot red"></span>
        <span class="dot yellow"></span>
        <span class="dot green"></span>
      </div>
      <p class="window-title" id="window-title">contextzip config</p>
    </div>

    <div class="window-body">
      <div class="head">
        <div class="head-left">
          <h1 id="project-name">Loading project…</h1>
          <p id="project-path">&nbsp;</p>
        </div>
        <div class="stats">
          <div class="stat">
            <div class="num included" id="stat-included">–</div>
            <div class="label">included</div>
          </div>
          <div class="stat">
            <div class="num excluded" id="stat-excluded">–</div>
            <div class="label">excluded</div>
          </div>
          <div class="stat">
            <div class="num" id="stat-size">–</div>
            <div class="label">packed size</div>
          </div>
        </div>
      </div>

      <div class="suggestions" id="suggestions-wrap">
        <p class="suggestions-label">Suggested excludes</p>
        <div class="chip-row" id="chip-row">
          <span class="empty-suggestions">Scanning…</span>
        </div>
      </div>

      <div class="grid">
        <div class="pane">
          <div class="pane-title">
            <span>File tree</span>
            <span class="hint" id="tree-hint"></span>
          </div>
          <div class="search-box">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
            <input id="search-input" type="text" placeholder="Filter files…" autocomplete="off" spellcheck="false" />
          </div>
          <div class="tree" id="tree-root">
            <div class="tree-empty">Scanning your project…</div>
          </div>
        </div>

        <div class="pane side">
          <div class="pane-title"><span>Working config</span></div>

          <div class="pattern-group">
            <h3 class="exclude">always_exclude</h3>
            <div class="pattern-list" id="exclude-list"></div>
          </div>

          <div class="pattern-group">
            <h3 class="include">always_include</h3>
            <div class="pattern-list" id="include-list"></div>
          </div>

          <div class="add-row">
            <select id="add-kind">
              <option value="exclude">exclude</option>
              <option value="include">include</option>
            </select>
            <input id="add-pattern" type="text" placeholder="e.g. *.snap or docs/internal/" spellcheck="false" />
            <button id="add-btn" type="button">Add</button>
          </div>

          <details class="json-toggle">
            <summary>View raw config.json ›</summary>
            <div class="json-block" id="json-preview"></div>
          </details>
        </div>
      </div>

      <div class="foot">
        <div class="foot-path">Will write to <b id="config-path-inline">.contextzip/config.json</b></div>
        <div class="foot-actions">
          <button class="btn btn-ghost" id="reset-btn" type="button">Reset</button>
          <button class="btn btn-primary" id="save-btn" type="button">Save config.json</button>
        </div>
      </div>
    </div>
  </div>

  <p class="below-note"><span class="lock">●</span> Running on 127.0.0.1 only — nothing about this project leaves your machine.</p>
</div>

<div class="overlay" id="overlay">
  <div class="overlay-card">
    <div class="overlay-check">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6 9 17l-5-5"/></svg>
    </div>
    <h2>Saved</h2>
    <p>Your project preferences are set. You can close this tab and go back to your terminal.</p>
    <code id="overlay-path"></code>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
(function () {
  "use strict";

  var TOKEN = "__TOKEN__";

  var state = {
    always_include: [],
    always_exclude: [],
    tree: [],
    expanded: new Set(["__root__"]),
    filter: "",
  };

  function api(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ "X-Contextzip-Token": TOKEN }, opts.headers || {});
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(path, Object.assign({}, opts, { headers: headers }))
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            throw new Error((data && data.error) || ("Request failed: " + res.status));
          }
          return data;
        });
      });
  }

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    var units = ["KB", "MB", "GB"];
    var v = n;
    for (var i = 0; i < units.length; i++) {
      v = v / 1024;
      if (v < 1024 || i === units.length - 1) return v.toFixed(v < 10 ? 1 : 0) + " " + units[i];
    }
    return v.toFixed(1) + " GB";
  }

  function debounce(fn, ms) {
    var t;
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  }

  function showToast(msg) {
    var el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }

  // ---- pattern list helpers ----
  function addUnique(list, value) {
    if (list.indexOf(value) === -1) list.push(value);
  }
  function removeExact(list, value) {
    var idx = list.indexOf(value);
    if (idx !== -1) list.splice(idx, 1);
  }

  // ---- rendering: header / stats ----
  function renderHeader(data) {
    document.getElementById("project-name").textContent = data.project.name;
    document.getElementById("window-title").textContent = "contextzip config — " + data.project.name;
    document.getElementById("project-path").innerHTML =
      data.project.path + '<span class="badge"><span class="pulse"></span>' + data.project.ecosystem + "</span>";
    document.getElementById("config-path-inline").textContent = data.configPath;
    document.getElementById("overlay-path").textContent = data.configPath;
  }

  function renderStats(stats) {
    document.getElementById("stat-included").textContent = stats.includedCount;
    document.getElementById("stat-excluded").textContent = stats.excludedCount;
    document.getElementById("stat-size").textContent = formatBytes(stats.includedBytes);
    document.getElementById("tree-hint").textContent = stats.includedCount + " / " + stats.totalCount + " files included";
  }

  // ---- rendering: suggestions ----
  function renderSuggestions(suggestions) {
    var row = document.getElementById("chip-row");
    row.innerHTML = "";
    if (!suggestions.length) {
      row.innerHTML = '<span class="empty-suggestions">Nothing unusual found — looks clean already.</span>';
      return;
    }
    suggestions.forEach(function (s) {
      var applied = isSuggestionApplied(s);
      var chip = document.createElement("div");
      chip.className = "chip" + (applied ? " applied" : "");
      var meta = s.count + (s.count === 1 ? " file" : " files") + " · " + formatBytes(s.bytes);
      chip.innerHTML =
        '<span>' + s.label + '</span><span class="chip-meta">' + meta + "</span>";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = applied ? "Undo" : "Exclude";
      btn.addEventListener("click", function () { toggleSuggestion(s, applied); });
      chip.appendChild(btn);
      row.appendChild(chip);
    });
  }

  function isSuggestionApplied(s) {
    if (s.pattern) return state.always_exclude.indexOf(s.pattern) !== -1;
    if (s.paths) return s.paths.every(function (p) { return state.always_exclude.indexOf(p) !== -1; });
    return false;
  }

  function toggleSuggestion(s, applied) {
    var targets = s.pattern ? [s.pattern] : (s.paths || []);
    targets.forEach(function (p) {
      if (applied) removeExact(state.always_exclude, p);
      else addUnique(state.always_exclude, p);
    });
    schedulePreview();
  }

  // ---- rendering: pattern pills ----
  function renderPatternList(elId, list, kind) {
    var el = document.getElementById(elId);
    el.innerHTML = "";
    if (!list.length) {
      el.innerHTML = '<span class="pattern-empty">none yet</span>';
      return;
    }
    list.forEach(function (pattern) {
      var pill = document.createElement("span");
      pill.className = "pattern-pill";
      var label = document.createElement("span");
      label.textContent = pattern;
      pill.appendChild(label);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", "Remove");
      btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>';
      btn.addEventListener("click", function () {
        removeExact(kind === "exclude" ? state.always_exclude : state.always_include, pattern);
        schedulePreview();
      });
      pill.appendChild(btn);
      el.appendChild(pill);
    });
  }

  function renderJsonPreview() {
    var obj = { always_include: state.always_include, always_exclude: state.always_exclude };
    document.getElementById("json-preview").textContent = JSON.stringify(obj, null, 2);
  }

  // ---- rendering: tree ----
  function nodeMatchesFilter(node, query) {
    if (!query) return true;
    if (node.name.toLowerCase().indexOf(query) !== -1) return true;
    if (node.children) {
      return node.children.some(function (c) { return nodeMatchesFilter(c, query); });
    }
    return false;
  }

  function renderTree(nodes) {
    var root = document.getElementById("tree-root");
    root.innerHTML = "";
    var query = state.filter.trim().toLowerCase();
    var visible = query ? nodes.filter(function (n) { return nodeMatchesFilter(n, query); }) : nodes;

    if (!visible.length) {
      root.innerHTML = '<div class="tree-empty">' + (query ? "No files match “" + state.filter + "”." : "No files found.") + "</div>";
      return;
    }
    var frag = document.createDocumentFragment();
    visible.forEach(function (n) { frag.appendChild(renderNode(n, query, 0)); });
    root.appendChild(frag);
  }

  function renderNode(node, query, depth) {
    var wrap = document.createElement("div");
    wrap.className = "node";

    var row = document.createElement("div");
    row.className = "node-row" + (node.included === false ? " excluded" : "");

    var isDir = node.type === "dir";
    var isOpen = query ? true : state.expanded.has(node.path);

    var caret = document.createElement("span");
    caret.className = "caret" + (isDir ? (isOpen ? " open" : "") : " leaf");
    caret.innerHTML = isDir ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m9 6 6 6-6 6"/></svg>' : "";
    row.appendChild(caret);

    var checkbox = document.createElement("span");
    var checkState = node.included === true ? "checked" : (node.included === "partial" ? "partial" : "");
    checkbox.className = "checkbox" + (checkState ? " " + checkState : "");
    if (checkState === "checked") {
      checkbox.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6 9 17l-5-5"/></svg>';
    } else if (checkState === "partial") {
      checkbox.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12h14"/></svg>';
    }
    row.appendChild(checkbox);

    var name = document.createElement("span");
    name.className = "node-name" + (isDir ? " dir" : "");
    name.textContent = node.name;
    row.appendChild(name);

    if (!isDir) {
      var size = document.createElement("span");
      size.className = "node-size";
      size.textContent = formatBytes(node.size || 0);
      row.appendChild(size);
    }

    row.addEventListener("click", function (e) {
      if (isDir && (e.target === caret || caret.contains(e.target))) {
        toggleExpand(node.path);
        return;
      }
      toggleNode(node);
    });

    wrap.appendChild(row);

    if (isDir && isOpen && node.children) {
      var childWrap = document.createElement("div");
      childWrap.className = "children";
      var kids = query ? node.children.filter(function (c) { return nodeMatchesFilter(c, query); }) : node.children;
      kids.forEach(function (c) { childWrap.appendChild(renderNode(c, query, depth + 1)); });
      wrap.appendChild(childWrap);
    }

    return wrap;
  }

  function toggleExpand(path) {
    if (state.expanded.has(path)) state.expanded.delete(path);
    else state.expanded.add(path);
    renderTree(state.tree);
  }

  function toggleNode(node) {
    var willInclude = node.included !== true; // true -> exclude; false/partial -> include
    var pattern = node.type === "dir" ? node.path + "/" : node.path;

    if (willInclude) {
      removeExact(state.always_exclude, pattern);
      removeExact(state.always_exclude, node.path);
      addUnique(state.always_include, pattern);
    } else {
      removeExact(state.always_include, pattern);
      removeExact(state.always_include, node.path);
      addUnique(state.always_exclude, pattern);
    }
    if (node.type === "dir") state.expanded.add(node.path);
    schedulePreview();
  }

  // ---- server round-trips ----
  function applyPayload(data) {
    state.tree = data.tree;
    if (data.project) renderHeader(data);
    renderStats(data.stats);
    renderSuggestions(data.suggestions);
    renderPatternList("exclude-list", state.always_exclude, "exclude");
    renderPatternList("include-list", state.always_include, "include");
    renderJsonPreview();
    renderTree(state.tree);
  }

  function loadInitial() {
    api("/api/state").then(function (data) {
      state.always_include = data.config.always_include.slice();
      state.always_exclude = data.config.always_exclude.slice();
      applyPayload(data);
    }).catch(function (err) {
      document.getElementById("project-name").textContent = "Could not load project";
      document.getElementById("project-path").textContent = err.message || "Unknown error";
      document.getElementById("chip-row").innerHTML =
        '<span class="empty-suggestions">Unavailable — see error below.</span>';
      var root = document.getElementById("tree-root");
      root.innerHTML = "";
      var msg = document.createElement("div");
      msg.className = "tree-empty";
      msg.textContent = "Could not load this project: " + (err.message || "unknown error") + ". ";
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn btn-ghost";
      retry.style.marginLeft = "4px";
      retry.textContent = "Retry";
      retry.addEventListener("click", function () {
        document.getElementById("project-name").textContent = "Loading project…";
        document.getElementById("project-path").textContent = "\u00a0";
        root.innerHTML = '<div class="tree-empty">Scanning your project…</div>';
        document.getElementById("chip-row").innerHTML = '<span class="empty-suggestions">Scanning…</span>';
        loadInitial();
      });
      root.appendChild(msg);
      root.appendChild(retry);
    });
  }

  var schedulePreview = debounce(function () {
    api("/api/preview", {
      method: "POST",
      body: JSON.stringify({ always_include: state.always_include, always_exclude: state.always_exclude }),
    }).then(applyPayload).catch(function () { showToast("Preview failed — check the terminal."); });
  }, 120);

  // ---- manual add row ----
  document.getElementById("add-btn").addEventListener("click", function () {
    var input = document.getElementById("add-pattern");
    var kind = document.getElementById("add-kind").value;
    var val = input.value.trim();
    if (!val) return;
    addUnique(kind === "exclude" ? state.always_exclude : state.always_include, val);
    input.value = "";
    schedulePreview();
  });
  document.getElementById("add-pattern").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("add-btn").click();
  });

  // ---- search ----
  document.getElementById("search-input").addEventListener("input", debounce(function (e) {
    state.filter = e.target.value;
    renderTree(state.tree);
  }, 100));

  // ---- reset ----
  document.getElementById("reset-btn").addEventListener("click", function () {
    state.always_include = [];
    state.always_exclude = [];
    schedulePreview();
    showToast("Reset to defaults");
  });

  // ---- save ----
  document.getElementById("save-btn").addEventListener("click", function (e) {
    var btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Saving…";
    api("/api/save", {
      method: "POST",
      body: JSON.stringify({ always_include: state.always_include, always_exclude: state.always_exclude }),
    }).then(function () {
      document.getElementById("overlay").classList.add("show");
      setTimeout(function () {
        api("/api/shutdown", { method: "GET" }).catch(function () {});
      }, 900);
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = "Save config.json";
      showToast("Save failed — check the terminal.");
    });
  });

  loadInitial();
})();
</script>
</body>
</html>
"""

TOKEN_ERROR_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>contextzip</title>
<style>
  body { font-family: ui-sans-serif, system-ui, sans-serif; background: #0f1115; color: #e6e6e6;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .card { max-width: 420px; text-align: center; padding: 32px; }
  code { background: #1c1f26; padding: 2px 6px; border-radius: 4px; }
</style></head>
<body>
  <div class="card">
    <h2>Invalid or missing session token</h2>
    <p>This link has expired or is incomplete. Go back to your terminal and
    run <code>contextzip config --ui</code> again to get a fresh link.</p>
  </div>
</body></html>
"""
