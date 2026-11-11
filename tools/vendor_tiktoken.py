"""
vendor_tiktoken.py — Download the o200k_base vocabulary into the package,
so contextzip counts tokens offline. A maintainer script, not part of the
package. Run it after changing the tiktoken version, then commit the file:

    python tools/vendor_tiktoken.py

tiktoken looks for its vocabulary in TIKTOKEN_CACHE_DIR under a file named
after the SHA-1 of the download URL. Pointing that variable at
contextzip/data/tiktoken/ while loading the encoding once leaves exactly
that file behind; contextzip/tokens.py reads it from there at runtime.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOCAB_DIR = ROOT / "contextzip" / "data" / "tiktoken"
MIN_SIZE = 1024 * 1024


def main() -> int:
    try:
        import tiktoken
    except ImportError:
        print("tiktoken is not installed. Install contextzip into this Python first: pip install -e .", file=sys.stderr)
        return 1

    shutil.rmtree(VOCAB_DIR, ignore_errors=True)
    VOCAB_DIR.mkdir(parents=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(VOCAB_DIR)
    tiktoken.get_encoding("o200k_base")

    files = sorted(p for p in VOCAB_DIR.iterdir() if p.is_file())
    for p in files:
        print(f"{p.relative_to(ROOT).as_posix()}  {p.stat().st_size:,} bytes")

    if len(files) != 1 or files[0].stat().st_size <= MIN_SIZE:
        print(
            f"Expected exactly one file larger than 1 MB in {VOCAB_DIR}, found {len(files)}. "
            "tiktoken may have changed how it caches vocabularies.",
            file=sys.stderr,
        )
        return 1
    print(f"tiktoken {tiktoken.__version__}: vocabulary vendored. Commit {files[0].relative_to(ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
