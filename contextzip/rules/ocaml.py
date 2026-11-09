"""
OCaml (Dune, opam) exclusion rules.
"""

PATTERNS = [
    # Build output and local switch
    "_build/",
    "_opam/",
    "_esy/",
    "esy.lock/",
    "*.install",
    ".merlin",
    # Compiler artifacts
    "*.cmi",
    "*.cmo",
    "*.cmx",
    "*.cma",
    "*.cmxa",
    "*.cmxs",
    "*.cmt",
    "*.cmti",
    "*.annot",
    "*.o",
    "*.a",
]
