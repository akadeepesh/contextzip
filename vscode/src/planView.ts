// The "AI Changes" view in the Explorer: what an AI reply would do to each
// file, grouped by whether it needs a decision. Clicking a file opens it.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import type { FileStatus, Plan, PlanFile } from "./format";
import { riskyFiles, safeFiles } from "./format";

const ICONS: Record<FileStatus, [string, string?]> = {
  new: ["diff-added", "gitDecoration.addedResourceForeground"],
  modified: ["diff-modified", "gitDecoration.modifiedResourceForeground"],
  merged: ["git-merge", "gitDecoration.modifiedResourceForeground"],
  moved: ["diff-renamed", "gitDecoration.renamedResourceForeground"],
  unchanged: ["circle-small"],
  conflict: ["warning", "gitDecoration.conflictingResourceForeground"],
  partial: ["warning", "list.warningForeground"],
  drifted: ["warning", "list.warningForeground"],
  deleted: ["warning", "list.warningForeground"],
  untracked: ["question", "list.warningForeground"],
  ambiguous: ["question", "list.warningForeground"],
  "delete-requested": ["trash", "list.warningForeground"],
};

type Node = { kind: "group"; label: string; files: PlanFile[]; expanded: boolean } | { kind: "file"; file: PlanFile };

export class PlanView implements vscode.TreeDataProvider<Node> {
  private plan: Plan | undefined;
  private root: string | undefined;
  private written = new Set<string>();
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  show(plan: Plan, projectRoot: string): void {
    this.plan = plan;
    this.root = projectRoot;
    this.written.clear();
    void vscode.commands.executeCommand("setContext", "contextzip.hasPlan", true);
    this.changed.fire(undefined);
  }

  markWritten(paths: string[]): void {
    for (const p of paths) this.written.add(p);
    this.changed.fire(undefined);
  }

  clear(): void {
    this.plan = undefined;
    void vscode.commands.executeCommand("setContext", "contextzip.hasPlan", false);
    this.changed.fire(undefined);
  }

  getChildren(node?: Node): Node[] {
    if (!this.plan) return [];
    if (node?.kind === "group") return node.files.map((file) => ({ kind: "file", file }));
    if (node) return [];
    const risky = riskyFiles(this.plan);
    const safe = safeFiles(this.plan);
    const unchanged = this.plan.files.filter((f) => f.status === "unchanged");
    const groups: Node[] = [];
    if (risky.length) groups.push({ kind: "group", label: "Needs a decision", files: risky, expanded: true });
    if (safe.length) groups.push({ kind: "group", label: "Safe changes", files: safe, expanded: true });
    if (unchanged.length) groups.push({ kind: "group", label: "Already up to date", files: unchanged, expanded: false });
    return groups;
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "group") {
      const item = new vscode.TreeItem(
        node.label,
        node.expanded ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed
      );
      item.description = String(node.files.length);
      return item;
    }
    const f = node.file;
    const display = f.path ?? f.source;
    const item = new vscode.TreeItem(path.posix.basename(display));
    const dir = path.posix.dirname(display);
    const written = f.path !== null && this.written.has(f.path);
    item.description = `${written ? "applied" : f.status}${dir !== "." ? ` · ${dir}` : ""}`;
    item.tooltip = [display, f.reason, f.note, f.moved_from ? `moved from ${f.moved_from}` : "", f.source !== display ? `in the ZIP as ${f.source}` : ""]
      .filter(Boolean)
      .join("\n");
    const [icon, colour] = written ? ["check", "testing.iconPassed"] : ICONS[f.status];
    item.iconPath = new vscode.ThemeIcon(icon, colour ? new vscode.ThemeColor(colour) : undefined);
    if (f.path && this.root) {
      const uri = vscode.Uri.file(path.join(this.root, f.path));
      item.resourceUri = uri;
      if (fs.existsSync(uri.fsPath)) item.command = { command: "vscode.open", title: "Open", arguments: [uri] };
    }
    return item;
  }
}
