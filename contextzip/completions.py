"""
completions.py — `contextzip completions`: tab completion for bash, zsh,
fish and PowerShell, for both command names (`contextzip` and `cz`).

bash, zsh and fish scripts come from click's own shell_completion: the
shell calls the program back with `_CZ_COMPLETE=…_complete` set, and click
answers. click has no PowerShell support, so PowerShell gets a small
Register-ArgumentCompleter script that calls
`contextzip completions --complete -- WORDS…`, a hidden mode that prints
the matching subcommands and options, one per line.

`--install` writes the script into the shell's startup file between two
marker lines (pathsetup.write_marked_block), so installing again replaces
the block instead of adding a second one. fish gets its own files in
~/.config/fish/completions/, which fish loads by command name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SHELLS = ("bash", "zsh", "fish", "powershell")
PROGS = ("contextzip", "cz")
START = "# >>> contextzip completions >>>"
END = "# <<< contextzip completions <<<"

_ZSH_PREAMBLE = "(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }"

POWERSHELL_SCRIPT = r"""Register-ArgumentCompleter -Native -CommandName 'contextzip', 'cz' -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $text = $commandAst.ToString()
    $upTo = [Math]::Max(0, [Math]::Min($text.Length, $cursorPosition - $commandAst.Extent.StartOffset))
    $before = $text.Substring(0, $upTo)
    $words = @($before -split '\s+' | Where-Object { $_ -ne '' } | Select-Object -Skip 1)
    $new = @()
    if ($before -match '\s$') { $new = @('--new') }
    $exe = $commandAst.CommandElements[0].ToString()
    & $exe completions --complete @new -- @words 2>$null | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}"""


def detect_shell(environ: dict | None = None, os_name: str | None = None) -> str | None:
    """
    The user's shell: $SHELL's name on macOS and Linux; on Windows
    PowerShell, unless $SHELL says otherwise (Git Bash sets it to bash).
    None when it can't be told.
    """
    environ = os.environ if environ is None else environ
    os_name = os.name if os_name is None else os_name
    shell = environ.get("SHELL", "").strip()
    if shell:
        name = Path(shell.replace("\\", "/")).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name in ("pwsh", "powershell"):
            return "powershell"
        return name if name in SHELLS else None
    return "powershell" if os_name == "nt" else None


def _click_source(cli, shell: str, prog: str) -> str:
    from click.shell_completion import get_completion_class

    cls = get_completion_class(shell)
    if cls is None:  # pragma: no cover - click ships all three
        raise ValueError(f"click has no {shell} completion")
    var = f"_{prog}_COMPLETE".replace("-", "_").upper()
    return cls(cli, {}, prog, var).source().strip("\n")


def script(cli, shell: str, progs: tuple[str, ...] = PROGS) -> str:
    """The completion script for *shell*, covering every name in *progs*."""
    if shell == "powershell":
        return POWERSHELL_SCRIPT + "\n"
    parts = [_click_source(cli, shell, prog) for prog in progs]
    if shell == "zsh":
        parts.insert(0, _ZSH_PREAMBLE)
    return "\n\n".join(parts) + "\n"


def candidates(cli, words: list[str], *, new_word: bool = False) -> list[str]:
    """
    What can come next after *words* (the command line without the program
    name). The last word is the one being completed, unless *new_word* says
    the cursor is after a space. Subcommands and options only: paths are
    left to the shell.
    """
    from click.shell_completion import ShellComplete

    words = list(words)
    incomplete = "" if new_word or not words else words.pop()
    comp = ShellComplete(cli, {}, "contextzip", "_CONTEXTZIP_COMPLETE")
    return [item.value for item in comp.get_completions(words, incomplete) if item.type == "plain"]


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def _fish_dir(home: Path) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(base) / "fish" / "completions"


def powershell_profiles(home: Path) -> list[tuple[Path, str | None]]:
    """
    [(profile path, execution policy)] for each PowerShell found: Windows
    PowerShell and PowerShell 7 keep separate profiles. Falls back to
    Documents/PowerShell/profile.ps1 when neither answers.
    """
    found: list[tuple[Path, str | None]] = []
    for exe in ("pwsh", "powershell"):
        path = shutil.which(exe)
        if path is None:
            continue
        try:
            out = subprocess.run(
                [path, "-NoProfile", "-NonInteractive", "-Command", "$PROFILE.CurrentUserAllHosts; Get-ExecutionPolicy"],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if lines:
            profile = Path(lines[0])
            policy = lines[1] if len(lines) > 1 else None
            if all(profile != p for p, _ in found):
                found.append((profile, policy))
    return found or [(home / "Documents" / "PowerShell" / "profile.ps1", None)]


def install(cli, shell: str, *, home: Path | None = None, profiles=None) -> list[tuple[Path, str | None]]:
    """
    Write the completions for *shell* into its startup file(s). Returns
    [(path, PowerShell execution policy or None)]. Raises OSError.
    """
    from contextzip import pathsetup

    home = home or Path.home()
    if shell == "fish":
        targets = []
        folder = _fish_dir(home)
        for prog in PROGS:
            path = folder / f"{prog}.fish"
            pathsetup.write_marked_block(path, START, END, script(cli, "fish", (prog,)))
            targets.append((path, None))
        return targets
    if shell == "powershell":
        found = profiles(home) if profiles is not None else powershell_profiles(home)
        for path, _policy in found:
            pathsetup.write_marked_block(path, START, END, POWERSHELL_SCRIPT)
        return found
    rc = pathsetup.shell_rc(home, shell)
    pathsetup.write_marked_block(rc, START, END, script(cli, shell))
    return [(rc, None)]


def display(path: Path, home: Path | None = None) -> str:
    """*path* with the home folder shown as ~."""
    home_str = str(home or Path.home())
    text = str(path)
    return "~" + text[len(home_str):] if text.startswith(home_str) else text


def blocks_policy(policy: str | None) -> bool:
    """True when PowerShell won't run a profile script under *policy*."""
    return bool(policy) and policy.strip().lower() in ("restricted", "allsigned")


__all__ = [
    "SHELLS",
    "PROGS",
    "START",
    "END",
    "detect_shell",
    "script",
    "candidates",
    "install",
    "powershell_profiles",
    "display",
    "blocks_policy",
]
