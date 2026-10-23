import assert from "node:assert/strict";
import { test } from "node:test";
import { Plan, formatTokens, humanSize, packageSummary, planCounts, riskyFiles, safeFiles, snapshotFromGuide } from "../../src/format";

test("formatTokens matches the CLI's rounding", () => {
  assert.equal(formatTokens(850), "≈ 850 tokens");
  assert.equal(formatTokens(4_249), "≈ 4.2k tokens");
  assert.equal(formatTokens(46_400), "≈ 46k tokens");
  assert.equal(formatTokens(999_700), "≈ 1.0M tokens");
});

test("humanSize", () => {
  assert.equal(humanSize(512), "512 B");
  assert.equal(humanSize(64_512), "63 KB");
  assert.equal(humanSize(1_572_864), "1.5 MB");
});

test("packageSummary", () => {
  const zip = { path: "x.zip", files: 44, bytes: 190_000, compressed_bytes: 64_512, estimated_tokens: 46_400, snapshot: "1-abc" };
  assert.equal(packageSummary(zip), "Packed 44 files · 63 KB · ≈ 46k tokens");
});

test("plan helpers split safe and risky files", () => {
  const file = (path: string, status: any, safe: boolean) => ({ path, source: path, status, safe, reason: "", note: null, moved_from: null });
  const plan: Plan = {
    zip: "r.zip",
    snapshot: "3-abc",
    needs_decision: true,
    counts: { modified: 2, new: 1, conflict: 1, unchanged: 1 },
    files: [file("a", "modified", true), file("b", "modified", true), file("c", "new", true), file("d", "conflict", false), file("e", "unchanged", false)],
    remap: null,
    structure_warning: null,
  };
  assert.deepEqual(planCounts(plan), { safe: "2 modified, 1 new", risky: "1 conflict" });
  assert.deepEqual(safeFiles(plan).map((f) => f.path), ["a", "b", "c"]);
  assert.deepEqual(riskyFiles(plan).map((f) => f.path), ["d"]);
});

test("snapshotFromGuide reads the label CONTEXTZIP.md carries", () => {
  assert.equal(snapshotFromGuide("# CONTEXTZIP.md\n\nSnapshot: 14-k3f9a2\n"), "14-k3f9a2");
  assert.equal(snapshotFromGuide("snapshot: `7-ABC123`"), "7-abc123");
  assert.equal(snapshotFromGuide("no snapshot here"), undefined);
});
