"""
Clojure / ClojureScript (Leiningen, deps.edn, shadow-cljs) exclusion rules.
"""

PATTERNS = [
    # Build output
    "target/",
    "*.jar",
    "*.class",
    "pom.xml.asc",
    # Tool caches
    ".cpcache/",
    ".lein-*",
    ".nrepl-port",
    ".shadow-cljs/",
    ".clj-kondo/.cache/",
    ".lsp/.cache/",
]
