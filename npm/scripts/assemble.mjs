#!/usr/bin/env node
// Turns the release archives from installers/build_binary.py into npm
// packages, ready for `npm publish`:
//
//   node npm/scripts/assemble.mjs --artifacts dist/release --out dist/npm
//
//   dist/npm/cli-<target>/   @contextzip/cli-<target>: the binary for one platform
//   dist/npm/cli/            @contextzip/cli: the launcher, with every platform
//                            package as an optional dependency (npm installs the
//                            one matching the machine's os + cpu)
//
// --allow-missing assembles whatever targets are present and drops the rest
// from optionalDependencies (for testing on one machine). Without it, every
// target in installers/targets.json must be there.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};
const artifacts = path.resolve(option("--artifacts", "dist/release"));
const out = path.resolve(option("--out", "dist/npm"));
const allowMissing = args.includes("--allow-missing");

const targets = JSON.parse(fs.readFileSync(path.join(root, "installers", "targets.json"), "utf8"));
const launcher = JSON.parse(fs.readFileSync(path.join(root, "npm", "cli", "package.json"), "utf8"));
const version = launcher.version;

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });

const built = [];
for (const t of targets) {
  const archive = path.join(artifacts, `contextzip-${version}-${t.target}.tar.gz`);
  if (!fs.existsSync(archive)) {
    if (allowMissing) continue;
    throw new Error(`missing ${archive} — build it with installers/build_binary.py on ${t.target}`);
  }
  const name = `@contextzip/cli-${t.target}`;
  if (launcher.optionalDependencies?.[name] !== version) {
    throw new Error(`npm/cli/package.json must list ${name}@${version} in optionalDependencies`);
  }
  const dir = path.join(out, `cli-${t.target}`);
  const binDir = path.join(dir, "bin");
  fs.mkdirSync(binDir, { recursive: true });
  // Unpacks to bin/contextzip/; tar keeps the executable bit. Run from the
  // destination with a relative path: GNU tar reads "C:\…" as a remote host.
  const relArchive = path.relative(binDir, archive).split(path.sep).join("/");
  execFileSync("tar", ["-xzf", relArchive], { cwd: binDir, stdio: "inherit" });
  const manifest = {
    name,
    version,
    description: `The contextzip binary for ${t.target}. Install @contextzip/cli instead; it picks this for you.`,
    homepage: launcher.homepage,
    repository: launcher.repository,
    license: launcher.license,
    os: [t.os],
    cpu: [t.cpu],
    files: ["bin/"],
    preferUnplugged: true,
  };
  fs.writeFileSync(path.join(dir, "package.json"), JSON.stringify(manifest, null, 2) + "\n");
  fs.writeFileSync(
    path.join(dir, "README.md"),
    `# ${name}\n\nThe standalone contextzip ${version} binary for ${t.target}.\n\n` +
      "Don't install this directly: `npm install -g @contextzip/cli` installs the right one for your machine.\n"
  );
  fs.copyFileSync(path.join(root, "LICENSE"), path.join(dir, "LICENSE"));
  built.push(name);
}
if (built.length === 0) throw new Error(`no release archives for ${version} in ${artifacts}`);

const cliDir = path.join(out, "cli");
fs.cpSync(path.join(root, "npm", "cli"), cliDir, { recursive: true });
fs.copyFileSync(path.join(root, "LICENSE"), path.join(cliDir, "LICENSE"));
if (allowMissing) {
  const manifest = { ...launcher, optionalDependencies: {} };
  for (const name of built) manifest.optionalDependencies[name] = version;
  fs.writeFileSync(path.join(cliDir, "package.json"), JSON.stringify(manifest, null, 2) + "\n");
}

console.log(`assembled @contextzip/cli ${version} with ${built.join(", ")} in ${out}`);
