// The shapes of the CLI's --json results this extension reads (documented
// in CLI.md under "JSON output"), and how they're summarised for people.

export interface ZipInfo {
  path: string;
  files: number;
  bytes: number;
  compressed_bytes: number;
  estimated_tokens: number;
  snapshot: string | null;
}

export interface ClipboardInfo {
  copied: boolean;
  message: string;
  hint: string | null;
}

export type FileStatus =
  | "new"
  | "modified"
  | "merged"
  | "unchanged"
  | "conflict"
  | "drifted"
  | "deleted"
  | "untracked"
  | "partial"
  | "ambiguous"
  | "moved"
  | "delete-requested";

export interface PlanFile {
  path: string | null;
  source: string;
  status: FileStatus;
  safe: boolean;
  reason: string;
  note: string | null;
  moved_from: string | null;
}

export interface Plan {
  zip: string;
  snapshot: string | null;
  needs_decision: boolean;
  counts: Partial<Record<FileStatus, number>>;
  files: PlanFile[];
  remap: string | null;
  structure_warning: string | null;
}

export interface ApplyResult {
  written: string[];
  skipped: string[];
  removed: string[];
  zip_kept: boolean;
  archived_to: string | null;
}

export interface SnapshotInfo {
  id: number;
  label: string;
  status: string;
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let n = bytes / 1024;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit++;
  }
  return `${n >= 10 ? Math.round(n) : n.toFixed(1)} ${units[unit]}`;
}

/** Same rounding as the CLI's tokens.format_tokens. */
export function formatTokens(tokens: number): string {
  let n: string;
  if (tokens < 1000) n = String(tokens);
  else if (tokens < 10_000) n = `${(tokens / 1000).toFixed(1)}k`;
  else if (tokens < 999_500) n = `${Math.round(tokens / 1000)}k`;
  else n = `${(tokens / 1_000_000).toFixed(1)}M`;
  return `≈ ${n} tokens`;
}

export function packageSummary(zip: ZipInfo): string {
  const files = `${zip.files} file${zip.files === 1 ? "" : "s"}`;
  return `Packed ${files} · ${humanSize(zip.compressed_bytes)} · ${formatTokens(zip.estimated_tokens)}`;
}

const SAFE_ORDER: FileStatus[] = ["modified", "merged", "moved", "new"];
const RISKY_ORDER: FileStatus[] = ["conflict", "partial", "drifted", "deleted", "untracked", "ambiguous", "delete-requested"];

/** "2 modified, 1 new" and "1 conflict" (empty strings when there are none). */
export function planCounts(plan: Plan): { safe: string; risky: string } {
  const part = (s: FileStatus) => (plan.counts[s] ? `${plan.counts[s]} ${s}` : "");
  return {
    safe: SAFE_ORDER.map(part).filter(Boolean).join(", "),
    risky: RISKY_ORDER.map(part).filter(Boolean).join(", "),
  };
}

export function safeFiles(plan: Plan): PlanFile[] {
  return plan.files.filter((f) => f.safe);
}

export function riskyFiles(plan: Plan): PlanFile[] {
  return plan.files.filter((f) => !f.safe && f.status !== "unchanged");
}

/** The "14-k3f9a2" label in a CONTEXTZIP.md, if it has one. */
export function snapshotFromGuide(text: string): string | undefined {
  const match = /^\s*snapshot:\s*`?(\d+-[a-z0-9]+)`?\s*$/im.exec(text);
  return match ? match[1].toLowerCase() : undefined;
}
