"""
mcp_server.py — contextzip as an MCP server, for AI agents and editors.

    pip install "contextzip[mcp]"          # Python 3.10+
    contextzip mcp                         # stdio; register it in your MCP client

For example, in a client's MCP settings:

    {"mcpServers": {"contextzip": {"command": "contextzip", "args": ["mcp"]}}}

Every tool is a thin wrapper over `contextzip.web.run`, so an agent gets
exactly what the CLI does — the same exclusions, redaction, snapshots and
safe apply — and the same JSON (see CLI.md, "JSON output"). Tools that only
read are marked read-only; the ones that write are marked destructive, and
default to the cautious option (safe changes only, pull requests as a dry
run) so an agent has to ask for more.
"""

from __future__ import annotations

from typing import Any

from contextzip.web import run

INSTRUCTIONS = (
    "contextzip packages a project's relevant files into a ZIP for an AI chat (leaving out dependencies, "
    "build output and secrets) and applies the ZIP the AI sends back, merging around local edits. "
    "Use package_project before handing a codebase to another model; use preview_apply before apply_changes."
)


def _clean(result: dict) -> dict:
    """Drop the step-by-step log; keep the result, warnings and any error."""
    return {k: v for k, v in result.items() if k != "log"}


def _filters(include: list[str] | None, exclude: list[str] | None) -> list[str]:
    args: list[str] = []
    for path in include or []:
        args += ["-i", path]
    for pattern in exclude or []:
        args += ["-e", pattern]
    return args


def package_project(
    path: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    git_changes: bool = False,
    task: str | None = None,
    dry_run: bool = False,
    copy_to_clipboard: bool = False,
) -> dict[str, Any]:
    """
    Package a local project into a lean ZIP for an AI chat.

    path: the project folder (any folder inside it works; the project root is found like git does).
    include: only these files or folders (relative to path); naming an excluded file such as .env brings it back, redacted.
    exclude: extra gitignore-style patterns to leave out.
    git_changes: only files git reports as staged, modified or untracked.
    task: describe a task to let Gemini pick only the relevant files (needs GEMINI_API_KEY).
    dry_run: report what would be included without writing the ZIP.
    copy_to_clipboard: also put the ZIP on the clipboard as a file.
    Returns the ZIP's path, file counts, sizes and an estimated token count.
    """
    args = _filters(include, exclude)
    if git_changes:
        args.append("--git-changes")
    if task:
        args += ["--prompt", task]
    if dry_run:
        args.append("--dry-run")
    if not copy_to_clipboard:
        args.append("--no-clipboard")
    return _clean(run(args, cwd=path))


def package_repository(
    repo: str,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Package a GitHub, GitLab or Bitbucket repository without cloning it.

    repo: github.com/user/repo, gitlab.com/group/project, bitbucket.org/team/repo, a link to a branch's folder, or gh:user/repo.
    ref: a branch, tag or commit (default: the repository's default branch).
    include / exclude: as for package_project, relative to the repository root.
    Private repositories use GITHUB_TOKEN, GITLAB_TOKEN or BITBUCKET_TOKEN.
    """
    args = ["--from", repo, "--no-clipboard", *_filters(include, exclude)]
    if ref:
        args += ["--ref", ref]
    if dry_run:
        args.append("--dry-run")
    return _clean(run(args))


def preview_apply(zip_path: str, path: str) -> dict[str, Any]:
    """
    Show what applying an AI-returned ZIP would do to a project, writing nothing.

    zip_path: the ZIP the AI sent back.
    path: the project folder it answers.
    Returns every file with its status (new, modified, merged, conflict, partial, ...) and
    whether anything needs a decision.
    """
    return _clean(run(["apply-zip", zip_path, "--dry-run"], cwd=path))


def apply_changes(zip_path: str, path: str, mode: str = "safe") -> dict[str, Any]:
    """
    Write an AI-returned ZIP into a project.

    mode "safe" (default) applies new, modified and cleanly merged files and leaves anything that
    needs a decision (conflicts, cut-down files, files changed since) untouched.
    mode "all" applies everything; where the local file and the AI disagree, the AI's version wins.
    Files are never deleted by this tool. Call preview_apply first.
    """
    if mode not in ("safe", "all"):
        return {"ok": False, "error": 'mode must be "safe" or "all"'}
    return _clean(run(["apply-zip", zip_path, "--safe-only" if mode == "safe" else "--yes"], cwd=path))


def open_pull_request(zip_path: str, repo: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """
    Open a GitHub pull request with an AI's reply to a repository packaged with package_repository.

    zip_path: the ZIP the AI sent back.
    repo: the repository, if the reply doesn't name its snapshot.
    dry_run: true (default) only reports what the pull request would contain; set false to open it,
    which publishes the changes on GitHub (forking first if needed). Needs GITHUB_TOKEN or a gh login.
    """
    args = ["apply-zip", zip_path, "--pr"]
    if repo:
        args += ["--from", repo]
    args.append("--dry-run" if dry_run else "--yes")
    return _clean(run(args))


def list_snapshots(path: str) -> dict[str, Any]:
    """List a project's snapshots: every ZIP packaged from it, which replies are merged against."""
    return _clean(run(["snapshots"], cwd=path))


def build_server():
    """The MCP server with every tool registered."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    from contextzip import __version__

    server = MCPServer(name="contextzip", version=__version__, instructions=INSTRUCTIONS, website_url="https://www.contextzipcli.com")
    read_only = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    tools = [
        (package_project, "Package a project for AI", ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)),
        (package_repository, "Package a remote repository", ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)),
        (preview_apply, "Preview applying an AI reply", ToolAnnotations(read_only_hint=True, open_world_hint=False)),
        (apply_changes, "Apply an AI reply", ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)),
        (open_pull_request, "Open a pull request from an AI reply", ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)),
        (list_snapshots, "List a project's snapshots", read_only),
    ]
    for fn, title, annotations in tools:
        server.tool(title=title, annotations=annotations)(fn)
    return server


def serve() -> None:
    build_server().run("stdio")
