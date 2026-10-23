import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { DownloadsWatcher, downloadsFolder, isCandidateName } from "../../src/downloads";

test("downloadsFolder defaults to ~/Downloads", () => {
  assert.equal(downloadsFolder("/home/me", "darwin"), path.join("/home/me", "Downloads"));
});

test("only finished .zip files are candidates", () => {
  assert.ok(isCandidateName("reply.zip"));
  assert.ok(isCandidateName("REPLY.ZIP"));
  assert.ok(!isCandidateName("reply.zip.crdownload"));
  assert.ok(!isCandidateName("reply.zip.part"));
});

test("reports a new ZIP once, after it stops growing", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cz-dl-"));
  const seen: string[] = [];
  const watcher = new DownloadsWatcher(dir, (p) => seen.push(p), { settleMs: 100 });
  assert.ok(watcher.start());
  try {
    const partial = path.join(dir, "reply.zip.crdownload");
    fs.writeFileSync(partial, "partial");
    await new Promise((r) => setTimeout(r, 150));
    fs.renameSync(partial, path.join(dir, "reply.zip"));
    fs.writeFileSync(path.join(dir, "notes.txt"), "ignored");
    await new Promise((r) => setTimeout(r, 800));
    assert.deepEqual(seen, [path.join(dir, "reply.zip")]);
  } finally {
    watcher.dispose();
  }
});

test("a folder that doesn't exist can't be watched", () => {
  const watcher = new DownloadsWatcher(path.join(os.tmpdir(), "cz-no-such-folder-xyz"), () => {});
  assert.equal(watcher.start(), false);
});
