"""
build_binary.py — Build, smoke-test and archive the standalone contextzip
binary for the machine it runs on. No Python needed by whoever runs it.

    python -m pip install . pyinstaller==6.22.3
    python installers/build_binary.py                  # this machine's target
    python installers/build_binary.py --expect-version 0.5.3

Writes:
    dist/contextzip/                              the app: contextzip[.exe] + _internal/
    dist/release/contextzip-<v>-<target>.tar.gz   every target — npm is assembled from these
    dist/release/contextzip-<v>-<target>.zip      Windows only, for downloading by hand

A one-folder build (not --onefile, which unpacks itself to a temp folder
on every run) keeps startup fast. Target names match Node's
process.platform-process.arch, so npm can pick the right package.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_DIR = DIST / "contextzip"
RELEASE_DIR = DIST / "release"
TARGETS = {t["target"] for t in json.loads((ROOT / "installers" / "targets.json").read_text(encoding="utf-8"))}


def current_target() -> str:
    os_name = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = platform.machine().lower()
    cpu = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return f"{os_name}-{cpu}"


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from contextzip import __version__

    return __version__


def build() -> Path:
    work = ROOT / "build" / "pyinstaller"
    shutil.rmtree(APP_DIR, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "installers" / "entry.py"),
        "--name", "contextzip",
        "--onedir",
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(work),
        "--specpath", str(work),
        "--paths", str(ROOT),
        # Rule sets are imported by name (filters.py), and rich loads some
        # of its tables lazily, so their modules must be collected explicitly.
        "--collect-submodules", "contextzip",
        "--collect-submodules", "rich",
        "--collect-submodules", "pathspec",
        "--exclude-module", "tkinter",
        # UTF-8 mode, as Python 3.15 does by default: output never depends on
        # the console's code page (cp1252 can't print ✓ and would crash).
        "--python-option", "X utf8",
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = APP_DIR / ("contextzip.exe" if os.name == "nt" else "contextzip")
    if not exe.is_file():
        sys.exit(f"build failed: {exe} is missing")
    replaced = materialize_symlinks(APP_DIR)
    if replaced:
        print(f"replaced {replaced} symlink(s) with copies")
    return exe


def materialize_symlinks(app: Path) -> int:
    """
    Replace every symlink in the app with a copy of what it points to.

    The macOS build has a few (PyInstaller's Python.framework layout, and
    `_internal/Python`, which the binary loads at startup). VSIX files can't
    hold symlinks and npm packages may drop them, so the app must work
    without any. Runs before the smoke test, so that's what gets tested.
    """
    replaced = 0
    while True:
        links = [
            Path(dirpath) / name
            for dirpath, dirnames, filenames in os.walk(app)  # doesn't descend into linked folders
            for name in dirnames + filenames
            if os.path.islink(os.path.join(dirpath, name))
        ]
        if not links:
            return replaced
        for link in links:
            target = link.resolve()
            if not target.exists() or not target.is_relative_to(app.resolve()):
                sys.exit(f"build failed: {link} points outside the app ({target})")
            link.unlink()
            if target.is_dir():
                shutil.copytree(target, link, symlinks=True)  # links inside are handled next pass
            else:
                shutil.copy2(target, link)
            replaced += 1


def smoke_test(exe: Path, expected_version: str) -> None:
    """Run the frozen app the way people will: version, help, and a real package + apply."""

    def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([str(exe), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0:
            sys.exit(f"smoke test failed: contextzip {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
        return proc

    out = run("--version").stdout
    if expected_version not in out:
        sys.exit(f"smoke test failed: expected version {expected_version}, got {out.strip()!r}")
    run("--help")
    run("apply-zip", "--help")

    with tempfile.TemporaryDirectory(prefix="cz-smoke-") as tmp:
        project = Path(tmp) / "app"
        (project / "src").mkdir(parents=True)
        (project / "node_modules" / "dep").mkdir(parents=True)
        (project / "package.json").write_text('{"name": "smoke"}\n', encoding="utf-8")
        (project / "src" / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
        (project / "node_modules" / "dep" / "index.js").write_text("x\n", encoding="utf-8")
        (project / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

        data = json.loads(run("--json", "--no-clipboard", cwd=project).stdout)
        included = data["files"]["included_files"]
        if not data["ok"] or "Node.js" not in data["detection"]["ecosystems"] or included != ["package.json", "src/index.js"]:
            sys.exit(f"smoke test failed: unexpected package result {json.dumps(data)[:2000]}")

        reply = Path(tmp) / "reply.zip"
        with zipfile.ZipFile(reply, "w") as zf:
            zf.writestr("index.js", "module.exports = 2;\n")  # flattened on purpose
        data = json.loads(run("apply-zip", str(reply), "--json", "--safe-only", cwd=project).stdout)
        if data["result"]["written"] != ["src/index.js"]:
            sys.exit(f"smoke test failed: unexpected apply result {json.dumps(data)[:2000]}")
    print(f"smoke test passed: {exe}")


def archive(target: str, ver: str) -> list[Path]:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    base = RELEASE_DIR / f"contextzip-{ver}-{target}"
    written = []

    tar_path = base.with_name(base.name + ".tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(APP_DIR, arcname="contextzip")  # keeps the executable bit
    written.append(tar_path)

    if target.startswith("win32-"):
        zip_path = base.with_name(base.name + ".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(APP_DIR.rglob("*")):
                zf.write(f, Path("contextzip") / f.relative_to(APP_DIR))
        written.append(zip_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=None, help="Target name (default: this machine's)")
    parser.add_argument("--expect-version", default=None, help="Fail unless the package version is this (for release builds)")
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()

    target = args.target or current_target()
    if target != current_target():
        sys.exit(f"{target} can only be built on a {target} machine (this one is {current_target()})")
    if target not in TARGETS:
        sys.exit(f"{target} isn't in installers/targets.json")
    ver = version()
    if args.expect_version and args.expect_version.lstrip("v") != ver:
        sys.exit(f"version mismatch: building {ver}, expected {args.expect_version}")

    exe = build()
    smoke_test(exe, ver)
    if not args.skip_archive:
        for path in archive(target, ver):
            print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
