import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { CliNotFoundError, bundledBinary, findOnPath, isAtLeast, parseResult, resolveBinary, runJson } from "../../src/cli";

test("parseResult reads the JSON line, ignoring anything around it", () => {
  const out = 'warning from something\n{"ok": true, "command": "package", "version": "0.5.3", "zip": null}\n\n';
  const result = parseResult(out);
  assert.equal(result?.ok, true);
  assert.equal(result?.command, "package");
  assert.deepEqual(result?.warnings, []);
  assert.equal(parseResult("no json here"), undefined);
});

test("isAtLeast compares versions numerically", () => {
  assert.ok(isAtLeast("0.5.10", "0.5.2"));
  assert.ok(isAtLeast("0.5.2", "0.5.2"));
  assert.ok(!isAtLeast("0.5.1", "0.5.2"));
  assert.ok(isAtLeast("1.0.0", "0.9.9"));
});

test("findOnPath prefers contextzip, accepts cz, and only .exe on Windows", () => {
  const files = new Set(["C:\\py\\Scripts\\cz.exe", "/usr/local/bin/contextzip"]);
  const exists = (p: string) => files.has(p);
  assert.equal(findOnPath("C:\\nothing;C:\\py\\Scripts", "win32", exists), "C:\\py\\Scripts\\cz.exe");
  assert.equal(findOnPath("/usr/bin:/usr/local/bin", "linux", exists), "/usr/local/bin/contextzip");
  assert.equal(findOnPath("/usr/bin", "linux", exists), undefined);
});

test("findOnPath follows an npm global cz.cmd to its binary", () => {
  const npmDir = "C:\\Users\\me\\AppData\\Roaming\\npm";
  const binary = `${npmDir}\\node_modules\\@contextzip\\cli-win32-x64\\bin\\contextzip\\contextzip.exe`;
  const files = new Set([`${npmDir}\\contextzip.cmd`, binary]);
  assert.equal(findOnPath(npmDir, "win32", (p) => files.has(p)), binary);
});

test("bundledBinary finds bin/contextzip inside the extension", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cz-ext-"));
  assert.equal(bundledBinary(dir), undefined);
  const exe = path.join(dir, "bin", "contextzip", process.platform === "win32" ? "contextzip.exe" : "contextzip");
  fs.mkdirSync(path.dirname(exe), { recursive: true });
  fs.writeFileSync(exe, "");
  assert.equal(bundledBinary(dir), exe);
  if (process.platform !== "win32") assert.ok(fs.statSync(exe).mode & 0o100, "made executable");
});

test("resolveBinary rejects a contextzip.path that doesn't exist", () => {
  assert.throws(() => resolveBinary("/no/such/contextzip", os.tmpdir()), CliNotFoundError);
});

/** A stand-in CLI: node running a script that prints what a --json run would. */
function fakeCli(body: string): { command: string; source: "setting"; script: string } {
  const script = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cz-fake-")), "fake.js");
  fs.writeFileSync(script, body);
  return { command: process.execPath, source: "setting", script };
}

test("runJson passes --json last and returns the parsed result", async () => {
  const cli = fakeCli(
    'console.log(JSON.stringify({ok: true, command: "package", version: "0.5.3", args: process.argv.slice(2), warnings: [], log: []}));'
  );
  const result = await runJson(cli, [cli.script, "include", "src/"], { cwd: os.tmpdir() });
  assert.equal(result.ok, true);
  assert.deepEqual(result.args, ["include", "src/", "--json"]);
});

test("runJson keeps the CLI's own error when it exits non-zero", async () => {
  const cli = fakeCli(
    'console.log(JSON.stringify({ok: false, command: "package", version: "0.5.3", error: "Nothing to compress", warnings: [], log: []})); process.exit(1);'
  );
  const result = await runJson(cli, [cli.script], { cwd: os.tmpdir() });
  assert.equal(result.ok, false);
  assert.equal(result.error, "Nothing to compress");
});

test("runJson turns a crash without JSON into an error result", async () => {
  const cli = fakeCli('process.stderr.write("Traceback: boom\\n"); process.exit(2);');
  const result = await runJson(cli, [cli.script], { cwd: os.tmpdir() });
  assert.equal(result.ok, false);
  assert.match(result.error ?? "", /boom/);
});

test("runJson reports a missing binary as CliNotFoundError", async () => {
  await assert.rejects(
    runJson({ command: path.join(os.tmpdir(), "definitely-not-here-cz"), source: "setting" }, [], { cwd: os.tmpdir() }),
    CliNotFoundError
  );
});
