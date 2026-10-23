// Runs inside VS Code (see run.ts). Throws on the first failure.

import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import type { ContextzipApi } from "../../src/extension";
import { makeZip } from "../unit/zipFixture";

export async function run(): Promise<void> {
  const ext = vscode.extensions.getExtension<ContextzipApi>("contextzip.contextzip");
  assert.ok(ext, "the extension is installed");
  const api = await ext.activate();

  const commands = await vscode.commands.getCommands(true);
  for (const id of [
    "contextzip.package",
    "contextzip.packageSelection",
    "contextzip.packageChanges",
    "contextzip.packageForTask",
    "contextzip.packageGitHub",
    "contextzip.applyZip",
  ]) {
    assert.ok(commands.includes(id), `${id} is registered`);
  }

  const workspace = process.env.CZ_TEST_WORKSPACE;
  assert.ok(workspace, "CZ_TEST_WORKSPACE is set");

  const packaged = await api.run(["--no-clipboard"], workspace);
  assert.equal(packaged.ok, true, JSON.stringify(packaged));
  const zip = packaged.zip as { files: number; snapshot: string };
  assert.equal(zip.files, 2, "package.json and src/index.js, not node_modules");

  // An AI reply that flattened src/index.js to index.js and echoed CONTEXTZIP.md.
  const reply = path.join(path.dirname(workspace), `reply-${Date.now()}.zip`);
  fs.writeFileSync(reply, makeZip({ "index.js": "module.exports = 2;\n", "CONTEXTZIP.md": `Snapshot: ${zip.snapshot}\n` }));
  const applied = await api.run(["apply-zip", reply, "--safe-only"], workspace);
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.deepEqual((applied.result as { written: string[] }).written, ["src/index.js"]);
  assert.equal(fs.readFileSync(path.join(workspace, "src", "index.js"), "utf8"), "module.exports = 2;\n");
}
