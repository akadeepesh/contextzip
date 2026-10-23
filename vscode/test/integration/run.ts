// Launches VS Code with the extension and runs suite.ts inside it.
//   npm run test:integration
// Needs a contextzip binary: bin/contextzip/ (as in a platform build), or
// contextzip on PATH.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = path.resolve(__dirname, "..", "..", "..");
  const extensionTestsPath = path.resolve(__dirname, "suite");

  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "cz-ext-fixture-"));
  fs.mkdirSync(path.join(workspace, "src"));
  fs.mkdirSync(path.join(workspace, "node_modules", "dep"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "package.json"), '{"name": "fixture"}\n');
  fs.writeFileSync(path.join(workspace, "src", "index.js"), "module.exports = 1;\n");
  fs.writeFileSync(path.join(workspace, "node_modules", "dep", "index.js"), "x\n");

  // Set when this is started from inside another editor's extension host;
  // it would make the downloaded VS Code run as plain Node.
  delete process.env.ELECTRON_RUN_AS_NODE;

  await runTests({
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: [workspace, "--disable-extensions", "--skip-welcome", "--skip-release-notes"],
    extensionTestsEnv: { CZ_TEST_WORKSPACE: workspace },
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
