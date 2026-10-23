from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from contextzip import __version__, cli, updater
from contextzip.updater import VersionCheck, binary_install
from tests.helpers import TempProject

ROOT = Path(__file__).resolve().parent.parent
TARGETS = json.loads((ROOT / "installers" / "targets.json").read_text(encoding="utf-8"))


def load_manifests():
    spec = importlib.util.spec_from_file_location("cz_manifests", ROOT / "installers" / "manifests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class BinaryInstallTests(TempProject):
    def test_not_frozen_means_a_python_install(self):
        self.assertIsNone(binary_install("C:/Python312/python.exe", frozen=False))

    def test_each_installer_is_recognised(self):
        cases = {
            "C:/Users/me/AppData/Roaming/npm/node_modules/@contextzip/cli-win32-x64/bin/contextzip/contextzip.exe": "npm",
            "/home/me/.npm/_npx/1a2b/node_modules/@contextzip/cli-linux-x64/bin/contextzip/contextzip": "npx",
            "/opt/homebrew/Cellar/contextzip/0.5.3/libexec/contextzip": "Homebrew",
            "/home/linuxbrew/.linuxbrew/Cellar/contextzip/0.5.3/libexec/contextzip": "Homebrew",
            "C:\\Users\\me\\scoop\\apps\\contextzip\\current\\contextzip.exe": "Scoop",
            "D:/tools/contextzip/contextzip.exe": "a download",
        }
        for exe, manager in cases.items():
            with self.subTest(exe=exe):
                self.assertEqual(binary_install(exe, frozen=True).manager, manager)

    def test_npm_update_command_names_the_package(self):
        got = binary_install("/usr/lib/node_modules/@contextzip/cli-linux-x64/bin/contextzip/contextzip", frozen=True)
        self.assertEqual(got.update_command, "npm install -g @contextzip/cli@latest")


class ReleaseFilesTests(TempProject):
    """The files a release is built from must agree with each other."""

    def test_npm_launcher_version_matches_the_package(self):
        pkg = json.loads((ROOT / "npm" / "cli" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["name"], updater.NPM_PACKAGE)
        self.assertEqual(pkg["version"], __version__, "bump npm/cli/package.json with contextzip/__init__.py")
        expected = {f"@contextzip/cli-{t['target']}": __version__ for t in TARGETS}
        self.assertEqual(pkg["optionalDependencies"], expected)
        self.assertEqual(set(pkg["bin"].values()), {"bin/contextzip.js"})  # one file, so npx can pick it

    def test_vscode_extension_bundles_this_version(self):
        ext = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ext["contextzip"]["cliVersion"], __version__, "bump vscode/package.json contextzip.cliVersion too"
        )

    def test_targets_use_node_platform_names(self):
        for t in TARGETS:
            self.assertEqual(t["target"], f"{t['os']}-{t['cpu']}")
            self.assertIn(t["os"], {"win32", "darwin", "linux"})
            self.assertIn(t["cpu"], {"x64", "arm64"})

    def test_pyproject_version_matches(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', text)


class ManifestTests(TempProject):
    def sums(self, version="1.2.3") -> dict[str, str]:
        out = {f"contextzip-{version}-{t['target']}.tar.gz": f"{i:064x}" for i, t in enumerate(TARGETS)}
        out[f"contextzip-{version}-win32-x64.zip"] = "f" * 64
        return out

    def test_checksums_file_is_parsed(self):
        path = self.tmp / "SHA256SUMS"
        path.write_text("ABC123  contextzip-1.2.3-linux-x64.tar.gz\nabc *contextzip-1.2.3-win32-x64.zip\n", encoding="utf-8")
        got = load_manifests().read_checksums(path)
        self.assertEqual(got, {"contextzip-1.2.3-linux-x64.tar.gz": "abc123", "contextzip-1.2.3-win32-x64.zip": "abc"})

    def test_homebrew_formula(self):
        sums = self.sums()
        formula = load_manifests().homebrew_formula("1.2.3", sums)
        for target in ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64"):
            name = f"contextzip-1.2.3-{target}.tar.gz"
            self.assertIn(f"https://github.com/akadeepesh/contextzip/releases/download/v1.2.3/{name}", formula)
            self.assertIn(sums[name], formula)
        self.assertIn('(bin/"cz").write_exec_script libexec/"contextzip"', formula)

    def test_scoop_manifest(self):
        manifest = load_manifests().scoop_manifest("1.2.3", self.sums())
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertEqual(manifest["architecture"]["64bit"]["hash"], "f" * 64)
        self.assertTrue(manifest["architecture"]["64bit"]["url"].endswith("/v1.2.3/contextzip-1.2.3-win32-x64.zip"))
        self.assertIn(["contextzip.exe", "cz"], manifest["bin"])

    def test_missing_asset_stops(self):
        with self.assertRaises(SystemExit):
            load_manifests().homebrew_formula("9.9.9", self.sums())


class StandaloneCommandTests(TempProject):
    def test_update_points_to_the_installer(self):
        install = updater.BinaryInstall("Homebrew", "brew upgrade contextzip")
        newer = VersionCheck(current="0.5.3", latest="0.5.4")
        with mock.patch.object(updater, "binary_install", return_value=install), mock.patch.object(
            updater, "check_latest_version", return_value=newer
        ), mock.patch.object(updater, "run_update") as run_update:
            result = CliRunner().invoke(cli.main, ["update", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("brew upgrade contextzip", result.output)
        run_update.assert_not_called()

    def test_setup_path_explains_a_standalone_build(self):
        install = updater.BinaryInstall("npm", "npm install -g @contextzip/cli@latest")
        with mock.patch.object(updater, "binary_install", return_value=install):
            result = CliRunner().invoke(cli.main, ["setup-path"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("standalone build", result.output)
