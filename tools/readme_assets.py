"""
Draws two more pictures:

    python tools/readme_assets.py

assets/terminal.svg        an animated terminal: `cz`, then `cz apply-zip`, line by line, looping
assets/social-preview.html the 1280x640 card for GitHub's social preview; screenshot it to a PNG and
                           upload it under Settings -> General -> Social preview
"""

from __future__ import annotations

from html import escape
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
TEXT, DIM, FAINT, GREEN, BLUE = "#e6edf3", "#8b949e", "#6e7681", "#56d364", "#79c0ff"

# (seconds at which the line appears, spans)
LINES = [
    (0.4, [("$ ", FAINT), ("cz", TEXT)]),
    (1.3, [("✓ ", GREEN), ("Detected Next.js + Node.js + Python", TEXT)]),
    (1.9, [("✓ ", GREEN), ("Scanned 37949 files & Packed 62 files", TEXT), ("  · 323 KB → 114 KB, ≈ 83k tokens", DIM)]),
    (2.5, [("✓ ", GREEN), ("Saved to codebase.zip", TEXT), ("  · snapshot #14", DIM)]),
    (3.0, [("✓ ", GREEN), ("Ready to paste", TEXT)]),
    (4.6, [("  # paste into Claude or ChatGPT, ask for a change, download the reply", FAINT)]),
    (6.2, [("$ ", FAINT), ("cz apply-zip", TEXT)]),
    (7.1, [("✓ ", GREEN), ("Compared with snapshot #14", TEXT)]),
    (7.7, [("✓ ", GREEN), ("Classified 6 files", TEXT), ("  · 3 modified, 1 merged, 2 new", DIM)]),
    (8.2, [("  ≈ ", BLUE), ("src/auth/login.ts", TEXT), ("  your edits kept, merged around", DIM)]),
    (8.9, [("✓ ", GREEN), ("Applied 6 files", TEXT)]),
]
TOTAL = 13  # seconds per loop


def terminal() -> str:
    w, line, top = 860, 26, 74
    h = top + len(LINES) * line + 22
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        'aria-label="A terminal: cz packs the project, then cz apply-zip merges the AI\'s reply back">',
        "<style>",
        f"text{{font-family:{MONO};font-size:15px;white-space:pre}}",
        ".l{opacity:0;animation-duration:%ds;animation-iteration-count:infinite;animation-timing-function:step-end}" % TOTAL,
    ]
    for i, (at, _) in enumerate(LINES):
        pct = at / TOTAL * 100
        out.append(f"@keyframes k{i}{{0%{{opacity:0}}{pct:.1f}%{{opacity:1}}97%{{opacity:1}}100%{{opacity:0}}}}.l{i}{{animation-name:k{i}}}")
    out += [
        "@media (prefers-reduced-motion: reduce){.l{animation:none;opacity:1}}",
        "</style>",
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" fill="#0d1117" stroke="#30363d"/>',
        f'<path d="M1 44 v-31 a12 12 0 0 1 12 -12 h{w - 26} a12 12 0 0 1 12 12 v31 z" fill="#161b22"/>',
        f'<line x1="1" y1="44.5" x2="{w - 1}" y2="44.5" stroke="#30363d"/>',
    ]
    for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        out.append(f'<circle cx="{24 + i * 20}" cy="22.5" r="6" fill="{colour}"/>')
    out.append(f'<text x="{w / 2}" y="27" text-anchor="middle" fill="{DIM}" style="font-family:{SANS};font-size:13px">my-app</text>')
    for i, (_, spans) in enumerate(LINES):
        body = "".join(f'<tspan fill="{c}">{escape(t)}</tspan>' for t, c in spans)
        out.append(f'<text class="l l{i}" x="22" y="{top + i * line}" xml:space="preserve">{body}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def social() -> str:
    logo = (ASSETS / "logo.svg").read_text(encoding="utf-8")
    demo = (ASSETS / "readme-demo.svg").read_text(encoding="utf-8")
    return f"""<!doctype html><meta charset="utf-8">
<style>
  html,body{{margin:0}} body{{width:1280px;height:640px;background:#0a0d12;color:{TEXT};font-family:{SANS};overflow:hidden;
    background-image:radial-gradient(900px 420px at 50% -10%, rgba(86,211,100,.16), transparent)}}
  .top{{display:flex;align-items:center;gap:22px;padding:56px 72px 0}}
  .top svg{{width:76px;height:76px}} h1{{font-size:60px;margin:0;letter-spacing:-.03em}}
  p{{font-size:38px;line-height:1.25;margin:26px 72px 0;font-weight:600;letter-spacing:-.02em}}
  p span{{color:{GREEN}}} .demo{{margin:44px 72px 0}} .demo svg{{width:1136px;height:auto}}
</style>
<div class="top">{logo}<h1>contextzip</h1></div>
<p>Send your codebase to any AI chat. <span>Get the changes back.</span></p>
<div class="demo">{demo}</div>
"""


if __name__ == "__main__":
    (ASSETS / "terminal.svg").write_text(terminal(), encoding="utf-8", newline="\n")
    (ASSETS / "social-preview.html").write_text(social(), encoding="utf-8", newline="\n")
    print("wrote assets/terminal.svg and assets/social-preview.html")
