"""
manifests.py — The Homebrew formula and Scoop manifest for one release.

    python installers/manifests.py --version 0.5.3 --checksums dist/release/SHA256SUMS --out dist/manifests

Writes contextzip.rb (for the akadeepesh/homebrew-tap repo, Formula/) and
contextzip.json (for the akadeepesh/scoop-bucket repo, bucket/). Both
download the release archives built by build_binary.py, checked against
SHA256SUMS (`sha256sum` output: "<hash>  <file name>" per line).

The launchers are scripts that call the real executable rather than
symlinks to it, so a one-folder build always finds its _internal/ folder.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = "akadeepesh/contextzip"
HOMEPAGE = "https://www.contextzipcli.com"
DESCRIPTION = "Package exactly the right parts of your codebase for any AI tool"


def asset_url(version: str, filename: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{version}/{filename}"


def read_checksums(path: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            digest, name = parts
            sums[name.lstrip("*")] = digest.lower()
    return sums


def _asset(sums: dict[str, str], version: str, target: str, ext: str) -> tuple[str, str]:
    name = f"contextzip-{version}-{target}.{ext}"
    if name not in sums:
        raise SystemExit(f"{name} isn't in the checksums file")
    return asset_url(version, name), sums[name]


def homebrew_formula(version: str, sums: dict[str, str]) -> str:
    def block(target: str) -> str:
        url, sha = _asset(sums, version, target, "tar.gz")
        return f'      url "{url}"\n      sha256 "{sha}"'

    return f'''class Contextzip < Formula
  desc "{DESCRIPTION}"
  homepage "{HOMEPAGE}"
  version "{version}"
  license "MIT"

  on_macos do
    on_arm do
{block("darwin-arm64")}
    end
    on_intel do
{block("darwin-x64")}
    end
  end

  on_linux do
    on_arm do
{block("linux-arm64")}
    end
    on_intel do
{block("linux-x64")}
    end
  end

  def install
    libexec.install Dir["*"]
    (bin/"contextzip").write_exec_script libexec/"contextzip"
    (bin/"cz").write_exec_script libexec/"contextzip"
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/cz --version")
  end
end
'''


def scoop_manifest(version: str, sums: dict[str, str]) -> dict:
    url, sha = _asset(sums, version, "win32-x64", "zip")
    return {
        "version": version,
        "description": DESCRIPTION,
        "homepage": HOMEPAGE,
        "license": "MIT",
        "architecture": {"64bit": {"url": url, "hash": sha}},
        "extract_dir": "contextzip",
        "bin": ["contextzip.exe", ["contextzip.exe", "cz"]],
        "checkver": {"github": f"https://github.com/{REPO}"},
        "autoupdate": {
            "architecture": {
                "64bit": {"url": asset_url("$version", "contextzip-$version-win32-x64.zip")}
            },
            "hash": {"url": f"https://github.com/{REPO}/releases/download/v$version/SHA256SUMS"},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    version = args.version.lstrip("v")
    sums = read_checksums(args.checksums)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "contextzip.rb").write_text(homebrew_formula(version, sums), encoding="utf-8")
    (args.out / "contextzip.json").write_text(json.dumps(scoop_manifest(version, sums), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out / 'contextzip.rb'} and {args.out / 'contextzip.json'}")


if __name__ == "__main__":
    main()
