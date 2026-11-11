import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FitInfo,
  Plan,
  fitSummary,
  formatTokens,
  humanSize,
  packageSummary,
  planCounts,
  riskyFiles,
  safeFiles,
  snapshotFromGuide,
  tooBigForAny,
} from "../../src/format";

test("formatTokens matches the CLI's rounding", () => {
  assert.equal(formatTokens(850), "≈ 850 tokens");
  assert.equal(formatTokens(4_249), "≈ 4.2k tokens");
  assert.equal(formatTokens(46_400), "≈ 46k tokens");
  assert.equal(formatTokens(999_700), "≈ 1.0M tokens");
});

test("formatTokens with an exact count", () => {
  assert.equal(formatTokens(46_210, true), "46,210 tokens");
  assert.equal(formatTokens(1, true), "1 token");
  assert.equal(formatTokens(0, true), "0 tokens");
  assert.equal(formatTokens(1_300_000, true), "1,300,000 tokens");
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

test("packageSummary with token counts from contextzip 0.8", () => {
  const base = { path: "x.zip", files: 44, bytes: 190_000, compressed_bytes: 64_512, estimated_tokens: 46_210, snapshot: "1-abc" };
  const tokens = { total: 46_210, method: "o200k_base" as const, exact: true, by_dir: [], top_files: [] };
  assert.equal(packageSummary({ ...base, tokens }), "Packed 44 files · 63 KB · 46,210 tokens");
  const estimate = { ...tokens, method: "estimate" as const, exact: false };
  assert.equal(packageSummary({ ...base, tokens: estimate }), "Packed 44 files · 63 KB · ≈ 46k tokens");
});

const row = (name: string, share: number, exact = false): FitInfo => ({
  target: name.toLowerCase(),
  name,
  window: 1000,
  tokens: Math.ceil(share * 1000),
  share,
  exact,
  fits: share <= 1,
});

test("fitSummary when everything fits", () => {
  assert.equal(fitSummary([row("Claude", 0.23), row("ChatGPT", 0.36, true)]), "Fits Claude ≈23% · ChatGPT 36%");
  assert.equal(fitSummary([row("Gemini", 0.004)]), "Fits Gemini ≈<1%");
  assert.equal(tooBigForAny([row("Claude", 0.23)]), false);
});

test("fitSummary names only what it's too big for", () => {
  assert.equal(fitSummary([row("Claude", 1.31), row("Gemini", 0.5)]), "Too big for Claude (≈131%)");
  assert.equal(fitSummary([row("Claude", 1.31, true), row("ChatGPT", 4.2), row("Gemini", 2)]), "Too big for Claude (131%), ChatGPT (≈420%) and Gemini (≈200%)");
  assert.equal(tooBigForAny([row("Claude", 1.31), row("Gemini", 0.5)]), true);
});

test("an older CLI without fit data changes nothing", () => {
  assert.equal(fitSummary(undefined), undefined);
  assert.equal(fitSummary(null), undefined);
  assert.equal(fitSummary([]), undefined);
  assert.equal(tooBigForAny(undefined), false);
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
