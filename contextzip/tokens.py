"""
tokens.py — How many tokens the files in a ZIP come to.

Counts with OpenAI's o200k_base tokenizer through tiktoken. Its vocabulary
ships inside the package (contextzip/data/tiktoken/, written by
tools/vendor_tiktoken.py), so counting never touches the network. Other
vendors' tokenizers differ by some percent, but one real count is a far
better answer to "will this fit?" than bytes divided by four.

When tiktoken or the vocabulary is missing, or loading fails for any reason,
counts fall back to a bytes-per-token ratio per file extension. Results say
which method produced them (TokenReport.method), and estimates are always
shown with "≈".
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

METHOD_EXACT = "o200k_base"
METHOD_ESTIMATE = "estimate"

_BYTES_PER_TOKEN = 4

# The file tiktoken expects in TIKTOKEN_CACHE_DIR (named after the SHA-1 of
# its download URL) and the SHA-256 tiktoken checks it against. On a hash
# mismatch tiktoken deletes the file and downloads it again, so we check
# first and never let it get that far.
_VOCAB_DIR = Path(__file__).parent / "data" / "tiktoken"
_VOCAB_FILE = "fb374d419588a4632f3f557e76b4b70aebbca790"
_VOCAB_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

# Fallback ratios, in tenths of a byte per token so the arithmetic stays exact.
_DEFAULT_TENTHS = 40
_TENTHS_BY_SUFFIX: dict[str, int] = {}
for _tenths, _suffixes in (
    (32, ".json .lock .svg .xml .csv .tsv"),
    (36, ".md .mdx .txt .rst .html .htm .yml .yaml .toml"),
    (45, ".py .rb .go .rs .java .kt .cs .php .swift .dart"),
    (42, ".js .jsx .ts .tsx .mjs .cjs .c .cc .cpp .h .hpp .css .scss"),
):
    for _suffix in _suffixes.split():
        _TENTHS_BY_SUFFIX[_suffix] = _tenths
del _tenths, _suffixes, _suffix

_PARALLEL_THRESHOLD = 32
_CACHE_FILE = "tokens.json"

_load_lock = threading.Lock()
_loaded = False
_encoding_obj = None
_available: bool | None = None


# ---------------------------------------------------------------------------
# Formatting and the plain estimate
# ---------------------------------------------------------------------------


def estimate_tokens(text_bytes: int) -> int:
    """Estimated tokens for *text_bytes* bytes of text (binary files excluded by the caller)."""
    return (max(text_bytes, 0) + _BYTES_PER_TOKEN - 1) // _BYTES_PER_TOKEN


def format_tokens(tokens: int, *, exact: bool = False) -> str:
    """
    exact=False: '≈ 850 tokens', '≈ 4.2k tokens', '≈ 46k tokens', '≈ 1.3M tokens'.
    exact=True: '46,210 tokens', '1 token'.
    """
    if exact:
        return f"{tokens:,} token" + ("" if tokens == 1 else "s")
    if tokens < 1000:
        n = str(tokens)
    elif tokens < 10_000:
        n = f"{tokens / 1000:.1f}k"
    elif tokens < 999_500:
        n = f"{round(tokens / 1000)}k"
    else:
        n = f"{tokens / 1_000_000:.1f}M"
    return f"≈ {n} tokens"


# ---------------------------------------------------------------------------
# Loading the tokenizer
# ---------------------------------------------------------------------------


def _vocab_path() -> Path:
    return _VOCAB_DIR / _VOCAB_FILE


def tokenizer_available() -> bool:
    """
    True if tiktoken imports and the vendored vocabulary is present.
    Cached after the first call. Never raises and never touches the network.
    """
    global _available
    if _available is None:
        try:
            if not _vocab_path().is_file():
                _available = False
            else:
                import tiktoken  # noqa: F401

                _available = True
        except Exception:
            _available = False
    return _available


def _vocab_intact() -> bool:
    try:
        return hashlib.sha256(_vocab_path().read_bytes()).hexdigest() == _VOCAB_SHA256
    except OSError:
        return False


def _encoding():
    """The o200k_base encoding, loaded once per process; None if it can't be loaded offline."""
    global _loaded, _encoding_obj
    if _loaded:
        return _encoding_obj
    with _load_lock:
        if _loaded:
            return _encoding_obj
        enc = None
        try:
            # Without the vocabulary tiktoken would download it: never call it then.
            if tokenizer_available() and _vocab_intact():
                import tiktoken

                previous = os.environ.get("TIKTOKEN_CACHE_DIR")
                os.environ["TIKTOKEN_CACHE_DIR"] = str(_VOCAB_DIR)
                try:
                    enc = tiktoken.get_encoding("o200k_base")
                finally:
                    if previous is None:
                        os.environ.pop("TIKTOKEN_CACHE_DIR", None)
                    else:
                        os.environ["TIKTOKEN_CACHE_DIR"] = previous
        except Exception:
            enc = None
        _encoding_obj = enc
        _loaded = True
        return enc


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def _estimate_for(byte_count: int, suffix: str) -> int:
    tenths = _TENTHS_BY_SUFFIX.get(suffix.lower(), _DEFAULT_TENTHS)
    return (max(byte_count, 0) * 10 + tenths - 1) // tenths


def count_text(text: str) -> int:
    """Tokens in *text*: exact with the tokenizer, otherwise about four bytes per token."""
    enc = _encoding() if tokenizer_available() else None
    if enc is not None:
        try:
            return len(enc.encode_ordinary(text))
        except Exception:
            pass
    return _estimate_for(len(text.encode("utf-8", errors="replace")), "")


@dataclass
class TokenReport:
    total: int
    method: str
    """METHOD_EXACT or METHOD_ESTIMATE."""
    by_file: dict[str, int] = field(default_factory=dict)
    """Project-relative POSIX path -> tokens, in the order the files were given."""

    @property
    def exact(self) -> bool:
        return self.method == METHOD_EXACT

    def by_dir(self, depth: int = 1) -> list[tuple[str, int]]:
        """
        Tokens grouped by the first *depth* folders of each path ("src/",
        "src/app/"); files in the project root go under "(root)". Largest
        first, ties by name.
        """
        depth = max(depth, 1)
        groups: dict[str, int] = {}
        for rel, n in self.by_file.items():
            folders = rel.split("/")[:-1]
            key = "/".join(folders[:depth]) + "/" if folders else "(root)"
            groups[key] = groups.get(key, 0) + n
        return sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))

    def top_files(self, n: int = 10) -> list[tuple[str, int]]:
        """The *n* files with the most tokens, largest first, ties by name."""
        return sorted(self.by_file.items(), key=lambda kv: (-kv[1], kv[0]))[: max(n, 0)]

    def label(self) -> str:
        return format_tokens(self.total, exact=self.exact)

    def subset(self, keys: Iterable[str]) -> TokenReport:
        """The same counts for only *keys* (in this report's order), without tokenizing again."""
        wanted = set(keys)
        by_file = {k: n for k, n in self.by_file.items() if k in wanted}
        return TokenReport(total=sum(by_file.values()), method=self.method, by_file=by_file)


def _rel_key(path: Path, project_dir: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return path.name


def _load_cache(cache_dir: Path | None, method: str) -> dict[str, list[int]]:
    if cache_dir is None:
        return {}
    try:
        data = json.loads((cache_dir / _CACHE_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("method") != method or not isinstance(data.get("files"), dict):
            return {}
        entries: dict[str, list[int]] = {}
        for rel, entry in data["files"].items():
            if (
                isinstance(rel, str)
                and isinstance(entry, list)
                and len(entry) == 3
                and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in entry)
            ):
                entries[rel] = entry
        return entries
    except Exception:
        return {}


def _save_cache(cache_dir: Path, method: str, entries: dict[str, list[int]]) -> None:
    tmp = cache_dir / f"{_CACHE_FILE}.{os.getpid()}.tmp"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"method": method, "files": entries}, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, cache_dir / _CACHE_FILE)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass


def count_files(
    files: Iterable[Path],
    project_dir: Path,
    *,
    binary: Iterable[Path] = (),
    cache_dir: Path | None = None,
) -> TokenReport:
    """
    Count the tokens in *files* (absolute paths). Files in *binary* count 0
    and stay in the report. A file that can't be read counts 0. A path
    outside *project_dir* is keyed by its file name.

    With *cache_dir*, counts are kept in cache_dir/tokens.json and reused
    while a file's size and modification time are unchanged.
    """
    paths = list(files)
    binary_set = frozenset(binary)
    enc = _encoding() if tokenizer_available() else None
    method = METHOD_EXACT if enc is not None else METHOD_ESTIMATE

    cache = _load_cache(cache_dir, method)
    new_cache: dict[str, list[int]] = {}

    keys = [_rel_key(p, project_dir) for p in paths]
    results: list[int | None] = [None] * len(paths)
    stats: list[tuple[int, int] | None] = [None] * len(paths)
    todo: list[int] = []

    for i, path in enumerate(paths):
        if path in binary_set:
            results[i] = 0
            continue
        try:
            st = path.stat()
        except OSError:
            results[i] = 0
            continue
        stats[i] = (st.st_size, st.st_mtime_ns)
        entry = cache.get(keys[i])
        if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
            results[i] = entry[2]
        else:
            todo.append(i)

    def count_one(i: int) -> int | None:
        path = paths[i]
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if enc is None:
            return _estimate_for(len(data), path.suffix)
        return count_text(data.decode("utf-8", errors="replace"))

    counted: list[int | None] | None = None
    # tiktoken releases the GIL, so threads help. Pyodide can't start them.
    if len(todo) > _PARALLEL_THRESHOLD and sys.platform != "emscripten":
        from concurrent.futures import ThreadPoolExecutor

        try:
            with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
                counted = list(pool.map(count_one, todo))
        except RuntimeError:  # "can't start new thread"
            counted = None
    if counted is None:
        counted = [count_one(i) for i in todo]

    for i, n in zip(todo, counted):
        if n is None:
            stats[i] = None  # unreadable now; don't remember a 0 for it
        results[i] = n or 0

    # Two files outside the project can share a name: they add up in the
    # report, but neither is cached under the shared key.
    seen: set[str] = set()
    shared: set[str] = set()
    for key in keys:
        if key in seen:
            shared.add(key)
        seen.add(key)

    by_file: dict[str, int] = {}
    for i, key in enumerate(keys):
        n = results[i] or 0
        by_file[key] = by_file.get(key, 0) + n
        st_pair = stats[i]
        if st_pair is not None and key not in shared:
            new_cache[key] = [st_pair[0], st_pair[1], n]

    if cache_dir is not None and new_cache != cache:
        _save_cache(cache_dir, method, new_cache)

    return TokenReport(total=sum(by_file.values()), method=method, by_file=by_file)
