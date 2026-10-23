// The contextzip extension: package a project (or part of it) for an AI
// chat, and apply the ZIP the AI sends back. All the real work is done by
// the contextzip CLI through --json (see cli.ts); this file is the UI.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { Binary, CliNotFoundError, CliResult, MIN_CLI_VERSION, cliVersion, isAtLeast, resolveBinary, runJson } from "./cli";
import { DownloadsWatcher, downloadsFolder } from "./downloads";
import {
  ApplyResult,
  ClipboardInfo,
  Plan,
  SnapshotInfo,
  ZipInfo,
  packageSummary,
  planCounts,
  safeFiles,
  snapshotFromGuide,
} from "./format";
import { PlanView } from "./planView";
import { readGuide } from "./zip";

let output: vscode.OutputChannel;
let planView: PlanView;
let extensionPath: string;
let watcher: DownloadsWatcher | undefined;
const checkedVersions = new Map<string, boolean>();

/** What other extensions (and the integration tests) can call. */
export interface ContextzipApi {
  run(args: string[], cwd: string): Promise<CliResult>;
}

export function activate(context: vscode.ExtensionContext): ContextzipApi {
  extensionPath = context.extensionPath;
  output = vscode.window.createOutputChannel("contextzip");
  planView = new PlanView();

  const register = (id: string, fn: (...args: any[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  register("contextzip.package", () => packageProject([], "Packaging the project"));
  register("contextzip.packageSelection", (uri?: vscode.Uri, uris?: vscode.Uri[]) => packageSelection(uri, uris));
  register("contextzip.packageChanges", () => packageProject(["--git-changes"], "Packaging your changes"));
  register("contextzip.packageForTask", packageForTask);
  register("contextzip.packageGitHub", packageGitHub);
  register("contextzip.applyZip", (uri?: vscode.Uri) => applyZipCommand(uri));
  register("contextzip.clearPlan", () => planView.clear());
  register("contextzip.showLog", () => output.show());

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.text = "$(package) cz";
  status.tooltip = "contextzip: package this project for an AI chat";
  status.command = "contextzip.package";
  status.show();

  context.subscriptions.push(
    output,
    status,
    vscode.window.registerTreeDataProvider("contextzip.plan", planView),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("contextzip.watchDownloads") || e.affectsConfiguration("contextzip.downloadsFolder")) {
        startWatcher();
      }
      if (e.affectsConfiguration("contextzip.path")) checkedVersions.clear();
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => startWatcher()),
    { dispose: () => watcher?.dispose() }
  );
  startWatcher();

  return { run: (args, cwd) => runJson(binary(), args, { cwd }) };
}

export function deactivate(): void {
  watcher?.dispose();
}

// ---------------------------------------------------------------------------
// Running the CLI
// ---------------------------------------------------------------------------

const config = () => vscode.workspace.getConfiguration("contextzip");

function binary(): Binary {
  return resolveBinary(config().get<string>("path", ""), extensionPath);
}

async function notInstalled(error: CliNotFoundError): Promise<void> {
  output.appendLine(error.message);
  const choice = await vscode.window.showErrorMessage(
    "contextzip isn't installed where VS Code can find it. Install it, or point the contextzip.path setting at it.",
    "Install with pip",
    "Install with npm",
    "Open Settings"
  );
  if (choice === "Install with pip" || choice === "Install with npm") {
    const terminal = vscode.window.createTerminal("contextzip install");
    terminal.show();
    terminal.sendText(choice === "Install with pip" ? "pip install contextzip" : "npm install -g @contextzip/cli");
  } else if (choice === "Open Settings") {
    void vscode.commands.executeCommand("workbench.action.openSettings", "contextzip.path");
  }
}

async function versionOk(bin: Binary, cwd: string): Promise<boolean> {
  const known = checkedVersions.get(bin.command);
  if (known !== undefined) return known;
  const version = await cliVersion(bin, cwd);
  // An unreadable version isn't a reason to refuse; the run will say if anything's wrong.
  const ok = version === undefined || isAtLeast(version, MIN_CLI_VERSION);
  checkedVersions.set(bin.command, ok);
  if (!ok) {
    const choice = await vscode.window.showErrorMessage(
      `This extension needs contextzip ${MIN_CLI_VERSION} or newer, and ${bin.command} is ${version}. Update it, then try again.`,
      "Update in Terminal"
    );
    checkedVersions.delete(bin.command);
    if (choice) openCliTerminal(bin, ["update"], os.homedir());
  }
  return ok;
}

/** Run `contextzip <args> --json` with a cancellable progress notification. */
async function run(args: string[], cwd: string, title: string): Promise<CliResult | undefined> {
  let bin: Binary;
  try {
    bin = binary();
  } catch (error) {
    if (error instanceof CliNotFoundError) await notInstalled(error);
    else throw error;
    return undefined;
  }
  if (!(await versionOk(bin, cwd))) return undefined;

  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `contextzip: ${title}…`, cancellable: true },
    async (_progress, token) => {
      const controller = new AbortController();
      token.onCancellationRequested(() => controller.abort());
      output.appendLine(`\n$ ${path.basename(bin.command)} ${[...args, "--json"].map(quote).join(" ")}    (in ${cwd})`);
      try {
        const result = await runJson(bin, args, { cwd, signal: controller.signal });
        for (const entry of result.log) {
          const mark = { ok: "✓", warn: "!", error: "✗", info: " " }[entry.level] ?? " ";
          output.appendLine(`${mark} ${entry.text}${entry.detail ? `  · ${entry.detail}` : ""}`);
        }
        if (!result.ok && !result.log.length) output.appendLine(`✗ ${result.error}`);
        return result;
      } catch (error) {
        if ((error as Error).name === "AbortError") {
          output.appendLine("cancelled");
          return undefined;
        }
        if (error instanceof CliNotFoundError) {
          await notInstalled(error);
          return undefined;
        }
        throw error;
      }
    }
  );
}

function quote(arg: string): string {
  return /^[\w@%+=:,./-]+$/.test(arg) ? arg : JSON.stringify(arg);
}

/**
 * Run the CLI interactively in a terminal (the terminal's process is the
 * binary itself, so nothing needs shell quoting). Used where a person has
 * to decide things as they go. Its merge editor is this very editor.
 */
function openCliTerminal(bin: Binary, args: string[], cwd: string): void {
  const env: Record<string, string> = {};
  const editorBin = path.join(vscode.env.appRoot, "bin");
  try {
    const cli = fs
      .readdirSync(editorBin)
      .find((f) => (process.platform === "win32" ? f.endsWith(".cmd") : !f.includes(".")));
    if (cli) {
      env.PATH = `${editorBin}${path.delimiter}${process.env.PATH ?? ""}`;
      env.CONTEXTZIP_EDITOR = cli.replace(/\.cmd$/, "");
    }
  } catch {
    // No CLI next to this editor: contextzip looks for code, cursor, … on PATH.
  }
  const terminal = vscode.window.createTerminal({ name: "contextzip", shellPath: bin.command, shellArgs: args, cwd, env });
  terminal.show();
}

async function showFailure(result: CliResult, cwd: string, retryArgs?: string[]): Promise<void> {
  const error = result.error ?? "contextzip failed";
  if (/gemini api key/i.test(error) && retryArgs) {
    const choice = await vscode.window.showErrorMessage(
      "Picking files for a task needs a free Gemini API key. contextzip can walk you through it in a terminal.",
      "Set Up in Terminal"
    );
    if (choice) openCliTerminal(binary(), retryArgs, cwd);
    return;
  }
  const choice = await vscode.window.showErrorMessage(`contextzip: ${error}`, "Show Log");
  if (choice) output.show();
}

// ---------------------------------------------------------------------------
// Packaging
// ---------------------------------------------------------------------------

async function pickFolder(required = true): Promise<string | undefined> {
  const folders = (vscode.workspace.workspaceFolders ?? []).filter((f) => f.uri.scheme === "file");
  const active = vscode.window.activeTextEditor?.document.uri;
  const activeFolder = active ? vscode.workspace.getWorkspaceFolder(active) : undefined;
  if (activeFolder?.uri.scheme === "file") return activeFolder.uri.fsPath;
  if (folders.length === 1) return folders[0].uri.fsPath;
  if (folders.length === 0) {
    if (required) void vscode.window.showWarningMessage("Open a folder first: contextzip packages the project it's in.");
    return undefined;
  }
  const picked = await vscode.window.showWorkspaceFolderPick({ placeHolder: "Which project?" });
  return picked?.uri.fsPath;
}

async function packageProject(args: string[], title: string, cwd?: string): Promise<void> {
  const folder = cwd ?? (await pickFolder());
  if (!folder) return;
  const fullArgs = config().get<boolean>("copyToClipboard", true) ? args : [...args, "--no-clipboard"];
  const result = await run(fullArgs, folder, title);
  if (!result) return;
  if (!result.ok) return showFailure(result, folder, args);

  const zip = result.zip as ZipInfo | null;
  if (!zip) {
    const last = [...result.log].reverse().find((e) => e.level === "info" || e.level === "ok");
    void vscode.window.showInformationMessage(`contextzip: ${last?.text ?? "nothing to package"}`);
    return;
  }
  const clipboard = result.clipboard as ClipboardInfo | null;
  let message = packageSummary(zip);
  if (clipboard?.copied) message += ". It's on your clipboard: paste it into Claude, ChatGPT or any upload box.";
  else if (clipboard) message += `. Couldn't copy it to the clipboard${clipboard.hint ? `: ${clipboard.hint}` : "."}`;
  const actions = ["Reveal ZIP", "Open Report"];
  if (result.warnings.length) actions.push("Show Warnings");
  const choice = await vscode.window.showInformationMessage(message, ...actions);
  if (choice === "Reveal ZIP") void vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(zip.path));
  else if (choice === "Open Report" && typeof result.report === "string") {
    void vscode.window.showTextDocument(vscode.Uri.file(result.report));
  } else if (choice === "Show Warnings") output.show();
}

async function packageSelection(uri?: vscode.Uri, uris?: vscode.Uri[]): Promise<void> {
  const picked = (uris?.length ? uris : uri ? [uri] : []).filter((u) => u.scheme === "file");
  if (!picked.length) return packageProject([], "Packaging the project");
  const folder = vscode.workspace.getWorkspaceFolder(picked[0]);
  if (!folder) {
    void vscode.window.showWarningMessage("contextzip can only package files inside an open folder.");
    return;
  }
  const paths = picked
    .filter((u) => vscode.workspace.getWorkspaceFolder(u)?.uri.toString() === folder.uri.toString())
    .map((u) => path.relative(folder.uri.fsPath, u.fsPath).split(path.sep).join("/") || ".");
  if (paths.includes(".")) return packageProject([], "Packaging the project", folder.uri.fsPath);
  const what = paths.length === 1 ? paths[0] : `${paths.length} items`;
  return packageProject(["include", ...paths], `Packaging ${what}`, folder.uri.fsPath);
}

async function packageForTask(): Promise<void> {
  const folder = await pickFolder();
  if (!folder) return;
  const task = await vscode.window.showInputBox({
    title: "Package files for a task",
    prompt: "Describe the task. Gemini picks the files that matter from their paths and sizes; contents never leave your machine.",
    placeHolder: "fix the login redirect bug",
    ignoreFocusOut: true,
  });
  if (!task?.trim()) return;
  return packageProject(["--prompt", task.trim()], "Picking the files for your task", folder);
}

async function packageGitHub(): Promise<void> {
  const repo = await vscode.window.showInputBox({
    title: "Package a GitHub, GitLab or Bitbucket repository",
    prompt: "No clone needed. Private repos use GITHUB_TOKEN (or your gh login), GITLAB_TOKEN or BITBUCKET_TOKEN.",
    placeHolder: "github.com/user/repo, gitlab.com/group/project, bitbucket.org/team/repo, or a link to a folder",
    ignoreFocusOut: true,
    validateInput: (v) =>
      !v.trim() || /^(gh:|gl:|bb:|git@(github\.com|gitlab\.com|bitbucket\.org):|(https?:\/\/)?(www\.)?(github\.com|gitlab\.com|bitbucket\.org)\/)/i.test(v.trim())
        ? undefined
        : "Use a GitHub, GitLab or Bitbucket URL, or gh:/gl:/bb:user/repo",
  });
  if (!repo?.trim()) return;
  const cwd = (await pickFolder(false)) ?? os.homedir();
  return packageProject(["--from", repo.trim()], `Fetching ${repo.trim()}`, cwd);
}

// ---------------------------------------------------------------------------
// Applying an AI reply
// ---------------------------------------------------------------------------

async function applyZipCommand(uri?: vscode.Uri): Promise<void> {
  let zipPath = uri?.scheme === "file" ? uri.fsPath : undefined;
  if (!zipPath) {
    const picked = await vscode.window.showOpenDialog({
      title: "Apply the AI's changes",
      canSelectMany: false,
      filters: { "ZIP files": ["zip"] },
      defaultUri: vscode.Uri.file(watchFolder()),
      openLabel: "Apply",
    });
    zipPath = picked?.[0]?.fsPath;
  }
  if (!zipPath) return;
  const folder = (uri && vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath) || (await pickFolder());
  if (!folder) return;
  return applyFlow(zipPath, folder);
}

type Action = "safe" | "review" | "editor" | "terminal" | "all";

async function applyFlow(zipPath: string, cwd: string): Promise<void> {
  const dry = await run(["apply-zip", zipPath, "--dry-run"], cwd, "Comparing the reply with your project");
  if (!dry) return;
  if (!dry.ok) return showFailure(dry, cwd);
  const plan = dry.plan as Plan;
  const root = (dry.project as { root: string } | undefined)?.root ?? cwd;
  planView.show(plan, root);
  void vscode.commands.executeCommand("contextzip.plan.focus");

  if (!plan.files.some((f) => f.status !== "unchanged")) {
    void vscode.window.showInformationMessage("contextzip: nothing to apply. Every file in the reply already matches your project.");
    return;
  }

  const { safe, risky } = planCounts(plan);
  const safeCount = safeFiles(plan).length;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const items: (vscode.QuickPickItem & { action: Action })[] = [];
  if (!plan.needs_decision) {
    items.push(
      { label: `$(check) Apply ${plural(safeCount, "change")}`, description: safe, action: "safe" },
      { label: "$(browser) Review side by side first", description: "in your browser", action: "review" }
    );
  } else {
    if (safeCount) {
      items.push({
        label: `$(check) Apply the ${plural(safeCount, "safe change")}`,
        description: safe,
        detail: `Leaves ${risky} for later`,
        action: "safe",
      });
    }
    items.push(
      {
        label: "$(git-merge) Resolve in the merge editor",
        detail: "Opens each file that needs a decision in the three-way merge editor, from a terminal",
        action: "editor",
      },
      { label: "$(browser) Review side by side in your browser", detail: "Decide file by file, with diffs", action: "review" },
      { label: "$(terminal) Decide in the terminal", action: "terminal" },
      { label: "$(warning) Apply everything", detail: "Where you and the AI changed the same lines, the AI's version wins", action: "all" }
    );
  }
  const choice = await vscode.window.showQuickPick(items, {
    title: `Apply ${path.basename(zipPath)}`,
    placeHolder: plan.needs_decision ? `${risky}: needs your call` : `${safe}`,
    ignoreFocusOut: true,
  });
  if (!choice) return;

  if (choice.action === "editor" || choice.action === "terminal") {
    openCliTerminal(binary(), ["apply-zip", zipPath, ...(choice.action === "editor" ? ["--editor"] : [])], cwd);
    return;
  }
  if (choice.action === "all") {
    const sure = await vscode.window.showWarningMessage(
      `Apply everything from ${path.basename(zipPath)}? Where you and the AI disagree, your version is replaced.`,
      { modal: true },
      "Apply Everything"
    );
    if (!sure) return;
  }
  const flag = { safe: "--safe-only", review: "--review", all: "--yes" }[choice.action];
  const title = choice.action === "review" ? "Waiting for you to finish the review in your browser" : "Applying the changes";
  const result = await run(["apply-zip", zipPath, flag], cwd, title);
  if (!result) return;
  if (!result.ok) return showFailure(result, cwd);
  const applied = result.result as ApplyResult | null;
  if (!applied) return;
  planView.markWritten(applied.written);
  let message = `contextzip: applied ${plural(applied.written.length, "file")}`;
  if (applied.removed.length) message += `, removed ${applied.removed.length}`;
  if (applied.skipped.length) message += `; ${applied.skipped.length} left for later`;
  const actions = ["Open Source Control"];
  if (applied.zip_kept) actions.push("Decide on the Rest");
  const next = await vscode.window.showInformationMessage(`${message}.`, ...actions);
  if (next === "Open Source Control") void vscode.commands.executeCommand("workbench.view.scm");
  else if (next === "Decide on the Rest") void applyFlow(zipPath, cwd);
}

// ---------------------------------------------------------------------------
// Downloads: offer to apply a reply as soon as it arrives
// ---------------------------------------------------------------------------

function watchFolder(): string {
  return config().get<string>("downloadsFolder", "").trim() || downloadsFolder();
}

function startWatcher(): void {
  watcher?.dispose();
  watcher = undefined;
  if (!config().get<boolean>("watchDownloads", true) || !vscode.workspace.workspaceFolders?.length) return;
  watcher = new DownloadsWatcher(watchFolder(), (zip) => void onDownloadedZip(zip));
  if (!watcher.start()) watcher = undefined;
}

async function onDownloadedZip(zipPath: string): Promise<void> {
  const snapshot = snapshotFromGuide(readGuide(zipPath) ?? "");
  if (!snapshot) return; // not a reply to a contextzip package: stay quiet

  let bin: Binary;
  try {
    bin = binary();
  } catch {
    return;
  }
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    if (folder.uri.scheme !== "file") continue;
    let result: CliResult;
    try {
      result = await runJson(bin, ["snapshots"], { cwd: folder.uri.fsPath });
    } catch {
      return;
    }
    const snapshots = (result.snapshots as SnapshotInfo[] | undefined) ?? [];
    const match = snapshots.find((s) => s.label === snapshot);
    if (!match) continue;
    const choice = await vscode.window.showInformationMessage(
      `${path.basename(zipPath)} is an AI reply to snapshot #${match.id} of ${folder.name}. Apply it?`,
      "Apply…",
      "Not Now"
    );
    if (choice === "Apply…") await applyFlow(zipPath, folder.uri.fsPath);
    return;
  }
}
