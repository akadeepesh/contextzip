// Watches the Downloads folder for new ZIPs and reports each one once it's
// finished downloading. Deciding whether it's a reply for this workspace is
// the caller's job (see extension.ts). No vscode imports: unit-testable.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/** The user's Downloads folder, including a relocated one on Linux (XDG). */
export function downloadsFolder(home: string = os.homedir(), platform: NodeJS.Platform = process.platform): string {
  if (platform === "linux") {
    try {
      const dirs = fs.readFileSync(path.join(process.env.XDG_CONFIG_HOME || path.join(home, ".config"), "user-dirs.dirs"), "utf8");
      const match = /^XDG_DOWNLOAD_DIR="?([^"\n]+)"?/m.exec(dirs);
      if (match) return match[1].replace(/^\$HOME/, home);
    } catch {
      // No user-dirs file: use the default.
    }
  }
  return path.join(home, "Downloads");
}

export function isCandidateName(name: string): boolean {
  // Browsers write to .crdownload / .part / .download files and rename at the end.
  return name.toLowerCase().endsWith(".zip");
}

export interface WatcherOptions {
  /** How long a file's size must stay the same before it counts as downloaded. */
  settleMs?: number;
  /** Files older than this when first seen are ignored (moved in, not downloaded). */
  maxAgeMs?: number;
}

export class DownloadsWatcher {
  private watcher: fs.FSWatcher | undefined;
  private readonly pending = new Map<string, NodeJS.Timeout>();
  private readonly seen = new Set<string>();
  private readonly settleMs: number;
  private readonly maxAgeMs: number;

  constructor(
    private readonly folder: string,
    private readonly onZip: (zipPath: string) => void,
    options: WatcherOptions = {}
  ) {
    this.settleMs = options.settleMs ?? 1500;
    this.maxAgeMs = options.maxAgeMs ?? 2 * 60_000;
  }

  /** Returns false when the folder can't be watched (missing, no permission). */
  start(): boolean {
    try {
      this.watcher = fs.watch(this.folder, (_event, filename) => {
        if (filename && isCandidateName(String(filename))) this.schedule(path.join(this.folder, String(filename)));
      });
      this.watcher.on("error", () => this.dispose());
      return true;
    } catch {
      return false;
    }
  }

  private schedule(file: string): void {
    clearTimeout(this.pending.get(file));
    this.pending.set(
      file,
      setTimeout(() => this.check(file), this.settleMs)
    );
  }

  private check(file: string, lastSize = -1): void {
    this.pending.delete(file);
    let stat: fs.Stats;
    try {
      stat = fs.statSync(file);
    } catch {
      return; // renamed or removed again
    }
    if (!stat.isFile() || Date.now() - stat.mtimeMs > this.maxAgeMs) return;
    if (stat.size !== lastSize) {
      // Still growing (or first look): check again once it settles.
      this.pending.set(
        file,
        setTimeout(() => this.check(file, stat.size), this.settleMs)
      );
      return;
    }
    const key = `${file}:${stat.size}:${stat.mtimeMs}`;
    if (this.seen.has(key)) return;
    this.seen.add(key);
    this.onZip(file);
  }

  dispose(): void {
    this.watcher?.close();
    this.watcher = undefined;
    for (const timer of this.pending.values()) clearTimeout(timer);
    this.pending.clear();
  }
}
