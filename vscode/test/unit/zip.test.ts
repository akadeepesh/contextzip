import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { listEntries, readGuide } from "../../src/zip";
import { makeZip } from "./zipFixture";

function write(name: string, data: Buffer): string {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cz-zip-")), name);
  fs.writeFileSync(file, data);
  return file;
}

test("lists entries of stored and deflated ZIPs", () => {
  for (const deflate of [true, false]) {
    const file = write("r.zip", makeZip({ "src/a.ts": "export {}\n", "b.txt": "hi" }, deflate));
    assert.deepEqual(listEntries(file)?.map((e) => e.name), ["src/a.ts", "b.txt"]);
  }
});

test("reads CONTEXTZIP.md wherever the AI put it", () => {
  const file = write("reply.zip", makeZip({ "my-app-fixed/CONTEXTZIP.md": "Snapshot: 3-abc123\n", "my-app-fixed/a.ts": "x" }));
  assert.equal(readGuide(file), "Snapshot: 3-abc123\n");
});

test("a ZIP without CONTEXTZIP.md, or not a ZIP at all, reads as nothing", () => {
  assert.equal(readGuide(write("plain.zip", makeZip({ "a.ts": "x" }))), undefined);
  assert.equal(listEntries(write("fake.zip", Buffer.from("not a zip"))), undefined);
});
