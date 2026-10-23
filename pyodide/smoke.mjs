// Runs the real contextzip wheel inside Pyodide — the WebAssembly Python
// that contextzipcli.com uses to package a dropped folder in the browser —
// and checks a package + apply round trip through contextzip.web.
//
//   python -m pip wheel .. --no-deps -w ../dist/wheel
//   npm ci && node smoke.mjs ../dist/wheel/contextzip-*.whl
//
// Dependencies (click, rich, pathspec, httpx) come from PyPI via micropip,
// as they would in the browser.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadPyodide } from "pyodide";

const wheel = process.argv[2];
if (!wheel || !fs.existsSync(wheel)) {
  console.error("usage: node smoke.mjs path/to/contextzip-*.whl");
  process.exit(2);
}

const py = await loadPyodide();
await py.loadPackage("micropip");
const wheelName = path.basename(wheel);
py.FS.writeFile(`/tmp/${wheelName}`, fs.readFileSync(wheel));
await py.pyimport("micropip").install(`emfs:/tmp/${wheelName}`);

// A small project in Pyodide's in-memory file system, like a dropped folder.
const files = {
  "/project/package.json": '{"name": "smoke"}\n',
  "/project/src/index.js": "module.exports = 1;\n",
  "/project/node_modules/dep/index.js": "x\n",
  "/project/.env": "TOKEN=secret\n",
};
for (const [file, text] of Object.entries(files)) {
  py.FS.mkdirTree(path.posix.dirname(file));
  py.FS.writeFile(file, text);
}

const runJson = py.pyimport("contextzip.web").run_json;
const run = (args, cwd) => JSON.parse(runJson(py.toPy(args), cwd));

const packaged = run(["--no-clipboard"], "/project");
assert.equal(packaged.ok, true, JSON.stringify(packaged));
assert.ok(packaged.detection.ecosystems.includes("Node.js"));
assert.deepEqual(packaged.files.included_files, ["package.json", "src/index.js"]);
assert.ok(packaged.zip.estimated_tokens > 0);
const zipBytes = py.FS.readFile(packaged.zip.path);
assert.ok(zipBytes.length > 0);

// The AI's reply, flattened: index.js instead of src/index.js.
py.runPython(`
import zipfile
with zipfile.ZipFile("/reply.zip", "w") as z:
    z.writestr("index.js", "module.exports = 2;\\n")
`);
const applied = run(["apply-zip", "/reply.zip", "--safe-only"], "/project");
assert.equal(applied.ok, true, JSON.stringify(applied));
assert.deepEqual(applied.result.written, ["src/index.js"]);
assert.equal(py.FS.readFile("/project/src/index.js", { encoding: "utf8" }), "module.exports = 2;\n");

console.log(`contextzip ${packaged.version} works in Pyodide ${py.version}: packaged ${packaged.zip.files} files, applied a flattened reply`);
