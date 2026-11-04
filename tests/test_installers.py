from __future__ import annotations

import importlib.util
import json
import os
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


def load_build_binary():
    spec = importlib.util.spec_from_file_location("cz_build_binary", ROOT / "installers" / "build_binary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def can_symlink(where: Path) -> bool:
    try:
        (where / "probe-link").symlink_to(where)
    except (OSError, NotImplementedError):
        return False
    (where / "probe-link").unlink()
    return True


class MaterializeSymlinksTests(TempProject):
    def test_macos_framework_links_become_copies(self):
        if os.name == "nt" or not can_symlink(self.tmp):
            self.skipTest("only macOS builds have symlinks to replace")
        app = self.tmp / "contextzip"
        version = app / "_internal" / "Python.framework" / "Versions" / "3.12"
        (version / "Resources").mkdir(parents=True)
        (version / "Python").write_bytes(b"dylib")
        (version / "Resources" / "Info.plist").write_text("plist", encoding="utf-8")
        framework = app / "_internal" / "Python.framework"
        (framework / "Versions" / "Current").symlink_to("3.12", target_is_directory=True)
        (framework / "Python").symlink_to("Versions/Current/Python")
        (framework / "Resources").symlink_to("Versions/Current/Resources", target_is_directory=True)
        (app / "_internal" / "Python").symlink_to("Python.framework/Versions/3.12/Python")

        self.assertEqual(load_build_binary().materialize_symlinks(app), 4)
        self.assertEqual([p for p in app.rglob("*") if p.is_symlink()], [])
        self.assertEqual((app / "_internal" / "Python").read_bytes(), b"dylib")
        self.assertEqual((framework / "Resources" / "Info.plist").read_text(encoding="utf-8"), "plist")


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


def py_install(prefix, exe="/env/bin/python", in_venv=True, has_pip=True, cfg=None, marker=None):
    prefix = Path(prefix)
    if cfg is not None:
        (prefix / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
    if marker:
        (prefix / marker).write_text("", encoding="utf-8")
    return updater.PythonInstall(executable=exe, prefix=str(prefix), in_venv=in_venv, has_pip=has_pip)


def with_tools(*names):
    return lambda name: f"/bin/{name}" if name in names else None


class UpdatePlanTests(TempProject):
    def methods(self, install, *tools):
        return [s.method for s in updater.update_plan(install, with_tools(*tools))]

    def test_plain_pip_install_falls_back_to_uv(self):
        install = py_install(self.root, in_venv=False)
        self.assertEqual(self.methods(install, "uv"), ["pip", "uv pip"])
        self.assertEqual(self.methods(install), ["pip"])

    def test_uv_made_venv_prefers_uv_and_skips_missing_pip(self):
        install = py_install(self.root, has_pip=False, cfg="home = /usr/bin\nuv = 0.13.1\n")
        self.assertTrue(install.made_by_uv)
        self.assertEqual(self.methods(install, "uv"), ["uv pip"])

    def test_uv_tool_install(self):
        install = py_install(self.root, marker="uv-receipt.toml")
        self.assertEqual(self.methods(install, "uv"), ["uv tool", "uv pip", "pip"])

    def test_uv_tool_detected_by_windows_path(self):
        install = py_install(self.root, exe=r"C:\Users\me\AppData\Roaming\uv\data\tools\contextzip\Scripts\python.exe")
        self.assertTrue(install.via_uv_tool)

    def test_pipx_install(self):
        install = py_install(self.root, marker="pipx_metadata.json")
        self.assertEqual(self.methods(install, "pipx", "uv"), ["pipx", "pip", "uv pip"])

    def test_uv_pip_targets_the_running_interpreter(self):
        install = py_install(self.root, exe="/proj/.venv/bin/python", has_pip=False, cfg="uv = 0.13.1\n")
        (step,) = updater.update_plan(install, with_tools("uv"))
        self.assertEqual(step.cmd[:5], ("uv", "pip", "install", "--python", "/proj/.venv/bin/python"))


class RunUpdateTests(TempProject):
    def run_with(self, install, outcomes, versions, tools=("uv",)):
        calls = []

        def fake_run(cmd, timeout):
            calls.append(cmd)
            return outcomes.pop(0)

        with mock.patch.object(updater, "_run", side_effect=fake_run), mock.patch.object(
            updater, "_installed_version", side_effect=lambda exe: versions.pop(0)
        ), mock.patch.object(updater, "binary_install", return_value=None):
            result = updater.run_update(target="0.6.0", install=install, which=with_tools(*tools))
        return result, calls

    def test_falls_back_to_uv_when_pip_fails(self):
        install = py_install(self.root)
        result, calls = self.run_with(install, [(False, "No module named pip"), (True, "ok")], ["0.6.0"])
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "uv pip")
        self.assertEqual([a.step.method for a in result.attempts], ["pip", "uv pip"])

    def test_exit_zero_without_new_version_is_not_success(self):
        install = py_install(self.root)
        result, _ = self.run_with(install, [(True, "Requirement already satisfied"), (True, "")], ["0.5.6", "0.6.0"])
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "uv pip")
        self.assertIn("still on 0.5.6", result.attempts[0].output)

    def test_permission_error_retries_with_user_outside_venv(self):
        install = py_install(self.root, in_venv=False)
        result, calls = self.run_with(install, [(False, "[Errno 13] Permission denied"), (True, "")], ["0.6.0"], tools=())
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "pip --user")
        self.assertEqual(calls[-1][-1], "--user")

    def test_externally_managed_gives_a_real_hint(self):
        install = py_install(self.root, in_venv=False)
        result, _ = self.run_with(
            install, [(False, "error: externally-managed-environment"), (False, "externally managed")], []
        )
        self.assertFalse(result.ok)
        self.assertIn("uv tool install contextzip", result.hint)

    def test_cli_lists_every_attempt_on_failure(self):
        newer = VersionCheck(current="0.5.6", latest="0.6.0")
        failed = updater.UpdateResult(
            ok=False, method="uv pip", output="boom",
            attempts=[
                updater.Attempt(updater.UpdateStep("pip", ("python", "-m", "pip")), False, "pip broke"),
                updater.Attempt(updater.UpdateStep("uv pip", ("uv", "pip")), False, "uv broke"),
            ],
            hint="try something",
        )
        with mock.patch.object(updater, "binary_install", return_value=None), mock.patch.object(
            updater, "check_latest_version", return_value=newer
        ), mock.patch.object(updater, "run_update", return_value=failed):
            result = CliRunner().invoke(cli.main, ["update", "--yes"])
        self.assertEqual(result.exit_code, 1, result.output)
        for text in ("pip broke", "uv broke", "try something", "then uv pip"):
            self.assertIn(text, result.output)
