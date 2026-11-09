"""
stacks.py — Every stack contextzip knows, in one table.

A :class:`Stack` says how to recognise an ecosystem or framework (marker
files, dependency names, or just file types) and what to leave out of the
ZIP for it. detector.py reads the table to detect; filters.py reads it to
build exclusion rules; `contextzip stacks` and the website print it.

Adding a stack is one entry here, plus a case in tests/test_stacks.py:

    Stack(
        "Elixir",
        rules="elixir",                # contextzip/rules/elixir.py, a PATTERNS list
        markers=("mix.exs",),          # a file or folder that marks the project
        extensions=(".ex", ".exs"),    # recognised by file type when no marker exists
    ),

A framework on top of an ecosystem shares its rule set and adds its own
patterns with `extra`:

    Stack("Phoenix", rules="elixir", deps=(":phoenix",), extra=("priv/static/assets/",)),

Order matters twice: it's the order names are shown in ("Next.js + Node.js"),
so a framework goes before the ecosystem it's built on.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Stack:
    name: str
    # Shared rule set: a PATTERNS list in contextzip/rules/<rules>.py.
    rules: str | None = None
    # File or folder names that mark a project folder. A glob ("*.csproj")
    # matches any entry in the folder; a path ("app/build.gradle") must exist.
    markers: tuple[str, ...] = ()
    # Dependency names. In package.json / composer.json these are exact
    # package names (a trailing * matches a prefix); in every other manifest
    # the name only has to appear in the text.
    deps: tuple[str, ...] = ()
    # Where `deps` are looked up, if not the rule set's usual manifests.
    manifests: tuple[str, ...] = ()
    # Exclusions only this stack needs, on top of its rule set. A path
    # applies from the folder the stack was found in, so "/public/" means
    # that folder's public/, not every public/ in a monorepo. A bare name
    # ("*.apk", ".expo/") applies to the whole project.
    extra: tuple[str, ...] = ()
    # Stacks not worth naming once this one is found in the same folder (Nuxt
    # implies Vue), or in one of its platform folders (a Flutter app's ios/).
    hides: tuple[str, ...] = ()
    # File types that give the stack away in a project with no marker file.
    extensions: tuple[str, ...] = ()
    # How much one match says about the project (feeds the confidence label).
    weight: int = 3

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower().replace("#", "sharp").replace("+", "p")).strip("-")

    @property
    def extra_key(self) -> str | None:
        """The rule key under which this stack's own patterns are registered."""
        return f"{self.slug}-extra" if self.extra else None


# Manifests a rule set's dependency names are looked up in.
MANIFESTS: dict[str, tuple[str, ...]] = {
    "node": ("package.json",),
    "deno": ("deno.json", "deno.jsonc", "package.json"),
    "python": ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg"),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "php": ("composer.json",),
    "ruby": ("Gemfile",),
    "dart": ("pubspec.yaml",),
    "elixir": ("mix.exs",),
    "rust": ("Cargo.toml",),
    "go": ("go.mod",),
    "dotnet": ("*.csproj", "*.fsproj", "*.vbproj"),
    "scala": ("build.sbt", "build.sc"),
}

# Manifests read as JSON, where a dependency is a key and not just a word.
_JSON_MANIFESTS = {"package.json", "composer.json"}


# Native projects a cross-platform app scaffolds for itself.
_NATIVE = ("Swift", "Java", "Kotlin", "Android")

# Folders those scaffolded projects live in.
PLATFORM_DIRS = frozenset({"ios", "android", "macos", "linux", "windows", "web"})


STACKS: tuple[Stack, ...] = (
    # ── JavaScript / TypeScript ───────────────────────────────────────────
    Stack("Next.js", rules="node", markers=("next.config.js", "next.config.ts", "next.config.mjs", "next.config.cjs"), deps=("next",)),
    Stack("Nuxt", rules="node", markers=("nuxt.config.js", "nuxt.config.ts", "nuxt.config.mjs"), deps=("nuxt", "nuxt3"), extra=(".data/", ".nitro/"), hides=("Vue", "Vite")),
    Stack("SvelteKit", rules="node", deps=("@sveltejs/kit",), hides=("Svelte", "Vite")),
    Stack("Astro", rules="node", markers=("astro.config.mjs", "astro.config.ts", "astro.config.js"), deps=("astro",), hides=("Vite",)),
    Stack("Remix", rules="node", deps=("@remix-run/react", "@remix-run/node", "@remix-run/dev"), extra=("/public/build/",), hides=("Vite",)),
    Stack("Gatsby", rules="node", markers=("gatsby-config.js", "gatsby-config.ts", "gatsby-config.mjs"), deps=("gatsby",), extra=("/public/",)),
    Stack("Angular", rules="node", markers=("angular.json",), deps=("@angular/core",)),
    Stack("Expo", rules="node", deps=("expo",), extra=(".expo/", ".expo-shared/", "web-build/", "expo-env.d.ts"), hides=_NATIVE),
    Stack(
        "React Native",
        rules="node",
        deps=("react-native",),
        extra=(
            "ios/Pods/", "ios/build/", "ios/Podfile.lock", "*.pbxproj", "*.xcassets/", "xcuserdata/",
            "android/build/", "android/app/build/", "android/.gradle/", "android/gradlew", "android/gradlew.bat",
            "android/gradle/wrapper/", "android/local.properties", "*.jsbundle", ".bundle/", "vendor/bundle/",
        ),
        hides=_NATIVE,
    ),
    Stack("Electron", rules="node", deps=("electron",), extra=("/release/", "dist-electron/", ".webpack/")),
    Stack("Tauri", rules="node", markers=("src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"), deps=("@tauri-apps/api", "@tauri-apps/cli"), extra=("src-tauri/target/", "src-tauri/gen/", "src-tauri/Cargo.lock", "src-tauri/icons/")),
    Stack("NestJS", rules="node", markers=("nest-cli.json",), deps=("@nestjs/core",)),
    Stack("Vue", rules="node", markers=("vue.config.js",), deps=("vue",)),
    Stack("Svelte", rules="node", markers=("svelte.config.js", "svelte.config.ts"), deps=("svelte",)),
    Stack("Vite", rules="node", markers=("vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.mts"), deps=("vite",), weight=2),
    Stack("Bun", rules="node", markers=("bun.lockb", "bun.lock", "bunfig.toml"), weight=2),
    Stack("Node.js", rules="node", markers=("package.json",), extensions=(".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"), weight=2),
    Stack("Deno", rules="deno", markers=("deno.json", "deno.jsonc", "deno.lock")),
    # ── Python ────────────────────────────────────────────────────────────
    Stack("Django", rules="python", markers=("manage.py",), deps=("django",), manifests=("requirements.txt", "pyproject.toml")),
    Stack("FastAPI", rules="python", deps=("fastapi",), manifests=("requirements.txt", "pyproject.toml")),
    Stack("Flask", rules="python", deps=("flask",), extra=("/instance/",)),
    Stack("Jupyter", rules="jupyter", extensions=(".ipynb",), weight=2),
    Stack("Python", rules="python", markers=("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"), extensions=(".py",), weight=2),
    # ── Systems ───────────────────────────────────────────────────────────
    Stack("Rust", rules="rust", markers=("Cargo.toml",), extensions=(".rs",)),
    Stack("Go", rules="go", markers=("go.mod",), extensions=(".go",)),
    Stack("C/C++", rules="cpp", markers=("CMakeLists.txt", "meson.build", "configure.ac", "conanfile.txt", "conanfile.py", "vcpkg.json", "*.vcxproj"), extensions=(".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")),
    Stack("Bazel", rules="bazel", markers=("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel"), weight=2),
    Stack("Zig", rules="zig", markers=("build.zig",), extensions=(".zig",)),
    Stack("Nim", rules="nim", markers=("*.nimble", "nim.cfg", "config.nims"), extensions=(".nim",)),
    # ── Ruby ──────────────────────────────────────────────────────────────
    Stack("Rails", rules="ruby", markers=("bin/rails", "config/application.rb"), deps=("rails",)),
    Stack("Jekyll", rules="ruby", deps=("jekyll",), extra=("_site/", ".jekyll-cache/", ".jekyll-metadata", ".sass-cache/")),
    Stack("Ruby", rules="ruby", markers=("Gemfile",), extensions=(".rb",), weight=2),
    # ── JVM ───────────────────────────────────────────────────────────────
    Stack("Spring Boot", rules="java", deps=("spring-boot", "org.springframework.boot")),
    Stack(
        "Android",
        rules="java",
        deps=("com.android.application", "com.android.library", "com.android.tools.build"),
        manifests=("build.gradle", "build.gradle.kts"),
        extra=("local.properties", "*.apk", "*.aab", "*.ap_", "*.dex", "*.hprof", "/captures/", ".cxx/", ".externalNativeBuild/", "google-services.json", "lint-results*"),
    ),
    Stack("Java", rules="java", markers=("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), extensions=(".java",)),
    Stack("Kotlin", rules="java", deps=("kotlin",), extensions=(".kt", ".kts"), weight=2),
    Stack("Scala", rules="scala", markers=("build.sbt", "build.sc", "project/build.properties"), extensions=(".scala", ".sc")),
    Stack("Clojure", rules="clojure", markers=("project.clj", "deps.edn", "shadow-cljs.edn", "bb.edn"), extensions=(".clj", ".cljs", ".cljc")),
    # ── .NET ──────────────────────────────────────────────────────────────
    # Unity writes its own .sln and .csproj files; they aren't a .NET project to describe.
    Stack("Unity", rules="unity", markers=("ProjectSettings/ProjectVersion.txt",), hides=("C#/.NET",)),
    Stack(
        "C#/.NET",
        rules="dotnet",
        markers=("*.csproj", "*.fsproj", "*.vbproj", "global.json", "Directory.Build.props", "Directory.Packages.props"),
        # A .sln only counts when it lists .NET projects: C++ solutions use the same file.
        deps=(".csproj", ".fsproj", ".vbproj"),
        manifests=("*.sln", "*.slnx"),
        extensions=(".cs", ".fs", ".vb", ".razor", ".cshtml"),
    ),
    # ── PHP ───────────────────────────────────────────────────────────────
    Stack("Laravel", rules="php", markers=("artisan",), deps=("laravel/framework",), extra=("storage/framework/", "storage/logs/", "storage/app/", "bootstrap/cache/", "public/build/", "public/hot", "public/storage", "public/vendor/")),
    Stack("Symfony", rules="php", markers=("symfony.lock", "bin/console"), deps=("symfony/framework-bundle",), extra=("/var/", "public/bundles/", "symfony.lock")),
    Stack(
        "WordPress",
        rules="php",
        markers=("wp-config.php", "wp-config-sample.php", "wp-load.php"),
        # WordPress core is the same in every install, and wp-config.php holds the database password.
        extra=("wp-admin/", "wp-includes/", "wp-content/uploads/", "wp-content/upgrade/", "wp-content/cache/", "wp-config.php"),
    ),
    Stack("PHP", rules="php", markers=("composer.json",), extensions=(".php",), weight=2),
    # ── Mobile ────────────────────────────────────────────────────────────
    # A Flutter app carries ios/, android/, linux/ and windows/ runner projects; the Dart rules cover them.
    Stack("Flutter", rules="dart", deps=("sdk: flutter",), hides=(*_NATIVE, "C/C++")),
    Stack("Dart", rules="dart", markers=("pubspec.yaml",), extensions=(".dart",), weight=2),
    Stack("Swift", rules="swift", markers=("Package.swift", "*.xcodeproj", "*.xcworkspace", "Podfile", "Cartfile"), extensions=(".swift",)),
    # ── BEAM ──────────────────────────────────────────────────────────────
    Stack("Phoenix", rules="elixir", deps=(":phoenix",)),
    Stack("Elixir", rules="elixir", markers=("mix.exs",), extensions=(".ex", ".exs"), weight=2),
    Stack("Erlang", rules="erlang", markers=("rebar.config", "erlang.mk"), extensions=(".erl", ".hrl")),
    # ── Functional and scientific ─────────────────────────────────────────
    Stack("Haskell", rules="haskell", markers=("stack.yaml", "cabal.project", "*.cabal"), extensions=(".hs",)),
    Stack("OCaml", rules="ocaml", markers=("dune-project", "*.opam"), extensions=(".ml", ".mli")),
    Stack("R", rules="r", markers=("*.Rproj", "renv.lock", ".Rprofile"), extensions=(".r", ".rmd")),
    Stack("Julia", rules="julia", markers=("Project.toml", "JuliaProject.toml"), extensions=(".jl",)),
    Stack("Lua", rules="lua", markers=("*.rockspec", ".luarc.json"), extensions=(".lua",), weight=2),
    Stack("Perl", rules="perl", markers=("cpanfile", "Makefile.PL", "Build.PL", "dist.ini"), extensions=(".pl", ".pm")),
    # ── Smart contracts ───────────────────────────────────────────────────
    Stack("Foundry", markers=("foundry.toml",), extra=("/out/", "/cache/", "/broadcast/", "/lib/")),
    Stack("Hardhat", markers=("hardhat.config.js", "hardhat.config.ts", "hardhat.config.cjs", "hardhat.config.mjs"), extra=("/artifacts/", "/cache/", "/typechain/", "/typechain-types/", "/coverage.json")),
    # ── Game engines ──────────────────────────────────────────────────────
    Stack("Godot", rules="godot", markers=("project.godot",), extensions=(".gd",)),
    Stack("Unreal Engine", rules="unreal", markers=("*.uproject",), hides=("C#/.NET",)),
    # ── Infrastructure and sites ──────────────────────────────────────────
    Stack("Terraform", rules="terraform", markers=("*.tf", "terragrunt.hcl", ".terraform.lock.hcl"), extensions=(".tf",)),
    Stack("Hugo", markers=("hugo.toml", "hugo.yaml", "hugo.yml", "hugo.json"), extra=("/public/", "resources/_gen/", ".hugo_build.lock")),
)


BY_NAME: dict[str, Stack] = {s.name: s for s in STACKS}

# Rule keys that are a PATTERNS list in contextzip/rules/<key>.py.
RULE_MODULES: frozenset[str] = frozenset({"base", *(s.rules for s in STACKS if s.rules)})

_EXTRA: dict[str, tuple[str, ...]] = {s.extra_key: s.extra for s in STACKS if s.extra_key}


def rule_keys(stack: Stack) -> list[str]:
    """The rule keys a detected *stack* switches on: its rule set, then its own patterns."""
    keys = [stack.rules] if stack.rules else []
    if stack.extra_key:
        keys.append(stack.extra_key)
    return keys


def patterns_for(key: str) -> list[str]:
    """
    Exclusion patterns registered under a rule key ([] for an unknown key).
    A key may carry the folder it applies from ("laravel-extra@apps/api");
    filters.build_spec does the rebasing.
    """
    key = key.partition("@")[0]
    if key in RULE_MODULES:
        try:
            module = importlib.import_module(f"contextzip.rules.{key}")
        except ImportError:
            return []
        return list(getattr(module, "PATTERNS", []))
    return list(_EXTRA.get(key, ()))


def manifests_for(stack: Stack) -> tuple[str, ...]:
    """Files a stack's dependency names are looked up in."""
    return stack.manifests or MANIFESTS.get(stack.rules or "", ())


def is_json_manifest(name: str) -> bool:
    return name in _JSON_MANIFESTS


def describe(stack: Stack) -> dict:
    """One stack as plain data, for `contextzip stacks --json` and the website."""
    patterns = []
    for key in rule_keys(stack):
        patterns.extend(patterns_for(key))
    return {
        "name": stack.name,
        "slug": stack.slug,
        "rules": stack.rules,
        "markers": list(stack.markers),
        "dependencies": list(stack.deps),
        "manifests": list(manifests_for(stack)) if stack.deps else [],
        "extensions": list(stack.extensions),
        "excludes": patterns,
    }
