#!/usr/bin/env node
// Packages the extension as VSIX files, from the release archives that
// installers/build_binary.py makes:
//
//   node vscode/scripts/package.mjs --artifacts dist/release --out dist/vscode
//
//   contextzip-<target>-<version>.vsix   one per platform, with that binary
//                                        bundled in bin/contextzip/
//   contextzip-<version>.vsix            no binary: uses contextzip/cz from PATH
//
// The bundled CLI is the version in package.json's "contextzip.cliVersion".
// --allow-missing skips targets whose archive isn't there (local testing).

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ext = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(ext, "..");
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};
const artifacts = path.resolve(option("--artifacts", "dist/release"));
const out = path.resolve(option("--out", "dist/vscode"));
const allowMissing = args.includes("--allow-missing");

const manifest = JSON.parse(fs.readFileSync(path.join(ext, "package.json"), "utf8"));
const cliVersion = manifest.contextzip.cliVersion;
const targets = JSON.parse(fs.readFileSync(path.join(root, "installers", "targets.json"), "utf8"));
const bin = path.join(ext, "bin");
const vsce = path.join(ext, "node_modules", "@vscode", "vsce", "vsce");

function vscePackage(extra, file) {
  execFileSync(process.execPath, [vsce, "package", "--no-dependencies", ...extra, "--out", path.join(out, file)], {
    cwd: ext,
    stdio: "inherit",
  });
}

fs.mkdirSync(out, { recursive: true });
fs.copyFileSync(path.join(root, "LICENSE"), path.join(ext, "LICENSE"));
execFileSync(process.execPath, [path.join(ext, "node_modules", "typescript", "bin", "tsc"), "-p", ext], { stdio: "inherit" });

const built = [];
try {
  for (const t of targets) {
    const archive = path.join(artifacts, `contextzip-${cliVersion}-${t.target}.tar.gz`);
    if (!fs.existsSync(archive)) {
      if (allowMissing) continue;
      throw new Error(`missing ${archive}: the extension bundles contextzip ${cliVersion}`);
    }
    fs.rmSync(bin, { recursive: true, force: true });
    fs.mkdirSync(bin);
    // Relative path: GNU tar reads "C:\…" as a remote host.
    const rel = path.relative(bin, archive).split(path.sep).join("/");
    execFileSync("tar", ["-xzf", rel], { cwd: bin, stdio: "inherit" });
    vscePackage(["--target", t.target], `contextzip-${t.target}-${manifest.version}.vsix`);
    built.push(t.target);
  }
  fs.rmSync(bin, { recursive: true, force: true });
  vscePackage([], `contextzip-${manifest.version}.vsix`);
  built.push("universal");
} finally {
  fs.rmSync(bin, { recursive: true, force: true });
}
console.log(`packaged extension ${manifest.version} (contextzip ${cliVersion}) for ${built.join(", ")} in ${out}`);
