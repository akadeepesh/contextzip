"""
Draws assets/readme-demo.svg, the three-step picture at the top of the README.

    python tools/readme_demo.py

It's generated so it can be redrawn when the CLI's output changes: edit the
PACK and APPLY lines below to match what `cz` and `cz apply-zip` print, and
run this again. The numbers are from a real run on the website's repository;
update them together, never one at a time.

Everything sits on dark cards, so the picture reads the same on GitHub's
light and dark themes and on PyPI. Only system fonts are used: an SVG
shown through <img> can't load any others.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "readme-demo.svg"

# ── Palette ──────────────────────────────────────────────────────────────
CARD = "#0d1117"
BAR = "#161b22"
EDGE = "#30363d"
TEXT = "#e6edf3"
DIM = "#8b949e"
FAINT = "#6e7681"
GREEN = "#56d364"
AMBER = "#e3b341"
BLUE = "#79c0ff"
BUBBLE = "#1c2430"

MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# ── Layout ───────────────────────────────────────────────────────────────
W, H = 1200, 372
CARD_W, CARD_H, GAP, TOP = 368, 340, 48, 16
XS = [0, CARD_W + GAP, 2 * (CARD_W + GAP)]
PAD = 16
LINE = 23

# ── What each terminal prints: a list of lines, each a list of (text, colour) ──
OK = ("✓ ", GREEN)
PACK = [
    [("$ ", FAINT), ("cz", TEXT)],
    [],
    [OK, ("Detected Next.js + Node.js + Python", TEXT)],
    [OK, ("Scanned 37949 files & Packed 62 files", TEXT)],
    [("  62 included, 37887 excluded", DIM)],
    [("  323 KB → 114 KB, ≈ 83k tokens", DIM)],
    [OK, ("Saved to codebase.zip", TEXT), ("  · snapshot #14", DIM)],
    [OK, ("Ready to paste", TEXT)],
]
APPLY = [
    [("$ ", FAINT), ("cz apply-zip", TEXT)],
    [],
    [OK, ("Compared with snapshot #14", TEXT)],
    [OK, ("Classified 6 files", TEXT)],
    [("  3 modified, 1 merged, 2 new", DIM)],
    [("  ≈ ", BLUE), ("src/auth/login.ts", TEXT)],
    [("    your edits kept, merged around", DIM)],
    [OK, ("Applied 6 files", TEXT)],
]
CAPTIONS = [
    ("Finds your stack, skips 37,887 files of noise,", "and never packs your secrets."),
    ("The ZIP is on your clipboard as a file.", "Paste it into any chat's upload box."),
    ("Three-way merge against what you sent.", "Risky changes wait for your OK."),
]
TITLES = ["1   Pack", "2   Paste", "3   Apply"]


def text(x: float, y: float, spans, *, size: float = 14, family: str = MONO, weight: int = 400, anchor: str = "start") -> str:
    if isinstance(spans, str):
        spans = [(spans, TEXT)]
    body = "".join(f'<tspan fill="{colour}">{escape(chunk)}</tspan>' for chunk, colour in spans)
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}" xml:space="preserve">{body}</text>'
    )


def card(x: int, title: str, caption: tuple[str, str]) -> list[str]:
    out = [
        f'<rect x="{x + 0.5}" y="{TOP + 0.5}" width="{CARD_W - 1}" height="{CARD_H - 1}" rx="12" fill="{CARD}" stroke="{EDGE}"/>',
        # title bar: rounded on top, square where it meets the body
        f'<path d="M{x + 1} {TOP + 40} v-27 a12 12 0 0 1 12 -12 h{CARD_W - 26} a12 12 0 0 1 12 12 v27 z" fill="{BAR}"/>',
        f'<line x1="{x + 1}" y1="{TOP + 40.5}" x2="{x + CARD_W - 1}" y2="{TOP + 40.5}" stroke="{EDGE}"/>',
    ]
    for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        out.append(f'<circle cx="{x + PAD + 6 + i * 18}" cy="{TOP + 20.5}" r="5.5" fill="{colour}" opacity="0.9"/>')
    out.append(text(x + CARD_W - PAD, TOP + 25, [(title, TEXT)], size=14, family=SANS, weight=600, anchor="end"))
    # caption, on a rule at the bottom of the card
    out.append(f'<line x1="{x + 1}" y1="{TOP + CARD_H - 62.5}" x2="{x + CARD_W - 1}" y2="{TOP + CARD_H - 62.5}" stroke="{EDGE}"/>')
    out.append(text(x + PAD, TOP + CARD_H - 39, [(caption[0], DIM)], size=13.5, family=SANS))
    out.append(text(x + PAD, TOP + CARD_H - 19, [(caption[1], DIM)], size=13.5, family=SANS))
    return out


def terminal(x: int, lines) -> list[str]:
    out = []
    y = TOP + 40 + 30
    for line in lines:
        if line:
            out.append(text(x + PAD, y, line))
        y += LINE
    return out


def chip(x: float, y: float, name: str, detail: str) -> list[str]:
    """A file attachment: a small archive icon, the name, a dim detail."""
    width = 48 + len(name) * 8.0 + (len(detail) * 7.2 + 10 if detail else 0)
    out = [f'<rect x="{x}" y="{y}" width="{width:.0f}" height="30" rx="8" fill="{CARD}" stroke="{EDGE}"/>']
    ix, iy = x + 10, y + 7
    out.append(f'<rect x="{ix}" y="{iy}" width="13" height="16" rx="2.5" fill="none" stroke="{AMBER}" stroke-width="1.4"/>')
    for k in range(3):
        out.append(f'<rect x="{ix + 5.4}" y="{iy + 2.5 + k * 4}" width="2.2" height="2.2" fill="{AMBER}"/>')
    spans = [(name, TEXT)] + ([("  " + detail, DIM)] if detail else [])
    out.append(text(x + 31, y + 20, spans, size=13.5))
    return out


def chat(x: int) -> list[str]:
    out = []
    left, right = x + PAD, x + CARD_W - PAD
    top = TOP + 40 + 16

    # you: a ZIP and a sentence, right-aligned
    bw = 300
    bx = right - bw
    out.append(f'<rect x="{bx}" y="{top}" width="{bw}" height="84" rx="12" fill="{BUBBLE}"/>')
    out += chip(bx + 12, top + 12, "codebase.zip", "114 KB")
    out.append(text(bx + 14, top + 67, [("Add rate limiting to the login endpoint.", TEXT)], size=13.5, family=SANS))

    # the AI: a sentence and a ZIP, left-aligned
    ay = top + 100
    out.append(f'<circle cx="{left + 11}" cy="{ay + 12}" r="11" fill="none" stroke="{EDGE}"/>')
    out.append(text(left + 11, ay + 16.5, [("AI", DIM)], size=10, family=SANS, weight=600, anchor="middle"))
    out.append(text(left + 32, ay + 16.5, [("Done. I changed 4 files and added 2.", TEXT)], size=13.5, family=SANS))
    out += chip(left + 32, ay + 30, "reply.zip", "")
    out.append(text(left + 32, ay + 84, [("Claude · ChatGPT · Gemini · any upload box", FAINT)], size=12.5, family=SANS))
    return out


def arrow(x: float) -> str:
    y = TOP + CARD_H / 2 - 12
    return (
        f'<g stroke="{DIM}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">'
        f'<line x1="{x - 13}" y1="{y}" x2="{x + 12}" y2="{y}"/>'
        f'<polyline points="{x + 5},{y - 7} {x + 12},{y} {x + 5},{y + 7}"/></g>'
    )


def build() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        f'aria-labelledby="t d">',
        '<title id="t">contextzip in three steps</title>',
        '<desc id="d">Step 1, pack: running cz scans 37,949 files and packs the 62 that matter into a 114 KB ZIP. '
        "Step 2, paste: the ZIP is attached in an AI chat with a request, and the AI returns a ZIP of changed files. "
        "Step 3, apply: running cz apply-zip merges the six returned files back into the project, keeping your own edits.</desc>",
    ]
    for i, x in enumerate(XS):
        parts += card(x, TITLES[i], CAPTIONS[i])
    parts += terminal(XS[0], PACK)
    parts += chat(XS[1])
    parts += terminal(XS[2], APPLY)
    parts.append(arrow(XS[0] + CARD_W + GAP / 2))
    parts.append(arrow(XS[1] + CARD_W + GAP / 2))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)} ({OUT.stat().st_size:,} bytes)")
