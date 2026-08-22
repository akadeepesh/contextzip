"""
watcher.py — Spawns a child process, buffers its output, detects errors,
and packages debug context on demand.

Architecture
────────────
- Child process is spawned via subprocess.Popen with stdout/stderr piped.
- Two daemon threads drain stdout and stderr simultaneously, writing to
  the terminal and appending to a shared rolling buffer.
- The main thread watches a threading.Event for error detection signals.
- When an error is detected, the D/S prompt is rendered and a single
  keypress is read from stdin.
- On Ctrl+C (SIGINT), the main thread catches KeyboardInterrupt,
  terminates the child, and offers a final D/S prompt if no errors were
  already packaged this session.

Windows note
────────────
PTY / raw-mode stdin is not used. Dev servers (npm run dev, manage.py
runserver) don't read stdin, so we own it safely. On Windows, msvcrt is
used for non-blocking keypress detection; on Unix, termios + select.

Color output
────────────
The child's stdout/stderr is forwarded byte-for-byte to sys.stdout /
sys.stderr so colour and formatting are preserved for the developer.
The buffer stores the decoded text (UTF-8 with replacement) for parsing.
"""

from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import threading
import zipfile
from collections import deque
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from contextzip.error_parser import process_buffer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Rolling buffer cap — last N lines kept in memory.
# 2000 lines at ~120 chars avg ≈ 240 KB — well within reason for any process.
_BUFFER_MAX_LINES = 2000

# After an error is detected, wait this long (seconds) for the output to
# settle before showing the D/S prompt. Prevents the prompt from appearing
# mid-stream while the error is still printing.
_SETTLE_SECONDS = 0.4

# Keypress characters we act on in the D/S prompt (case-insensitive)
_KEY_DEBUG = {"d", "D"}
_KEY_SKIP = {"s", "S"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_watch(
    command: list[str],
    project_dir: Path,
    ecosystems: list[str],
    ecosystem_display: str,
    console: Console,
) -> int:
    """
    Spawn *command* as a child process and watch its output for errors.

    Blocks until the child exits (or Ctrl+C). Returns the child's exit code.

    Parameters
    ----------
    command:
        The command + args to run, e.g. ["npm", "run", "dev"].
    project_dir:
        Absolute path to the project root (used for ZIP workspace + path resolution).
    ecosystems:
        DetectionResult.ecosystems list, e.g. ["Next.js", "Node.js"].
    ecosystem_display:
        Human-readable display string for prompt.txt.
    console:
        Rich Console instance for rendering panels.
    """
    buffer: deque[str] = deque(maxlen=_BUFFER_MAX_LINES)
    buffer_lock = threading.Lock()

    # Signals from reader threads → main thread
    error_detected_event = threading.Event()
    output_queue: queue.Queue[tuple[str, bytes]] = queue.Queue()

    packaged_count = 0  # how many debug ZIPs we've produced this session

    # ── Spawn child ──────────────────────────────────────────────────────────
    try:
        child = subprocess.Popen(
            command,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Don't create a new process group — let Ctrl+C propagate naturally
        )
    except FileNotFoundError:
        console.print(
            Panel.fit(
                f"[red]Command not found:[/] [bold]{command[0]}[/]\n"
                f"[dim]Make sure it's installed and on your PATH.[/]",
                border_style="red",
                padding=(0, 2),
            )
        )
        return 1
    except PermissionError:
        console.print(
            Panel.fit(
                f"[red]Permission denied:[/] cannot execute [bold]{command[0]}[/]",
                border_style="red",
                padding=(0, 2),
            )
        )
        return 1

    console.print(
        f"[dim]\\[contextzip][/] watching [bold]{' '.join(command)}[/] "
        f"[dim]· Ctrl+C to stop[/]"
    )
    console.print()

    # ── Reader threads ───────────────────────────────────────────────────────
    # Each thread drains one pipe, writes raw bytes to a queue for the
    # main thread to forward, and also appends decoded text to the buffer.

    def _reader(pipe: io.RawIOBase, stream_name: str) -> None:
        """Drain *pipe*, enqueue raw bytes for forwarding, append text to buffer."""
        try:
            for raw_line in pipe:
                output_queue.put((stream_name, raw_line))
                text = raw_line.decode("utf-8", errors="replace")
                with buffer_lock:
                    # deque with maxlen handles the cap automatically
                    buffer.append(text.rstrip("\n").rstrip("\r"))
        finally:
            output_queue.put((stream_name, None))  # sentinel

    stdout_thread = threading.Thread(
        target=_reader, args=(child.stdout, "stdout"), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_reader, args=(child.stderr, "stderr"), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    # ── Error detection thread ───────────────────────────────────────────────
    # Watches the buffer for error patterns. When found, sets an event so
    # the main loop can handle the D/S prompt after output settles.

    _last_error_hash: str | None = None
    _detection_lock = threading.Lock()

    def _detect_loop() -> None:
        nonlocal _last_error_hash
        import hashlib
        import time

        from contextzip.error_parser import detect_error_block

        while child.poll() is None:
            time.sleep(0.3)  # poll every 300ms

            if error_detected_event.is_set():
                # Already signalled — don't fire again until cleared
                continue

            with buffer_lock:
                lines = list(buffer)

            if not lines:
                continue

            result = detect_error_block(lines, ecosystems)
            if result is None:
                continue

            error_block, _ = result
            # Deduplicate: hash the first 3 lines of the error block
            signature_lines = [l for l in error_block.splitlines() if l.strip()][:3]  # noqa: E741
            signature = "\n".join(signature_lines)
            h = hashlib.md5(signature.encode()).hexdigest()

            with _detection_lock:
                if h == _last_error_hash:
                    continue  # same error, already prompted
                _last_error_hash = h

            error_detected_event.set()

    detect_thread = threading.Thread(target=_detect_loop, daemon=True)
    detect_thread.start()

    # ── Output forwarding loop ───────────────────────────────────────────────
    # Main thread forwards child output to the real stdout/stderr.
    # Checks for error detection event to interject the D/S prompt.

    stdout_done = False
    stderr_done = False
    exit_code = 0

    try:
        while not (stdout_done and stderr_done):
            # Forward any pending output
            _drain_output_queue(output_queue, stdout_done, stderr_done)

            # Check if stdout/stderr readers are done
            stdout_done, stderr_done = _check_reader_done(
                output_queue, stdout_done, stderr_done
            )

            # Handle error detection event
            if error_detected_event.is_set():
                # Let output settle before interrupting
                import time

                time.sleep(_SETTLE_SECONDS)

                # Drain remaining output that arrived during settle period
                _drain_output_queue(output_queue, stdout_done, stderr_done)
                stdout_done, stderr_done = _check_reader_done(
                    output_queue, stdout_done, stderr_done
                )

                with buffer_lock:
                    snapshot = list(buffer)

                chose_debug = _show_error_prompt(console)

                if chose_debug:
                    success = _package_debug_context(
                        lines=snapshot,
                        project_dir=project_dir,
                        ecosystems=ecosystems,
                        ecosystem_display=ecosystem_display,
                        console=console,
                    )
                    if success:
                        packaged_count += 1

                error_detected_event.clear()

        # Child has finished — collect exit code
        child.wait()
        exit_code = child.returncode

    except KeyboardInterrupt:
        # User hit Ctrl+C — terminate child cleanly
        console.print()  # newline after ^C
        _terminate_child(child)

        # Drain any remaining output
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        _drain_output_queue(output_queue, done_stdout=True, done_stderr=True)

        exit_code = child.returncode or 130  # 130 = killed by SIGINT

        # Final session prompt
        console.print()
        with buffer_lock:
            snapshot = list(buffer)

        if packaged_count == 0:
            # Nothing packaged yet — offer to capture the full session
            chose_debug = _show_exit_prompt(console, packaged_count)
            if chose_debug and snapshot:
                success = _package_debug_context(
                    lines=snapshot,
                    project_dir=project_dir,
                    ecosystems=ecosystems,
                    ecosystem_display=ecosystem_display,
                    console=console,
                    is_full_session=True,
                )
        else:
            # Already packaged at least one error — just inform and exit
            _show_exit_summary(console, packaged_count)

    return exit_code


# ---------------------------------------------------------------------------
# Output forwarding helpers
# ---------------------------------------------------------------------------


def _drain_output_queue(
    q: queue.Queue,
    done_stdout: bool,
    done_stderr: bool,
    timeout: float = 0.1,
) -> None:
    """Forward pending items from the output queue to sys.stdout/stderr.

    Sentinels (raw=None) are put back into the queue so that
    _check_reader_done can observe them and set the done flags correctly.
    """
    try:
        while True:
            stream_name, raw = q.get_nowait()
            if raw is None:
                # Put the sentinel back — _check_reader_done must see it
                q.put((stream_name, None))
                break
            if stream_name == "stdout":
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
            else:
                sys.stderr.buffer.write(raw)
                sys.stderr.buffer.flush()
    except queue.Empty:
        pass


def _check_reader_done(
    q: queue.Queue,
    done_stdout: bool,
    done_stderr: bool,
) -> tuple[bool, bool]:
    """
    Process the queue, updating done flags when sentinels are received.
    Returns (done_stdout, done_stderr).
    """
    try:
        while True:
            stream_name, raw = q.get_nowait()
            if raw is None:
                if stream_name == "stdout":
                    done_stdout = True
                else:
                    done_stderr = True
            else:
                if stream_name == "stdout":
                    sys.stdout.buffer.write(raw)
                    sys.stdout.buffer.flush()
                else:
                    sys.stderr.buffer.write(raw)
                    sys.stderr.buffer.flush()
    except queue.Empty:
        pass
    return done_stdout, done_stderr


def _terminate_child(child: subprocess.Popen) -> None:
    """Terminate the child process gracefully, then forcefully."""
    import signal
    import time

    if child.poll() is not None:
        return  # already exited

    try:
        if os.name == "nt":
            child.terminate()
        else:
            child.send_signal(signal.SIGTERM)

        # Give it 3 seconds to exit gracefully
        for _ in range(30):
            if child.poll() is not None:
                break
            time.sleep(0.1)

        if child.poll() is None:
            child.kill()
    except (ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------------------
# D/S prompt rendering
# ---------------------------------------------------------------------------


def _show_error_prompt(console: Console) -> bool:
    """
    Render the error-detected panel and read a single D or S keypress.
    Returns True if the user chose D (debug), False if S (skip).
    """
    console.print()
    console.print(
        Panel(
            "  [bold yellow]contextzip detected an error[/]\n\n"
            "  Press [bold green]\\[D][/] to package debug context   "
            "[bold dim]\\[S][/] to skip",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    key = _read_single_key()
    console.print()
    return key in _KEY_DEBUG


def _show_exit_prompt(console: Console, packaged_count: int) -> bool:
    """
    Render the session-end panel after Ctrl+C.
    Returns True if user chose D.
    """
    console.print(
        Panel(
            "  [bold]contextzip · session ended[/]\n\n"
            "  No errors were auto-detected this session.\n\n"
            "  Press [bold green]\\[D][/] to package the full session output   "
            "[bold dim]\\[S][/] to exit",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    key = _read_single_key()
    console.print()
    return key in _KEY_DEBUG


def _show_exit_summary(console: Console, packaged_count: int) -> None:
    """Show a brief summary when exiting after already packaging errors."""
    noun = "package" if packaged_count == 1 else "packages"
    console.print(
        Panel.fit(
            f"  [green]✓[/]  [bold]{packaged_count} debug {noun}[/] saved to "
            f"[cyan].contextzip/[/] this session.",
            border_style="green",
            padding=(0, 2),
        )
    )


# ---------------------------------------------------------------------------
# Keypress reading (cross-platform, non-blocking)
# ---------------------------------------------------------------------------


def _read_single_key() -> str:
    """
    Block until the user presses a single key and return it as a string.

    Uses termios on Unix (raw mode, single char) and msvcrt on Windows.
    Falls back to a regular input() call if neither is available.
    """
    if os.name == "nt":
        return _read_key_windows()
    else:
        return _read_key_unix()


def _read_key_unix() -> str:
    try:
        import termios
        import tty
        import select

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            # Wait up to 60 seconds for a keypress
            ready, _, _ = select.select([sys.stdin], [], [], 60)
            if ready:
                ch = sys.stdin.read(1)
                return ch
            return "s"  # timeout → treat as skip
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        # Fallback if terminal manipulation isn't available
        try:
            return input("  [D/S] › ").strip()[:1] or "s"
        except (EOFError, KeyboardInterrupt):
            return "s"


def _read_key_windows() -> str:
    try:
        import msvcrt
        import time

        deadline = time.monotonic() + 60  # match the Unix 60-second timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                return ch
            time.sleep(0.05)
        return "s"  # timeout → treat as skip
    except Exception:
        try:
            return input("  [D/S] › ").strip()[:1] or "s"
        except (EOFError, KeyboardInterrupt):
            return "s"


# ---------------------------------------------------------------------------
# Debug context packaging
# ---------------------------------------------------------------------------


def _package_debug_context(
    lines: list[str],
    project_dir: Path,
    ecosystems: list[str],
    ecosystem_display: str,
    console: Console,
    is_full_session: bool = False,
) -> bool:
    """
    Process the buffer and write debug-context.zip to .contextzip/.

    Returns True on success, False on failure.
    """
    with console.status("[cyan]Packaging debug context…[/]", spinner="dots"):
        raw_text = "\n".join(lines)

        if is_full_session:
            # Full session capture — no error block extraction, take everything
            from contextzip.error_parser import (
                strip_ansi,
                strip_noise,
                build_prompt_txt,
            )

            clean_text = strip_ansi(raw_text)
            terminal_txt = strip_noise(clean_text, ecosystems)
            prompt_txt = build_prompt_txt(
                error_type="Full session output",
                ecosystem=ecosystem_display,
                error_block=terminal_txt,
                referenced_files=[],
                project_dir=project_dir,
            )
            referenced_paths: list[Path] = []
        else:
            result = process_buffer(
                raw_buffer=raw_text,
                project_dir=project_dir,
                ecosystems=ecosystems,
                ecosystem_display=ecosystem_display,
            )

            if result is None:
                console.print(
                    "[yellow]  ⚠[/]  [dim]No recognisable error block found in buffer. "
                    "Try again or use Ctrl+C → D for full session capture.[/]"
                )
                return False

            prompt_txt, terminal_txt, referenced_paths = result

    # Write the ZIP
    zip_path = _write_debug_zip(
        project_dir=project_dir,
        prompt_txt=prompt_txt,
        terminal_txt=terminal_txt,
        referenced_paths=referenced_paths,
        console=console,
    )

    if zip_path is None:
        return False

    # Success banner
    rel = (
        zip_path.relative_to(project_dir)
        if zip_path.is_relative_to(project_dir)
        else zip_path
    )
    console.print(
        Panel.fit(
            f"[green]✓[/]  debug context saved → [cyan]{rel}[/]"
            + (
                f"\n[dim]   {len(referenced_paths)} source file"
                f"{'s' if len(referenced_paths) != 1 else ''} included[/]"
                if referenced_paths
                else ""
            ),
            border_style="green",
            padding=(0, 2),
        )
    )
    console.print()
    return True


def _write_debug_zip(
    project_dir: Path,
    prompt_txt: str,
    terminal_txt: str,
    referenced_paths: list[Path],
    console: Console,
) -> Path | None:
    """
    Write debug-context.zip to the .contextzip/output/ workspace.

    Structure (flat):
      prompt.txt
      terminal-error.txt
      source-files.zip    (inner zip with referenced source files)
    """
    from contextzip.packager import _workspace_dir, _ensure_workspace_gitignore

    workspace, is_git_repo = _workspace_dir(project_dir)
    output_dir = workspace / "output"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"[red]  ✗[/]  Could not create workspace: {exc}")
        return None

    if is_git_repo:
        try:
            _ensure_workspace_gitignore(workspace)
        except OSError:
            pass

    zip_path = output_dir / "debug-context.zip"

    try:
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            # 1. prompt.txt — first entry so AI tools see it immediately
            zf.writestr("prompt.txt", prompt_txt.encode("utf-8"))

            # 2. terminal-error.txt
            zf.writestr("terminal-error.txt", terminal_txt.encode("utf-8"))

            # 3. source-files.zip — inner zip of referenced source files
            if referenced_paths:
                inner_buf = io.BytesIO()
                with zipfile.ZipFile(
                    inner_buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
                ) as inner_zf:
                    for abs_path in referenced_paths:
                        try:
                            rel = abs_path.relative_to(project_dir).as_posix()
                            inner_zf.write(abs_path, arcname=rel)
                        except (ValueError, OSError):
                            pass  # skip files we can't read or locate
                zf.writestr("source-files.zip", inner_buf.getvalue())

    except OSError as exc:
        console.print(f"[red]  ✗[/]  Failed to write debug-context.zip: {exc}")
        return None

    return zip_path
