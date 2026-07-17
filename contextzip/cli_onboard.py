"""
cli_onboard.py — Gemini API key onboarding flow.

Handles the first-time (and reset) interactive setup that guides the user
through getting a free Gemini API key from Google AI Studio and saving it
to disk.

Imported by cli.py; nothing here imports from cli.py (no circular deps).
"""

from __future__ import annotations

import webbrowser

import click
from rich.console import Console
from rich.panel import Panel

from contextzip.config import save_api_key, config_path

console = Console()

GEMINI_KEY_URL = "https://aistudio.google.com/apikey"


def open_browser_silent(url: str) -> None:
    """
    Open *url* in the default browser, suppressing all stderr output.

    On Linux, xdg-open frequently emits KDE/DBus/MIME warnings to stderr
    that are completely unrelated to contextzip and appear right on top of
    the API key prompt, confusing users. We redirect both stdout and stderr
    to /dev/null to keep the terminal clean.
    """
    import subprocess

    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except (FileNotFoundError, OSError):
        pass
    # macOS / Windows fallback
    try:
        webbrowser.open(url)
    except Exception:
        pass


def onboard_api_key(*, con: Console = console) -> str | None:
    """
    Guide the user through getting and saving a Gemini API key.

    Returns the key string if successfully configured, or None if the
    user declined or provided nothing.
    """
    con.print()
    con.print(
        Panel(
            "  [bold]--prompt[/] requires a free Gemini API key.\n\n"
            "  contextzip uses [bold cyan]Google AI Studio[/] — "
            "no credit card needed.\n\n"
            f"  [dim]Get your free key at:[/]\n"
            f"  [bold cyan link={GEMINI_KEY_URL}]{GEMINI_KEY_URL}[/]",
            title="[bold yellow]Gemini API Key Required[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    con.print()

    open_browser = click.confirm(
        "  Open Google AI Studio in your browser now?",
        default=True,
    )
    if open_browser:
        open_browser_silent(GEMINI_KEY_URL)
        con.print("  [dim]Browser opened. Generate a key, then come back here.[/]")

    con.print()
    con.print("  [dim]─────────────────────────────────────────────[/]")
    con.print("  Once you have your key, paste it below and press [bold]Enter[/].")
    con.print("  [dim]─────────────────────────────────────────────[/]")
    con.print()
    key = click.prompt(
        "  Paste your API key",
        hide_input=True,
        prompt_suffix=" › ",
    ).strip()

    if not key:
        con.print("\n  [red]No key provided. Exiting.[/]")
        return None

    # Basic sanity check — Gemini keys start with "AIza"
    if not key.startswith("AIza"):
        con.print(
            "\n  [yellow]⚠[/]  That doesn't look like a Gemini key "
            "(expected it to start with [cyan]AIza[/]).\n"
            "  [dim]Double-check you copied the full key from AI Studio.[/]"
        )
        if not click.confirm("\n  Save it anyway?", default=False):
            return None

    try:
        save_api_key(key)
        con.print(
            f"\n  [green]✓[/]  Key saved to "
            f"[dim]{config_path()}[/]\n"
            f"  [dim]You won't be asked again. "
            f"Run [cyan]contextzip config --reset-key[/] to change it.[/]"
        )
    except OSError as exc:
        con.print(
            f"\n  [yellow]⚠[/]  Could not save key to disk ([dim]{exc}[/]).\n"
            f"  [dim]Set [cyan]GEMINI_API_KEY[/] as an environment variable "
            f"to avoid this prompt next time.[/]"
        )

    con.print()
    return key


# ---------------------------------------------------------------------------
# Claude session key onboarding — used by eod/handoff's optional case-2
# (diverged-from-Claude) artifact fetching.
# ---------------------------------------------------------------------------


def onboard_session_key(*, con: Console = console) -> str | None:
    from contextzip.config import save_session_key

    con.print()
    con.print(
        Panel(
            "  [bold]eod[/] / [bold]handoff[/] can diff your codebase against the "
            "files Claude\n  last gave you, if you give it your Claude.ai session cookie.\n\n"
            "  1. Open [bold cyan]claude.ai[/] and make sure you're logged in.\n"
            "  2. Open DevTools (F12) → Application/Storage → Cookies → "
            "https://claude.ai\n"
            "  3. Copy the value of the cookie named [cyan]sessionKey[/]\n\n"
            "  [dim]This is equivalent to your account password while it's valid —\n"
            "  it's stored locally on this machine and only ever sent to claude.ai.[/]",
            title="[bold yellow]Claude Session Key (optional)[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    con.print()

    key = click.prompt(
        "  Paste your sessionKey cookie value (or press Enter to skip)",
        default="",
        show_default=False,
        hide_input=True,
        prompt_suffix=" › ",
    ).strip()

    if not key:
        con.print(
            "\n  [dim]Skipped — eod/handoff will still work, just without the "
            "diverged-from-Claude check.[/]"
        )
        return None

    try:
        save_session_key(key)
        con.print(
            f"\n  [green]✓[/]  Key saved to [dim]{config_path()}[/]\n"
            f"  [dim]Run [cyan]contextzip config --reset-session-key[/] to change it.[/]"
        )
    except OSError as exc:
        con.print(
            f"\n  [yellow]⚠[/]  Could not save key to disk ([dim]{exc}[/]).\n"
            f"  [dim]Set [cyan]CLAUDE_SESSION_KEY[/] as an environment variable "
            f"to avoid this prompt next time.[/]"
        )

    con.print()
    return key
