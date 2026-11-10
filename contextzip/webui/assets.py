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
    tree, suggestions, config, save) comes from the same-origin local
    server; there are no <script src="https://...">, no web fonts, no
    images fetched from anywhere. This matters as much as the Python side
    of "nothing leaves this machine" — so typography deliberately uses
    each OS's own UI/monospace font stack rather than a bundled or
    CDN-fetched face, even though that means giving up pixel-perfect
    control over exactly which fonts render.

The Files tab is a checkbox tree. A row has two separate controls with
their own hit areas: the caret opens a folder, the checkbox decides
whether it goes in the ZIP. Clicking a folder's name opens it; clicking a
file's name ticks it. (They used to be one "+" mark beside a 10px caret,
where a near miss on the caret flipped the whole folder.) Folder rows
show how much of the folder is going in, so it's clear where the bulk is.

Suggestions ("9.3 MB of this ZIP is probably not code") sit in an amber
panel above the tree, sized by how much each would save, and stay visible
once applied so they can be undone.

Five tabs share one page: Files (the tree + always_include/exclude —
the original single-pane UI), AI selection, Workspace, Advanced, and a
live Raw JSON preview of the exact object that gets POSTed to /api/save.
All five read from and write to the same `state.cfg` object client-side;
Save sends the whole thing in one request. See webui/server.py's
_ConfigUIState and /api/state /api/save handlers for the matching
server-side contract.
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
    --bg: #0a0c0f;
    --panel: #12151a;
    --panel-2: #0d0f13;
    --field: #1a1e24;
    --field-hover: #20252c;
    --border: #252b33;
    --border-soft: #1c2127;
    --text: #e8eaed;
    --muted: #7c8794;
    --muted-2: #4d5760;
    --add: #5fd98a;
    --add-dim: #2c4536;
    --add-bg: rgba(95, 217, 138, 0.08);
    --rem: #e2686c;
    --rem-dim: #4a2c2d;
    --rem-bg: rgba(226, 104, 108, 0.08);
    --warn: #e6b450;
    --warn-dim: #5c4a1f;
    --warn-bg: rgba(230, 180, 80, 0.07);
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
      Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Consolas,
      "Liberation Mono", monospace;
    --radius: 10px;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--add-dim); color: var(--text); }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 5px; border: 2px solid var(--panel); }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted-2); }

  .shell { max-width: 1180px; margin: 40px auto 24px; padding: 0 16px; }
  .window {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 30px 80px -30px rgba(0,0,0,0.6), 0 1px 0 rgba(255,255,255,0.03) inset;
  }

  .window-bar {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 18px;
    background: var(--panel-2);
    border-bottom: 1px solid var(--border-soft);
  }
  .dots { display: flex; gap: 7px; }
  .dot { width: 11px; height: 11px; border-radius: 50%; }
  .dot.red { background: #ff5f57; }
  .dot.yellow { background: #febc2e; }
  .dot.green { background: #28c840; }
  .window-title {
    margin: 0 0 0 8px; font-family: var(--mono); font-size: 12px; color: var(--muted);
  }

  .window-body { padding: 26px 28px 0; }

  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 32px; padding-bottom: 20px; border-bottom: 1px solid var(--border-soft); }
  .head-left { min-width: 0; }
  .head-title { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .head-title h1 { margin: 0; font-size: 21px; font-weight: 600; letter-spacing: -0.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .head-left p { margin: 0; font-family: var(--mono); font-size: 12.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px; flex: none; white-space: nowrap;
    font-size: 11.5px; font-weight: 500; color: #8fd6a8;
    background: var(--add-bg); border: 1px solid var(--add-dim);
    padding: 3px 10px 3px 8px; border-radius: 20px; font-family: var(--sans);
  }
  .badge[hidden] { display: none; }
  .pulse { width: 6px; height: 6px; border-radius: 50%; background: var(--add); display: inline-block; }

  .stats { display: flex; gap: 30px; flex: none; }
  .stat { text-align: right; }
  .stat .num { font-family: var(--mono); font-size: 22px; font-weight: 600; line-height: 1; white-space: nowrap; }
  .stat .num.included { color: var(--add); }
  .stat .num.excluded { color: var(--muted); }
  .stat .label { margin-top: 7px; font-size: 10px; letter-spacing: 0.08em; color: var(--muted-2); text-transform: uppercase; font-weight: 600; white-space: nowrap; }

  /* ---- suggestions: what is probably not worth sending ---- */
  .trim {
    margin-bottom: 22px; border: 1px solid var(--warn-dim); border-left: 3px solid var(--warn);
    background: var(--warn-bg); border-radius: 10px; padding: 16px 18px 18px;
  }
  .trim[hidden] { display: none; }
  .trim.done { border-color: var(--add-dim); border-left-color: var(--add); background: var(--add-bg); }
  .trim-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
  .trim-head h2 { margin: 0 0 4px; font-size: 15.5px; font-weight: 600; letter-spacing: -0.005em; }
  .trim-head h2 b { color: var(--warn); font-family: var(--mono); font-weight: 600; }
  .trim.done .trim-head h2 b { color: var(--add); }
  .trim-head p { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; }
  .trim-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 8px; }
  .sug {
    display: flex; align-items: center; gap: 12px; text-align: left; width: 100%;
    font-family: var(--sans); color: var(--text); cursor: pointer;
    background: var(--panel); border: 1px solid var(--border); border-radius: 9px;
    padding: 10px 12px; position: relative; overflow: hidden;
  }
  .sug:hover { border-color: var(--warn); }
  .sug.applied { border-color: var(--add-dim); }
  .sug.applied:hover { border-color: var(--add); }
  .sug-box {
    width: 18px; height: 18px; flex: none; border-radius: 5px; border: 1.5px solid var(--warn);
    display: inline-flex; align-items: center; justify-content: center; color: #0a1f12;
  }
  .sug.applied .sug-box { background: var(--add); border-color: var(--add); }
  .sug-box svg { display: none; }
  .sug.applied .sug-box svg { display: block; }
  .sug-body { flex: 1; min-width: 0; }
  .sug-label { display: block; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sug.applied .sug-label { color: var(--muted); text-decoration: line-through; text-decoration-color: var(--muted-2); }
  .sug-meta { display: block; margin-top: 2px; font-family: var(--mono); font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sug-size { font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--warn); flex: none; }
  .sug.applied .sug-size { color: var(--muted-2); }
  .sug-bar { position: absolute; left: 0; bottom: 0; height: 2px; background: var(--warn); opacity: 0.75; }
  .sug.applied .sug-bar { background: var(--add); opacity: 0.4; }

  /* ---- tabs ---- */
  .tabs { display: flex; gap: 2px; padding-top: 18px; border-bottom: 1px solid var(--border-soft); }
  .tab {
    font-family: var(--sans); background: none; border: none; cursor: pointer;
    color: var(--muted); font-size: 13px; font-weight: 500;
    padding: 9px 14px 11px; border-bottom: 2px solid transparent; margin-bottom: -1px;
    display: flex; align-items: center; gap: 7px; transition: color 0.12s ease;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--add); }
  .tab .tab-count {
    font-family: var(--mono); font-size: 10px; color: var(--muted-2);
    background: var(--field); padding: 1px 5px; border-radius: 20px;
  }

  .tab-panel { padding: 20px 0 26px; min-height: 420px; }
  .tab-panel[hidden] { display: none; }

  .panel-title { display: flex; align-items: center; justify-content: space-between; margin: 0 0 10px; }
  .panel-title h2 { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted-2); font-weight: 600; margin: 0; }
  .panel-title .sub { font-family: var(--mono); font-size: 11px; color: var(--muted-2); }

  /* ---- files tab: grid ---- */
  .grid { display: grid; grid-template-columns: 1.35fr 1fr; gap: 22px; }
  @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }

  .search-box { position: relative; margin-bottom: 10px; }
  .search-box svg { position: absolute; left: 11px; top: 50%; transform: translateY(-50%); color: var(--muted-2); pointer-events: none; }
  .search-box input {
    width: 100%; background: var(--field); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 12px 8px 32px; color: var(--text); font-family: var(--sans); font-size: 13px;
  }
  .search-box input::placeholder { color: var(--muted-2); }
  .search-box input:focus { outline: none; border-color: var(--muted-2); background: var(--field-hover); }

  .tree-tools { display: flex; gap: 8px; margin-bottom: 10px; }
  .tree-tools .search-box { flex: 1; margin-bottom: 0; }
  .btn-small {
    font-family: var(--sans); font-size: 12px; font-weight: 500; white-space: nowrap;
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    border-radius: 8px; padding: 0 11px; cursor: pointer;
  }
  .btn-small:hover { color: var(--text); border-color: var(--muted-2); }

  .tree {
    border: 1px solid var(--border); border-radius: 10px; background: var(--panel-2);
    max-height: max(420px, calc(100vh - 380px)); overflow-y: auto; font-family: var(--mono); font-size: 12.5px;
  }
  .tree-empty { padding: 24px 14px; color: var(--muted); font-size: 12.5px; }
  .tree-help { margin: 9px 2px 0; font-size: 11.5px; color: var(--muted-2); }
  .node-row {
    display: flex; align-items: center; min-height: 34px; padding: 0 14px 0 6px;
    cursor: pointer; border-bottom: 1px solid var(--border-soft);
  }
  .node-row:hover { background: rgba(255,255,255,0.03); }
  .node:last-child > .node-row { border-bottom: none; }

  /* Two controls, two hit areas: the caret opens, the box includes. */
  .caret {
    width: 26px; height: 26px; flex: none; padding: 0; border: none; background: none; cursor: pointer;
    color: var(--muted); border-radius: 6px; display: inline-flex; align-items: center; justify-content: center;
  }
  .caret svg { transition: transform 0.12s ease; }
  .caret.open svg { transform: rotate(90deg); }
  .caret:hover { background: var(--field-hover); color: var(--text); }
  .caret.leaf { visibility: hidden; }

  .check {
    width: 30px; height: 30px; flex: none; padding: 0; margin: 0 6px 0 4px; border: none; background: none; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center; border-radius: 6px;
  }
  .check i {
    width: 17px; height: 17px; border-radius: 5px; border: 1.5px solid var(--muted-2);
    display: inline-flex; align-items: center; justify-content: center; color: #0a1f12;
  }
  .check:hover { background: var(--field-hover); }
  .check:hover i { border-color: var(--text); }
  .check.on i { background: var(--add); border-color: var(--add); }
  .check.partial i { background: var(--warn); border-color: var(--warn); }
  .caret:focus-visible, .check:focus-visible, .sug:focus-visible, .btn-small:focus-visible { outline: 2px solid var(--add); outline-offset: 1px; }

  .node-name { flex: 1; min-width: 0; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .node-name.dir { font-weight: 600; }
  .node-row.excluded .node-name { color: var(--muted-2); }
  .node-meta { color: var(--muted-2); font-size: 11.5px; margin-left: 14px; white-space: nowrap; flex: none; }
  .node-meta b { color: var(--muted); font-weight: 500; }
  .node-meta.heavy b { color: var(--warn); }
  .tag-large {
    font-family: var(--sans); font-size: 10px; font-weight: 600; letter-spacing: 0.03em; flex: none;
    color: var(--warn); border: 1px solid var(--warn-dim); border-radius: 4px; padding: 1px 6px; margin-left: 10px;
  }
  .children { margin-left: 18px; border-left: 1px solid var(--border-soft); }

  .pane-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .pane-title span:first-child { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted-2); font-weight: 600; }
  .pane-title .hint { font-family: var(--mono); font-size: 11px; color: var(--muted-2); text-transform: none; letter-spacing: 0; }

  .pattern-group { margin-bottom: 18px; }
  .pattern-group h3 {
    font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; font-weight: 600;
    margin: 0 0 9px; display: flex; align-items: center; gap: 6px;
  }
  .pattern-group h3::before { content: ""; width: 8px; height: 8px; border-radius: 2px; }
  .pattern-group h3.exclude { color: var(--rem); }
  .pattern-group h3.exclude::before { background: var(--rem); }
  .pattern-group h3.include { color: var(--add); }
  .pattern-group h3.include::before { background: var(--add); }
  .pattern-group h3 code { font-family: var(--mono); font-size: 10.5px; font-weight: 400; letter-spacing: 0; text-transform: none; color: var(--muted-2); margin-left: 4px; }

  .pattern-list { display: flex; flex-direction: column; gap: 5px; }
  .pattern-empty { font-size: 12px; color: var(--muted-2); line-height: 1.5; padding: 4px 2px; display: block; }
  .pattern-pill {
    display: flex; align-items: center; justify-content: space-between; gap: 9px;
    font-family: var(--mono); font-size: 12.5px;
    background: var(--field); border: 1px solid var(--border);
    padding: 7px 8px 7px 10px; border-radius: 7px;
  }
  #exclude-list .pattern-pill { border-left: 2px solid var(--rem); }
  #include-list .pattern-pill { border-left: 2px solid var(--add); }
  .pattern-pill button {
    background: none; border: none; cursor: pointer; color: var(--muted-2);
    display: inline-flex; padding: 3px; border-radius: 4px;
  }
  .pattern-pill button:hover { color: var(--text); background: var(--field-hover); }

  .add-row { display: flex; gap: 8px; margin-top: 4px; }
  .add-row select {
    appearance: none; font-family: var(--mono); font-size: 12px; font-weight: 600;
    background: var(--field); border: 1px solid var(--border); color: var(--text);
    padding: 8px 10px; border-radius: 7px; cursor: pointer;
  }
  .add-row input[type="text"] {
    flex: 1; background: var(--field); border: 1px solid var(--border); border-radius: 7px;
    padding: 8px 12px; color: var(--text); font-family: var(--mono); font-size: 12.5px;
  }
  .add-row input::placeholder { color: var(--muted-2); }
  select:focus, input:focus, textarea:focus { outline: none; border-color: var(--muted-2); }

  .btn {
    font-family: var(--sans); font-size: 13px; font-weight: 600;
    border-radius: 7px; padding: 8px 16px; cursor: pointer; border: 1px solid transparent;
  }
  .btn-primary { background: var(--add); color: #0a1f12; }
  .btn-primary:hover { background: #74e29b; }
  .btn-primary:disabled { opacity: 0.6; cursor: default; }
  .btn-ghost { background: transparent; border-color: var(--border); color: var(--muted); }
  .btn-ghost:hover { color: var(--text); border-color: var(--muted-2); }
  .btn-warn { background: var(--warn); color: #241a02; white-space: nowrap; flex: none; }
  .btn-warn:hover { background: #f0c468; }
  .btn-warn.undo { background: transparent; border-color: var(--border); color: var(--muted); }
  .btn-warn.undo:hover { color: var(--text); border-color: var(--muted-2); }

  .json-block {
    margin-top: 10px; background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px; font-family: var(--mono); font-size: 11.5px; line-height: 1.6;
    white-space: pre-wrap; word-break: break-word; color: var(--muted);
  }

  /* ---- generic settings controls (AI / Workspace / Advanced) ---- */
  .field-grid { display: flex; flex-direction: column; gap: 20px; max-width: 620px; }
  .field { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
  .field .fl { max-width: 340px; }
  .field .ft { font-size: 13.5px; font-weight: 500; margin-bottom: 3px; }
  .field .fd { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .field .fc { flex: none; padding-top: 1px; }

  .toggle { width: 38px; height: 22px; border-radius: 20px; background: var(--field); border: 1px solid var(--border); position: relative; cursor: pointer; flex: none; }
  .toggle.on { background: var(--add-dim); border-color: var(--add-dim); }
  .toggle .knob { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--muted); transition: all 0.15s ease; }
  .toggle.on .knob { left: 18px; background: var(--add); }

  .stepper { display: inline-flex; align-items: center; background: var(--field); border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
  .stepper button { width: 28px; height: 32px; background: none; border: none; color: var(--muted); cursor: pointer; font-size: 15px; font-family: var(--mono); }
  .stepper button:hover { color: var(--text); background: var(--field-hover); }
  .stepper .val { min-width: 46px; text-align: center; font-family: var(--mono); font-size: 12.5px; font-weight: 600; border-left: 1px solid var(--border); border-right: 1px solid var(--border); padding: 6px 4px; }

  .radiogroup { display: flex; flex-direction: column; gap: 8px; max-width: 480px; }
  .radio { display: flex; align-items: flex-start; gap: 10px; padding: 10px 11px; border: 1px solid var(--border); border-radius: 8px; cursor: pointer; background: var(--panel-2); }
  .radio.sel { border-color: var(--add-dim); background: var(--add-bg); }
  .rdot { width: 15px; height: 15px; border-radius: 50%; border: 1.5px solid var(--muted-2); flex: none; position: relative; margin-top: 2px; }
  .radio.sel .rdot { border-color: var(--add); }
  .radio.sel .rdot::after { content: ""; position: absolute; inset: 3px; border-radius: 50%; background: var(--add); }
  .radio .rt { font-size: 13px; font-weight: 500; }
  .radio .rd { font-size: 11.5px; color: var(--muted); margin-top: 1px; }
  .radio .rd input {
    margin-top: 6px; background: var(--field); border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 8px; color: var(--text); font-family: var(--mono); font-size: 11.5px; width: 180px;
  }

  textarea {
    width: 100%; background: var(--field); border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); font-family: var(--mono); font-size: 12.5px; padding: 10px 12px;
    resize: vertical; min-height: 76px; line-height: 1.5;
  }

  .provider-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; max-width: 480px; }
  .provider { border: 1px solid var(--border); border-radius: 9px; padding: 12px 10px; background: var(--panel-2); cursor: pointer; text-align: center; }
  .provider.sel { border-color: var(--add-dim); background: var(--add-bg); }
  .provider .pn { font-size: 12.5px; font-weight: 600; margin-top: 2px; }
  .provider .pd { font-size: 10px; color: var(--muted-2); margin-top: 2px; }
  .provider.disabled { opacity: 0.4; cursor: not-allowed; }

  .port-input {
    width: 100px; background: var(--field); border: 1px solid var(--border); border-radius: 7px;
    padding: 8px 10px; color: var(--text); font-family: var(--mono); font-size: 12.5px; text-align: center;
  }
  .port-input::placeholder { color: var(--muted-2); }

  .unsaved-note { font-size: 11.5px; color: #d9b35f; margin-top: 2px; display: none; }
  .unsaved-note.show { display: block; }

  /* ---- footer ---- */
  .foot {
    display: flex; align-items: center; justify-content: space-between;
    margin: 0 -28px; padding: 16px 28px; border-top: 1px solid var(--border-soft); background: var(--panel-2);
  }
  .foot { gap: 24px; }
  .foot-path { font-size: 12px; color: var(--muted); min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .foot-path b { font-family: var(--mono); color: var(--text); font-weight: 500; }
  .foot-actions { display: flex; gap: 10px; flex: none; }
  .foot-actions .btn { white-space: nowrap; }

  .below-note {
    text-align: center; padding: 14px; font-size: 11.5px; color: var(--muted-2);
    display: flex; align-items: center; justify-content: center; gap: 6px; margin: 0;
  }
  .below-note .lock { color: var(--add); font-size: 8px; }

  /* ---- overlay + toast ---- */
  .overlay {
    position: fixed; inset: 0; background: rgba(6,7,9,0.72); display: none;
    align-items: center; justify-content: center; z-index: 50;
  }
  .overlay.show { display: flex; }
  .overlay-card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: 32px 36px; text-align: center; max-width: 340px;
    box-shadow: 0 30px 80px -20px rgba(0,0,0,0.7);
  }
  .overlay-check {
    width: 46px; height: 46px; border-radius: 50%; background: var(--add-bg); color: var(--add);
    display: flex; align-items: center; justify-content: center; margin: 0 auto 14px; border: 1px solid var(--add-dim);
  }
  .overlay-card h2 { margin: 0 0 8px; font-size: 17px; }
  .overlay-card p { margin: 0 0 14px; font-size: 13px; color: var(--muted); line-height: 1.5; }
  .overlay-card code { font-family: var(--mono); font-size: 11px; color: var(--muted-2); word-break: break-all; }

  .toast {
    position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%) translateY(20px);
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    padding: 10px 18px; border-radius: 8px; font-size: 12.5px; opacity: 0; pointer-events: none;
    transition: all 0.18s ease; box-shadow: 0 12px 30px -8px rgba(0,0,0,0.5); z-index: 60;
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
          <div class="head-title">
            <h1 id="project-name">Loading project…</h1>
            <span class="badge" id="project-stack" hidden></span>
          </div>
          <p id="project-path">&nbsp;</p>
        </div>
        <div class="stats">
          <div class="stat">
            <div class="num included" id="stat-included">–</div>
            <div class="label">files in the ZIP</div>
          </div>
          <div class="stat">
            <div class="num excluded" id="stat-excluded">–</div>
            <div class="label">left out</div>
          </div>
          <div class="stat">
            <div class="num" id="stat-size">–</div>
            <div class="label">size before zipping</div>
          </div>
        </div>
      </div>

      <div class="tabs" id="tabs">
        <button class="tab active" type="button" data-tab="files">Files <span class="tab-count" id="tab-files-count">–/–</span></button>
        <button class="tab" type="button" data-tab="ai">AI selection</button>
        <button class="tab" type="button" data-tab="workspace">Workspace</button>
        <button class="tab" type="button" data-tab="advanced">Advanced</button>
        <button class="tab" type="button" data-tab="raw">Raw JSON</button>
      </div>

      <!-- FILES -->
      <div class="tab-panel" id="panel-files">
        <section class="trim" id="trim" hidden aria-live="polite">
          <div class="trim-head">
            <div>
              <h2 id="trim-title"></h2>
              <p id="trim-sub"></p>
            </div>
            <button class="btn btn-warn" id="trim-all" type="button">Leave all out</button>
          </div>
          <div class="trim-list" id="trim-list"></div>
        </section>

        <div class="grid">
          <div class="pane">
            <div class="pane-title"><span>What goes in the ZIP</span><span class="hint" id="tree-hint"></span></div>
            <div class="tree-tools">
              <div class="search-box">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                <input id="search-input" type="text" placeholder="Filter files…" autocomplete="off" spellcheck="false" />
              </div>
              <button class="btn-small" id="expand-all" type="button">Open all</button>
              <button class="btn-small" id="collapse-all" type="button">Close all</button>
            </div>
            <div class="tree" id="tree-root">
              <div class="tree-empty">Scanning your project…</div>
            </div>
            <p class="tree-help">Tick what the AI should see. The arrow, or a folder's name, opens the folder.</p>
          </div>

          <div class="pane side">
            <div class="pane-title"><span>Your rules for this project</span></div>

            <div class="pattern-group">
              <h3 class="exclude">Always leave out <code>always_exclude</code></h3>
              <div class="pattern-list" id="exclude-list"></div>
            </div>

            <div class="pattern-group">
              <h3 class="include">Always include <code>always_include</code></h3>
              <div class="pattern-list" id="include-list"></div>
            </div>

            <div class="add-row">
              <select id="add-kind" aria-label="Kind of rule">
                <option value="exclude">leave out</option>
                <option value="include">include</option>
              </select>
              <input id="add-pattern" type="text" placeholder="e.g. *.snap or docs/internal/" spellcheck="false" aria-label="Pattern" />
              <button class="btn btn-primary" id="add-btn" type="button" style="padding:8px 14px;">Add</button>
            </div>
          </div>
        </div>
      </div>

      <!-- AI -->
      <div class="tab-panel" id="panel-ai" hidden>
        <div class="panel-title"><h2>AI file selection</h2><span class="sub">used by --prompt</span></div>
        <div class="field-grid">
          <div class="field">
            <div class="fl"><div class="ft">Enable AI selection</div><div class="fd">When off, --prompt is refused with a clear message instead of silently doing a full scan.</div></div>
            <div class="fc"><div class="toggle" id="ai-enabled-toggle"><div class="knob"></div></div></div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Provider</div><div class="fd">Which model selects files for --prompt. Only Gemini is currently supported end-to-end.</div></div>
          </div>
          <div class="provider-grid" id="provider-grid">
            <div class="provider" data-provider="gemini"><div class="pn">Gemini</div><div class="pd">default</div></div>
            <div class="provider disabled"><div class="pn">OpenAI</div><div class="pd">coming soon</div></div>
            <div class="provider disabled"><div class="pn">Claude</div><div class="pd">coming soon</div></div>
            <div class="provider disabled"><div class="pn">Local</div><div class="pd">coming soon</div></div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Max files per selection</div><div class="fd">Hard cap on how many files the model may return for one --prompt run.</div></div>
            <div class="fc stepper" id="max-files-stepper">
              <button type="button" data-step="-1">−</button><span class="val" id="max-files-val">10</span><button type="button" data-step="1">+</button>
            </div>
          </div>
          <div class="field" style="align-items:flex-start;">
            <div class="fl" style="max-width:100%;flex:1;">
              <div class="ft">Prompt template</div>
              <div class="fd" style="margin-bottom:8px;">Prepended to every generated prompt.txt, ahead of the task description — house conventions the AI tool should always see.</div>
              <textarea id="prompt-template" placeholder="e.g. We use pytest, not unittest. API routes live under app/api/."></textarea>
            </div>
          </div>
        </div>
      </div>

      <!-- WORKSPACE -->
      <div class="tab-panel" id="panel-workspace" hidden>
        <div class="panel-title"><h2>Workspace location</h2><span class="sub">where .contextzip/ lives</span></div>
        <div class="radiogroup" id="workspace-radiogroup" style="margin-bottom:22px;">
          <div class="radio" data-value="git-root">
            <div class="rdot"></div>
            <div><div class="rt">Git root</div><div class="rd">default — same folder no matter where you run contextzip from</div></div>
          </div>
          <div class="radio" data-value="cwd">
            <div class="rdot"></div>
            <div><div class="rt">Current directory</div><div class="rd">.contextzip/ wherever the command is run — useful per-subproject</div></div>
          </div>
          <div class="radio" data-value="custom">
            <div class="rdot"></div>
            <div>
              <div class="rt">Custom path</div>
              <div class="rd">
                <input id="workspace-custom-path" type="text" placeholder="~/zips" spellcheck="false" />
              </div>
            </div>
          </div>
        </div>

        <div class="panel-title"><h2>Scan depth</h2><span class="sub">monorepo ecosystem detection</span></div>
        <div class="field" style="max-width:620px;">
          <div class="fl"><div class="ft">Subdirectory scan depth</div><div class="fd">How many levels deep to look for framework markers (package.json, requirements.txt, …) beyond the project root.</div></div>
          <div class="fc stepper" id="scan-depth-stepper">
            <button type="button" data-step="-1">−</button><span class="val" id="scan-depth-val">2</span><button type="button" data-step="1">+</button>
          </div>
        </div>
      </div>

      <!-- ADVANCED -->
      <div class="tab-panel" id="panel-advanced" hidden>
        <div class="field-grid">
          <div class="field">
            <div class="fl"><div class="ft">Large file threshold</div><div class="fd">Files at or above this size get flagged before packaging instead of silently included.</div></div>
            <div class="fc stepper" id="max-size-stepper">
              <button type="button" data-step="-0.5">−</button><span class="val" id="max-size-val">1.0 MB</span><button type="button" data-step="0.5">+</button>
            </div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Redact secret-shaped values</div><div class="fd">Beyond excluding known credential files — scrub API-key-shaped strings inside otherwise-included files.</div></div>
            <div class="fc"><div class="toggle" id="redact-toggle"><div class="knob"></div></div></div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Add CONTEXTZIP.md to every ZIP</div><div class="fd">A short note for the AI: which snapshot this is, and how to reply so apply-zip can put the changes back cleanly.</div></div>
            <div class="fc"><div class="toggle" id="guide-toggle"><div class="knob"></div></div></div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Keep applied zips</div><div class="fd">How many past apply-zip archives to retain in .contextzip/inbox/applied/ before pruning.</div></div>
            <div class="fc stepper" id="retention-stepper">
              <button type="button" data-step="-1">−</button><span class="val" id="retention-val">1</span><button type="button" data-step="1">+</button>
            </div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Auto-open browser</div><div class="fd">Launch this config UI in your default browser automatically on contextzip config --ui.</div></div>
            <div class="fc"><div class="toggle" id="autoopen-toggle"><div class="knob"></div></div></div>
          </div>
          <div class="field">
            <div class="fl"><div class="ft">Fixed local port</div><div class="fd">Pin the config UI to one port instead of a random free one. Leave blank to keep picking a random port.</div></div>
            <div class="fc"><input class="port-input" id="port-input" type="text" inputmode="numeric" placeholder="random" /></div>
          </div>
        </div>
      </div>

      <!-- RAW JSON -->
      <div class="tab-panel" id="panel-raw" hidden>
        <div class="panel-title"><h2 id="raw-config-path">.contextzip/config.json</h2><span class="sub">live preview</span></div>
        <div class="json-block" id="json-preview-full" style="margin-top:14px;max-height:420px;overflow:auto;"></div>
      </div>

      <div class="foot">
        <div class="foot-path">
          Saves to <b id="config-path-inline">.contextzip/config.json</b>
          <div class="unsaved-note" id="unsaved-note">Unsaved changes</div>
        </div>
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
    suggestions: [],
    expanded: new Set(["__root__"]),
    filter: "",
    activeTab: "files",
    dirty: false,
    cfg: {
      workspace_location: "git-root",
      workspace_custom_path: "",
      scan_depth: 2,
      ai: { enabled: true, provider: "gemini", max_files: 10, prompt_template: "" },
      limits: { max_file_size_mb: 1, redact_secrets: false },
      applied_zip_retention: 1,
      webui: { auto_open: true, port: null },
      zip_guide: true
    }
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

  function markDirty() {
    state.dirty = true;
    document.getElementById("unsaved-note").classList.add("show");
    renderJsonPreview();
  }

  function renderPatterns() {
    renderPatternList("exclude-list", state.always_exclude);
    renderPatternList("include-list", state.always_include);
  }

  // ---- pattern list helpers ----
  function addUnique(list, value) {
    if (list.indexOf(value) === -1) list.push(value);
  }
  function removeExact(list, value) {
    var idx = list.indexOf(value);
    if (idx !== -1) list.splice(idx, 1);
  }

  // ---- tabs ----
  function initTabs() {
    document.querySelectorAll(".tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setActiveTab(btn.getAttribute("data-tab"));
      });
    });
  }
  function setActiveTab(name) {
    state.activeTab = name;
    document.querySelectorAll(".tab").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-tab") === name);
    });
    ["files", "ai", "workspace", "advanced", "raw"].forEach(function (n) {
      var panel = document.getElementById("panel-" + n);
      if (panel) panel.hidden = n !== name;
    });
    if (name === "raw") renderJsonPreview();
  }

  // ---- rendering: header / stats ----
  function shortPath(full) {
    var parts = String(full).split(/[\\/]/);
    return parts.slice(-2).join("/");
  }

  function renderHeader(data) {
    document.getElementById("project-name").textContent = data.project.name;
    document.getElementById("window-title").textContent = "contextzip config — " + data.project.name;
    var pathEl = document.getElementById("project-path");
    pathEl.textContent = data.project.path;
    pathEl.title = data.project.path;
    var stack = document.getElementById("project-stack");
    stack.innerHTML = '<span class="pulse"></span>';
    stack.appendChild(document.createTextNode(data.project.ecosystem));
    stack.hidden = false;
    var inline = document.getElementById("config-path-inline");
    inline.textContent = shortPath(data.configPath);
    inline.title = data.configPath;
    document.getElementById("overlay-path").textContent = data.configPath;
    document.getElementById("raw-config-path").textContent = data.configPath;
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  function renderStats(stats) {
    document.getElementById("stat-included").textContent = stats.includedCount;
    document.getElementById("stat-excluded").textContent = stats.excludedCount;
    document.getElementById("stat-size").textContent = formatBytes(stats.includedBytes);
    document.getElementById("tree-hint").textContent = stats.includedCount + " of " + plural(stats.totalCount, "file");
    document.getElementById("tab-files-count").textContent = stats.includedCount + "/" + stats.totalCount;
  }

  // ---- rendering: suggestions ----
  var CHECK_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  var DASH_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"><path d="M5 12h14"/></svg>';

  function renderSuggestions(suggestions, stats) {
    state.suggestions = suggestions;
    var panel = document.getElementById("trim");
    var list = document.getElementById("trim-list");
    list.innerHTML = "";
    if (!suggestions.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    var left = stats.suggestedBytes || 0;
    var allApplied = suggestions.every(isSuggestionApplied);
    var title = document.getElementById("trim-title");
    var sub = document.getElementById("trim-sub");
    panel.classList.toggle("done", left === 0);
    title.innerHTML = "";
    if (left > 0) {
      var share = stats.includedBytes ? Math.round((left / stats.includedBytes) * 100) : 0;
      var b = document.createElement("b");
      b.textContent = formatBytes(left);
      title.appendChild(b);
      title.appendChild(document.createTextNode(" of this ZIP is probably not code"));
      sub.textContent = "That's " + share + "% of its size. An AI rarely needs these, and they can push a ZIP past the upload limit. Leave out what it doesn't need.";
    } else {
      var d = document.createElement("b");
      d.textContent = "Trimmed.";
      title.appendChild(d);
      title.appendChild(document.createTextNode(" Nothing else looks out of place"));
      sub.textContent = "Click a suggestion again to bring those files back.";
    }
    var all = document.getElementById("trim-all");
    all.textContent = allApplied ? "Undo all" : "Leave all out";
    all.classList.toggle("undo", allApplied);

    var biggest = Math.max.apply(null, suggestions.map(function (s) { return s.bytes; })) || 1;
    suggestions.forEach(function (s) {
      var applied = isSuggestionApplied(s);
      var card = document.createElement("button");
      card.type = "button";
      card.className = "sug" + (applied ? " applied" : "");
      card.setAttribute("aria-pressed", applied ? "true" : "false");
      card.title = applied ? "Left out. Click to bring back." : "Click to leave these out of the ZIP.";

      var box = document.createElement("span");
      box.className = "sug-box";
      box.innerHTML = CHECK_SVG;
      card.appendChild(box);

      var body = document.createElement("span");
      body.className = "sug-body";
      var label = document.createElement("span");
      label.className = "sug-label";
      label.textContent = s.label;
      var meta = document.createElement("span");
      meta.className = "sug-meta";
      meta.textContent = applied ? plural(s.count, "file") + " left out" : plural(s.count, "file") + (s.pattern ? " · " + s.pattern : "");
      body.appendChild(label);
      body.appendChild(meta);
      card.appendChild(body);

      var size = document.createElement("span");
      size.className = "sug-size";
      size.textContent = formatBytes(s.bytes);
      card.appendChild(size);

      var bar = document.createElement("span");
      bar.className = "sug-bar";
      bar.style.width = Math.max(4, Math.round((s.bytes / biggest) * 100)) + "%";
      card.appendChild(bar);

      card.addEventListener("click", function () { toggleSuggestion(s, applied); });
      list.appendChild(card);
    });
  }

  function isSuggestionApplied(s) {
    if (s.pattern) return state.always_exclude.indexOf(s.pattern) !== -1;
    if (s.paths) return s.paths.every(function (p) { return state.always_exclude.indexOf(p) !== -1; });
    return false;
  }

  function setSuggestion(s, on) {
    var targets = s.pattern ? [s.pattern] : (s.paths || []);
    targets.forEach(function (p) {
      if (on) addUnique(state.always_exclude, p);
      else removeExact(state.always_exclude, p);
    });
  }

  function toggleSuggestion(s, applied) {
    setSuggestion(s, !applied);
    markDirty();
    renderPatterns();
    schedulePreview();
  }

  function toggleAllSuggestions() {
    var allApplied = state.suggestions.every(isSuggestionApplied);
    state.suggestions.forEach(function (s) { setSuggestion(s, !allApplied); });
    markDirty();
    renderPatterns();
    schedulePreview();
  }

  // ---- rendering: pattern pills (diff-styled) ----
  function renderPatternList(elId, list) {
    var el = document.getElementById(elId);
    el.innerHTML = "";
    if (!list.length) {
      el.innerHTML = '<span class="pattern-empty">' + (elId === "exclude-list"
        ? "Nothing yet. Untick a file or folder in the tree, or add a pattern below."
        : "Nothing yet. Tick something the rules leave out to bring it back.") + "</span>";
      return;
    }
    var sign = elId === "exclude-list" ? "\u2212" : "+";
    list.forEach(function (pattern) {
      var pill = document.createElement("span");
      pill.className = "pattern-pill";
      var label = document.createElement("span");
      label.textContent = sign + pattern;
      pill.appendChild(label);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", "Remove");
      btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>';
      btn.addEventListener("click", function () {
        removeExact(elId === "exclude-list" ? state.always_exclude : state.always_include, pattern);
        markDirty();
        renderPatterns();
        schedulePreview();
      });
      pill.appendChild(btn);
      el.appendChild(pill);
    });
  }

  // ---- full config object matching the /api/save contract ----
  function buildConfigPayload() {
    var ws = state.cfg.workspace_location;
    return {
      always_include: state.always_include,
      always_exclude: state.always_exclude,
      workspace_location: ws === "custom" ? (state.cfg.workspace_custom_path || "").trim() : ws,
      scan_depth: state.cfg.scan_depth,
      ai: state.cfg.ai,
      limits: state.cfg.limits,
      applied_zip_retention: state.cfg.applied_zip_retention,
      webui: state.cfg.webui,
      zip_guide: state.cfg.zip_guide !== false
    };
  }

  function renderJsonPreview() {
    document.getElementById("json-preview-full").textContent = JSON.stringify(buildConfigPayload(), null, 2);
  }

  // ---- rendering: tree (diff-gutter style) ----
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
      root.innerHTML = '<div class="tree-empty">' + (query ? "No files match \u201c" + state.filter + "\u201d." : "No files found.") + "</div>";
      return;
    }
    var frag = document.createDocumentFragment();
    visible.forEach(function (n) { frag.appendChild(renderNode(n, query)); });
    root.appendChild(frag);
  }

  function renderNode(node, query) {
    var wrap = document.createElement("div");
    wrap.className = "node";

    var row = document.createElement("div");
    row.className = "node-row" + (node.included === false ? " excluded" : "");

    var isDir = node.type === "dir";
    var isOpen = query ? true : state.expanded.has(node.path);

    // Opens or closes a folder. Its own button, well clear of the checkbox.
    var caret = document.createElement("button");
    caret.type = "button";
    caret.className = "caret" + (isDir ? (isOpen ? " open" : "") : " leaf");
    if (isDir) {
      caret.setAttribute("aria-label", (isOpen ? "Close " : "Open ") + node.path);
      caret.setAttribute("aria-expanded", isOpen ? "true" : "false");
      caret.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>';
      caret.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleExpand(node.path);
      });
    } else {
      caret.tabIndex = -1;
      caret.setAttribute("aria-hidden", "true");
    }
    row.appendChild(caret);

    // Decides whether this file or folder goes in the ZIP.
    var check = document.createElement("button");
    check.type = "button";
    var partial = node.included === "partial";
    check.className = "check" + (node.included === true ? " on" : (partial ? " partial" : ""));
    check.setAttribute("role", "checkbox");
    check.setAttribute("aria-checked", node.included === true ? "true" : (partial ? "mixed" : "false"));
    check.setAttribute("aria-label", "Include " + node.path);
    check.title = node.included === true ? "In the ZIP. Click to leave out." : (partial ? "Partly in the ZIP. Click to include all of it." : "Left out. Click to include.");
    check.innerHTML = "<i>" + (node.included === true ? CHECK_SVG : (partial ? DASH_SVG : "")) + "</i>";
    check.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleNode(node);
    });
    row.appendChild(check);

    var name = document.createElement("span");
    name.className = "node-name" + (isDir ? " dir" : "");
    name.textContent = node.name + (isDir ? "/" : "");
    name.title = node.path;
    row.appendChild(name);

    var meta = document.createElement("span");
    meta.className = "node-meta";
    if (isDir) {
      if (node.included === false) {
        meta.textContent = plural(node.files, "file") + " left out";
      } else {
        var count = node.includedFiles === node.files ? plural(node.files, "file") : node.includedFiles + " of " + plural(node.files, "file");
        var size = document.createElement("b");
        size.textContent = formatBytes(node.includedSize || 0);
        if ((node.includedSize || 0) >= 1024 * 1024) meta.className += " heavy";
        meta.appendChild(document.createTextNode(count + " · "));
        meta.appendChild(size);
      }
    } else {
      var fsize = document.createElement("b");
      fsize.textContent = formatBytes(node.size || 0);
      if (node.large && node.included !== false) meta.className += " heavy";
      meta.appendChild(fsize);
    }
    if (!isDir && node.large && node.included !== false) {
      var tag = document.createElement("span");
      tag.className = "tag-large";
      tag.textContent = "large";
      row.appendChild(tag);
    }
    row.appendChild(meta);

    // The rest of the row does the obvious thing for its kind.
    row.addEventListener("click", function () {
      if (isDir) toggleExpand(node.path);
      else toggleNode(node);
    });

    wrap.appendChild(row);

    if (isDir && isOpen && node.children) {
      var childWrap = document.createElement("div");
      childWrap.className = "children";
      var kids = query ? node.children.filter(function (c) { return nodeMatchesFilter(c, query); }) : node.children;
      kids.forEach(function (c) { childWrap.appendChild(renderNode(c, query)); });
      wrap.appendChild(childWrap);
    }

    return wrap;
  }

  function toggleExpand(path) {
    if (state.expanded.has(path)) state.expanded.delete(path);
    else state.expanded.add(path);
    renderTree(state.tree);
  }

  function setAllExpanded(open) {
    state.expanded = new Set(["__root__"]);
    if (open) {
      (function walk(nodes) {
        nodes.forEach(function (n) {
          if (n.type === "dir") {
            state.expanded.add(n.path);
            walk(n.children || []);
          }
        });
      })(state.tree);
    }
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
    markDirty();
    renderPatterns();
    schedulePreview();
  }

  // ---- AI / Workspace / Advanced: bind config -> controls ----
  function renderConfigTabs() {
    var cfg = state.cfg;

    // AI
    document.getElementById("ai-enabled-toggle").classList.toggle("on", !!cfg.ai.enabled);
    document.querySelectorAll("#provider-grid .provider[data-provider]").forEach(function (p) {
      p.classList.toggle("sel", p.getAttribute("data-provider") === cfg.ai.provider);
    });
    document.getElementById("max-files-val").textContent = cfg.ai.max_files;
    document.getElementById("prompt-template").value = cfg.ai.prompt_template || "";

    // Workspace
    var ws = cfg.workspace_location;
    var known = ws === "git-root" || ws === "cwd";
    document.querySelectorAll("#workspace-radiogroup .radio").forEach(function (r) {
      var val = r.getAttribute("data-value");
      var isSel = known ? val === ws : val === "custom";
      r.classList.toggle("sel", isSel);
    });
    document.getElementById("workspace-custom-path").value = known ? "" : (ws || "");
    document.getElementById("scan-depth-val").textContent = cfg.scan_depth;

    // Advanced
    document.getElementById("max-size-val").textContent = cfg.limits.max_file_size_mb.toFixed(1) + " MB";
    document.getElementById("redact-toggle").classList.toggle("on", !!cfg.limits.redact_secrets);
    document.getElementById("guide-toggle").classList.toggle("on", cfg.zip_guide !== false);
    document.getElementById("retention-val").textContent = cfg.applied_zip_retention;
    document.getElementById("autoopen-toggle").classList.toggle("on", !!cfg.webui.auto_open);
    document.getElementById("port-input").value = cfg.webui.port === null || cfg.webui.port === undefined ? "" : cfg.webui.port;

    renderJsonPreview();
  }

  function bindConfigControls() {
    // AI enable toggle
    document.getElementById("ai-enabled-toggle").addEventListener("click", function () {
      state.cfg.ai.enabled = !state.cfg.ai.enabled;
      this.classList.toggle("on", state.cfg.ai.enabled);
      markDirty();
    });

    // Provider grid
    document.querySelectorAll("#provider-grid .provider[data-provider]").forEach(function (p) {
      p.addEventListener("click", function () {
        state.cfg.ai.provider = p.getAttribute("data-provider");
        renderConfigTabs();
        markDirty();
      });
    });

    // Max files stepper
    document.getElementById("max-files-stepper").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-step]");
      if (!btn) return;
      state.cfg.ai.max_files = Math.max(1, state.cfg.ai.max_files + parseInt(btn.getAttribute("data-step"), 10));
      document.getElementById("max-files-val").textContent = state.cfg.ai.max_files;
      markDirty();
    });

    // Prompt template
    document.getElementById("prompt-template").addEventListener("input", debounce(function (e) {
      state.cfg.ai.prompt_template = e.target.value;
      markDirty();
    }, 150));

    // Workspace radios
    document.querySelectorAll("#workspace-radiogroup .radio").forEach(function (r) {
      r.addEventListener("click", function (e) {
        var val = r.getAttribute("data-value");
        state.cfg.workspace_location = val;
        if (val === "custom") {
          setTimeout(function () { document.getElementById("workspace-custom-path").focus(); }, 0);
        }
        renderConfigTabs();
        markDirty();
      });
    });
    document.getElementById("workspace-custom-path").addEventListener("click", function (e) { e.stopPropagation(); });
    document.getElementById("workspace-custom-path").addEventListener("input", debounce(function (e) {
      state.cfg.workspace_location = "custom";
      state.cfg.workspace_custom_path = e.target.value;
      markDirty();
    }, 150));

    // Scan depth stepper
    document.getElementById("scan-depth-stepper").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-step]");
      if (!btn) return;
      state.cfg.scan_depth = Math.max(0, state.cfg.scan_depth + parseInt(btn.getAttribute("data-step"), 10));
      document.getElementById("scan-depth-val").textContent = state.cfg.scan_depth;
      markDirty();
    });

    // Max file size stepper (0.5 MB increments, floors at 0.1)
    document.getElementById("max-size-stepper").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-step]");
      if (!btn) return;
      var next = state.cfg.limits.max_file_size_mb + parseFloat(btn.getAttribute("data-step"));
      state.cfg.limits.max_file_size_mb = Math.max(0.1, Math.round(next * 10) / 10);
      document.getElementById("max-size-val").textContent = state.cfg.limits.max_file_size_mb.toFixed(1) + " MB";
      markDirty();
    });

    // Redact toggle
    document.getElementById("redact-toggle").addEventListener("click", function () {
      state.cfg.limits.redact_secrets = !state.cfg.limits.redact_secrets;
      this.classList.toggle("on", state.cfg.limits.redact_secrets);
      markDirty();
    });

    // CONTEXTZIP.md toggle
    document.getElementById("guide-toggle").addEventListener("click", function () {
      state.cfg.zip_guide = state.cfg.zip_guide === false;
      this.classList.toggle("on", state.cfg.zip_guide);
      markDirty();
    });

    // Retention stepper
    document.getElementById("retention-stepper").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-step]");
      if (!btn) return;
      state.cfg.applied_zip_retention = Math.max(1, state.cfg.applied_zip_retention + parseInt(btn.getAttribute("data-step"), 10));
      document.getElementById("retention-val").textContent = state.cfg.applied_zip_retention;
      markDirty();
    });

    // Auto-open toggle
    document.getElementById("autoopen-toggle").addEventListener("click", function () {
      state.cfg.webui.auto_open = !state.cfg.webui.auto_open;
      this.classList.toggle("on", state.cfg.webui.auto_open);
      markDirty();
    });

    // Port input — digits only, empty means "random" (null)
    document.getElementById("port-input").addEventListener("input", debounce(function (e) {
      var digits = e.target.value.replace(/[^0-9]/g, "").slice(0, 5);
      e.target.value = digits;
      state.cfg.webui.port = digits ? parseInt(digits, 10) : null;
      markDirty();
    }, 150));
  }

  // ---- server round-trips ----
  function applyPayload(data) {
    state.tree = data.tree;
    if (data.project) renderHeader(data);
    renderStats(data.stats);
    renderSuggestions(data.suggestions, data.stats);
    renderPatternList("exclude-list", state.always_exclude);
    renderPatternList("include-list", state.always_include);
    renderTree(state.tree);
    renderJsonPreview();
  }

  function loadInitial() {
    api("/api/state").then(function (data) {
      state.always_include = data.config.always_include.slice();
      state.always_exclude = data.config.always_exclude.slice();
      state.cfg.workspace_location = data.config.workspace_location || "git-root";
      state.cfg.scan_depth = data.config.scan_depth;
      state.cfg.ai = Object.assign({}, state.cfg.ai, data.config.ai);
      state.cfg.limits = Object.assign({}, state.cfg.limits, data.config.limits);
      state.cfg.applied_zip_retention = data.config.applied_zip_retention;
      state.cfg.webui = Object.assign({}, state.cfg.webui, data.config.webui);
      state.cfg.zip_guide = data.config.zip_guide !== false;
      applyPayload(data);
      renderConfigTabs();
    }).catch(function (err) {
      document.getElementById("project-name").textContent = "Could not load project";
      document.getElementById("project-path").textContent = err.message || "Unknown error";
      document.getElementById("trim").hidden = true;
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
    markDirty();
    renderPatterns();
    schedulePreview();
  });
  document.getElementById("add-pattern").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("add-btn").click();
  });

  // ---- suggestions and tree tools ----
  document.getElementById("trim-all").addEventListener("click", toggleAllSuggestions);
  document.getElementById("expand-all").addEventListener("click", function () { setAllExpanded(true); });
  document.getElementById("collapse-all").addEventListener("click", function () { setAllExpanded(false); });

  // ---- search ----
  document.getElementById("search-input").addEventListener("input", debounce(function (e) {
    state.filter = e.target.value;
    renderTree(state.tree);
  }, 100));

  // ---- reset (Files tab patterns only — matches prior behavior) ----
  document.getElementById("reset-btn").addEventListener("click", function () {
    state.always_include = [];
    state.always_exclude = [];
    markDirty();
    renderPatterns();
    schedulePreview();
    showToast("Reset include/exclude patterns");
  });

  // ---- save (posts the full config object from every tab) ----
  document.getElementById("save-btn").addEventListener("click", function (e) {
    var btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Saving…";
    api("/api/save", {
      method: "POST",
      body: JSON.stringify(buildConfigPayload()),
    }).then(function () {
      state.dirty = false;
      document.getElementById("overlay").classList.add("show");
      setTimeout(function () {
        api("/api/shutdown", { method: "GET" }).catch(function () {});
        // Try to close the tab automatically and hop back to the terminal.
        // Browsers only allow script-initiated close on windows/tabs that
        // were opened by a script, or ones whose entire session history is
        // just this one page — which this config-UI tab always is, since
        // it's a single page with no navigation. If the browser still
        // blocks it (some do, regardless), window.close() is a silent
        // no-op and the overlay's "you can close this tab" text — already
        // on screen — is the fallback the user follows manually.
        setTimeout(function () {
          try { window.close(); } catch (e) { /* ignored — fallback is the overlay text */ }
        }, 300);
      }, 900);
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = "Save config.json";
      showToast("Save failed — check the terminal.");
    });
  });

  initTabs();
  bindConfigControls();
  loadInitial();
})();
</script>
</body>
</html>
"""

TOKEN_ERROR_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>contextzip</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0a0c0f; color: #e8eaed;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .card { max-width: 420px; text-align: center; padding: 32px; }
  code { background: #1a1e24; border: 1px solid #252b33; padding: 2px 6px; border-radius: 5px;
         font-family: ui-monospace, "SF Mono", Consolas, monospace; }
</style></head>
<body>
  <div class="card">
    <h2>Invalid or missing session token</h2>
    <p>This link has expired or is incomplete. Go back to your terminal and
    run <code>contextzip config --ui</code> again to get a fresh link.</p>
  </div>
</body></html>
"""
