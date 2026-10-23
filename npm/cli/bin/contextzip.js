#!/usr/bin/env node
// Launches the standalone contextzip binary that npm installed for this
// platform (one of the @contextzip/cli-<os>-<cpu> optional dependencies).
// Both `cz` and `contextzip` point here.
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const target = `${process.platform}-${process.arch}`;
const exeName = process.platform === "win32" ? "contextzip.exe" : "contextzip";

function fail(message) {
  process.stderr.write(`contextzip: ${message}\n`);
  process.exit(1);
}

function findBinary() {
  const override = process.env.CONTEXTZIP_BINARY;
  if (override) return override;

  let dir;
  try {
    dir = path.dirname(require.resolve(`@contextzip/cli-${target}/package.json`));
  } catch {
    fail(
      `there's no prebuilt binary for ${target}, or npm skipped installing it ` +
        "(optional dependencies turned off?).\n" +
        "Install with Python instead: pip install contextzip   (needs Python 3.9+)"
    );
  }
  const exe = path.join(dir, "bin", "contextzip", exeName);
  if (!fs.existsSync(exe)) fail(`the binary is missing from ${dir}. Reinstall @contextzip/cli.`);
  if (process.platform !== "win32") {
    try {
      fs.accessSync(exe, fs.constants.X_OK);
    } catch {
      try {
        fs.chmodSync(exe, 0o755);
      } catch {
        // Not ours to fix (a read-only global install); spawn will report it.
      }
    }
  }
  return exe;
}

const child = spawn(findBinary(), process.argv.slice(2), { stdio: "inherit" });

// Ctrl+C reaches the binary directly (same terminal), and it decides what to
// do: `contextzip watch` offers to capture the session first. So this
// launcher must not exit on those signals itself.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"]) {
  try {
    process.on(signal, () => {});
  } catch {
    // SIGBREAK only exists on Windows, SIGHUP doesn't there.
  }
}

child.on("error", (err) => fail(`couldn't start the binary: ${err.message}`));
child.on("exit", (code, signal) => {
  if (signal) {
    process.removeAllListeners(signal);
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});
