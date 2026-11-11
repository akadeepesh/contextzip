"""
models.py — Does the ZIP fit? Context windows of the chat apps people paste into.

contextzip counts tokens with o200k_base (see tokens.py). Each target says
how big its context window is and how to scale an o200k count to that
model's own tokenizer. The numbers are estimates: shown with "≈" unless the
model is known to use o200k_base itself.

Windows are the chat apps' limits on the plan most people pay for (about
$20 a month), not the larger API limits, so "fits" doesn't over-promise.
A project can change any of them with "tokens.windows" in
.contextzip/config.json, or add its own target there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    key: str
    """What --for accepts: "claude", "chatgpt", "gemini"."""
    name: str
    """Shown to the user: "Claude", "ChatGPT", "Gemini"."""
    window: int
    """Context window in tokens."""
    factor: float
    """o200k tokens * factor = estimated tokens for this model's tokenizer."""
    exact: bool
    """True only when the model uses o200k_base itself."""


# Checked against the vendors' own pages on 2026-11-12. Re-check each release.
#
# Claude: 1M on paid plans for the current models (Fable 5.2, Opus 5.5,
#   Sonnet 5.5, Haiku 5.5).
#   https://support.claude.com/en/articles/8606394-how-large-is-the-context-window-on-paid-claude-plans
#   Factor 1.20: Anthropic's guidance is that o200k undercounts Claude's
#   tokenizer by about 15-20% on typical text (more on code).
#   https://platform.claude.com/docs/en/build-with-claude/token-counting
# ChatGPT: 256K on Plus with reasoning ("GPT Reasoning total context
#   window"; GPT Instant is 54K, Pro gets 400K).
#   https://chatgpt.com/pricing/
#   OpenAI doesn't document GPT-6's tokenizer (tiktoken maps GPT-5 to
#   o200k_base), so the count is not marked exact.
# Gemini: 1M on Google AI Pro and Ultra (128K on AI Plus, 32K without a plan).
#   https://support.google.com/gemini/answer/16275805
#   No published comparison with o200k: factor 1.00.
TARGETS: tuple[Target, ...] = (
    Target("claude", "Claude", 1_000_000, 1.20, False),
    Target("chatgpt", "ChatGPT", 256_000, 1.00, False),
    Target("gemini", "Gemini", 1_000_000, 1.00, False),
)


def fit(total: int, target: Target) -> tuple[int, float]:
    """
    (tokens_for_target, share): the o200k *total* scaled to *target*'s
    tokenizer (rounded up), and that count as a share of its window.
    """
    # round() first, so 100 * 1.2 is 120 and not 121 (from 120.00000000000001).
    tokens_for_target = max(0, math.ceil(round(total * target.factor, 6)))
    return tokens_for_target, tokens_for_target / target.window


def resolve(keys: list[str] | None, overrides: dict | None) -> list[Target]:
    """
    The targets to report. *keys* None means all of them, in TARGETS order
    (plus any extra ones from *overrides*); otherwise the named ones, in
    the order given, without repeats.

    *overrides* is the project config's tokens.windows: {"claude": 500000}
    replaces that target's window; a key not in TARGETS adds a target named
    after the key, with factor 1.0 and not exact. Raises ValueError for an
    unknown key, naming the valid ones.
    """
    overrides = overrides or {}
    by_key: dict[str, Target] = {}
    for target in TARGETS:
        window = overrides.get(target.key)
        by_key[target.key] = target if window is None else Target(target.key, target.name, window, target.factor, target.exact)
    for key, window in overrides.items():
        if key not in by_key:
            by_key[key] = Target(key, key, window, 1.0, False)

    if keys is None:
        return list(by_key.values())

    chosen: list[Target] = []
    for raw in keys:
        key = raw.strip().lower()
        if key not in by_key:
            valid = ", ".join(by_key)
            raise ValueError(f"Unknown model {raw!r}. Choose from: {valid}.")
        if by_key[key] not in chosen:
            chosen.append(by_key[key])
    return chosen
