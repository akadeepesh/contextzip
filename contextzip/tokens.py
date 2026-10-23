"""
tokens.py — A rough token count, so you can tell whether a ZIP will fit.

AI tools count tokens, not bytes, and every model's tokenizer differs.
Source code averages roughly four bytes per token, which is close enough to
answer "will this fit in the chat?" without shipping a tokenizer. Always
shown with "≈" — it's an estimate, never a promise.
"""

from __future__ import annotations

_BYTES_PER_TOKEN = 4


def estimate_tokens(text_bytes: int) -> int:
    """Estimated tokens for *text_bytes* bytes of text (binary files excluded by the caller)."""
    return (max(text_bytes, 0) + _BYTES_PER_TOKEN - 1) // _BYTES_PER_TOKEN


def format_tokens(tokens: int) -> str:
    """'≈ 850 tokens', '≈ 4.2k tokens', '≈ 46k tokens', '≈ 1.3M tokens'."""
    if tokens < 1000:
        n = str(tokens)
    elif tokens < 10_000:
        n = f"{tokens / 1000:.1f}k"
    elif tokens < 999_500:
        n = f"{round(tokens / 1000)}k"
    else:
        n = f"{tokens / 1_000_000:.1f}M"
    return f"≈ {n} tokens"
