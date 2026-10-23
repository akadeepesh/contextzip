// Finding and running the contextzip CLI. Everything goes through
// `--json`, so this file is the whole contract between the extension and
// the CLI. No vscode imports here, so it's unit-testable in plain Node.

import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";

/** The oldest CLI with --json (and everything else this extension calls). */
export const MIN_CLI_VERSION = "0.5.2";

export type BinarySource = "setting" | "bundled" | "path";

export interface Binary {
  command: string;
  source: BinarySource;
}

export interface LogEntry {
  level: "ok" | "warn" | "error" | "info";
  text: string;
  detail?: string;
}

/** One `--json` result: ok/error/warnings/log, plus the command's own fields. */
export interface CliResult {
  ok: boolean;
  command: string;
  version: string;
  error?: string;
  warnings: string[];
  log: LogEntry[];
  [field: string]: unknown;
}

export class CliNotFoundError extends Error {
  constructor(public readonly tried: string) {
    super(`contextzip isn't installed, or isn't where VS Code can find it (${tried}).`);
  }
}

const exeName = (platform: NodeJS.Platform) => (platform === "win32" ? "contextzip.exe" : "contextzip");

/** The binary shipped inside a platform-specific build of this extension, if any. */
export function bundledBinary(extensionPath: string, platform: NodeJS.Platform = process.platform): string | undefined {
  const exe = path.join(extensionPath, "bin", "contextzip", exeName(platform));
  if (!fs.existsSync(exe)) return undefined;
  if (platform !== "win32") {
    // A VSIX doesn't always keep the executable bit.
    try {
      fs.accessSync(exe, fs.constants.X_OK);
    } catch {
      try {
        fs.chmodSync(exe, 0o755);
      } catch {
        // Reported when it fails to start.
      }
    }
  }
  return exe;
}

/**
 * `contextzip` or `cz` on PATH. On Windows only real .exe files count —
 * .cmd launchers can't be run without a shell — but an npm global install's
 * cz.cmd is followed to the binary it launches.
 */
export function findOnPath(
  envPath: string = process.env.PATH ?? "",
  platform: NodeJS.Platform = process.platform,
  exists: (p: string) => boolean = fs.existsSync
): string | undefined {
  const delimiter = platform === "win32" ? ";" : ":";
  const join = platform === "win32" ? path.win32.join : path.posix.join;
  for (const dir of envPath.split(delimiter)) {
    if (!dir) continue;
    for (const name of ["contextzip", "cz"]) {
      if (platform === "win32") {
        const exe = join(dir, `${name}.exe`);
        if (exists(exe)) return exe;
        if (exists(join(dir, `${name}.cmd`))) {
          const npmBinary = join(dir, "node_modules", "@contextzip", "cli-win32-x64", "bin", "contextzip", "contextzip.exe");
          if (exists(npmBinary)) return npmBinary;
        }
      } else {
        const bin = join(dir, name);
        if (exists(bin)) return bin;
      }
    }
  }
  return undefined;
}

/** The binary to run: the setting, then the bundled one, then PATH. */
export function resolveBinary(setting: string, extensionPath: string): Binary {
  const configured = setting.trim();
  if (configured) {
    if (!fs.existsSync(configured)) throw new CliNotFoundError(`the contextzip.path setting points to ${configured}`);
    return { command: configured, source: "setting" };
  }
  const bundled = bundledBinary(extensionPath);
  if (bundled) return { command: bundled, source: "bundled" };
  const onPath = findOnPath();
  if (onPath) return { command: onPath, source: "path" };
  throw new CliNotFoundError("no bundled binary, and no contextzip or cz on PATH");
}

/** Parse the one JSON object a --json run prints (the last non-empty line, to be safe). */
export function parseResult(stdout: string): CliResult | undefined {
  const lines = stdout.split(/\r?\n/).filter((l) => l.trim());
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line.startsWith("{")) continue;
    try {
      const data = JSON.parse(line);
      if (data && typeof data === "object" && "ok" in data) {
        return { warnings: [], log: [], ...data } as CliResult;
      }
    } catch {
      // keep looking
    }
  }
  return undefined;
}

export interface RunOptions {
  cwd: string;
  signal?: AbortSignal;
}

/** Run `contextzip <args> --json`. Resolves with the result even when ok is false. */
export function runJson(binary: Binary, args: string[], options: RunOptions): Promise<CliResult> {
  return new Promise((resolve, reject) => {
    execFile(
      binary.command,
      [...args, "--json"],
      { cwd: options.cwd, signal: options.signal, maxBuffer: 256 * 1024 * 1024, windowsHide: true },
      (error, stdout, stderr) => {
        const parsed = parseResult(String(stdout));
        if (parsed) return resolve(parsed);
        const code = (error as NodeJS.ErrnoException | null)?.code;
        if (code === "ENOENT" || code === "EACCES") return reject(new CliNotFoundError(binary.command));
        if (error?.name === "AbortError") return reject(error);
        const detail = String(stderr).trim().split(/\r?\n/).slice(-5).join("\n");
        resolve({
          ok: false,
          command: args[0] ?? "package",
          version: "",
          error: detail || error?.message || "contextzip didn't return a result",
          warnings: [],
          log: [],
        });
      }
    );
  });
}

/** The installed CLI version, from `contextzip --version`. */
export function cliVersion(binary: Binary, cwd: string): Promise<string | undefined> {
  return new Promise((resolve) => {
    execFile(binary.command, ["--version"], { cwd, windowsHide: true, timeout: 30_000 }, (error, stdout) => {
      const match = /(\d+\.\d+\.\d+)/.exec(String(stdout));
      resolve(error || !match ? undefined : match[1]);
    });
  });
}

export function isAtLeast(version: string, minimum: string): boolean {
  const a = version.split(".").map((n) => parseInt(n, 10) || 0);
  const b = minimum.split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) > (b[i] ?? 0);
  }
  return true;
}
