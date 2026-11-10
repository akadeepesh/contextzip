"""
One small project per stack: what is detected, and which files reach the ZIP.

To cover a new stack, add a Case: the files of a minimal project (a path, or
a path and its content), the names detection should report, and the files
that should be included. Everything else in the project must be left out.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from contextzip import stacks
from contextzip.detector import detect
from contextzip.filters import build_spec, resolve_files
from tests.helpers import TempProject


@dataclass
class Case:
    name: str
    files: list
    detected: list[str]
    included: list[str]
    gitignore: str | None = None


def pkg(**deps) -> str:
    return json.dumps({"name": "app", "dependencies": deps})


CASES = [
    # ── JavaScript / TypeScript ───────────────────────────────────────────
    Case(
        "nextjs",
        [("package.json", pkg(next="16", react="19")), "next.config.ts", "app/page.tsx", ".next/server/app.js", "node_modules/next/index.js", "package-lock.json", "next-env.d.ts", "public/logo.png"],
        ["Next.js", "Node.js"],
        ["app/page.tsx", "next.config.ts", "package.json"],
    ),
    Case(
        "nuxt hides vue and vite",
        [("package.json", pkg(nuxt="4", vue="3", vite="6")), "nuxt.config.ts", "pages/index.vue", ".nuxt/types.d.ts", ".output/server/index.mjs", ".data/kv/x.json"],
        ["Nuxt", "Node.js"],
        ["nuxt.config.ts", "package.json", "pages/index.vue"],
    ),
    Case(
        "sveltekit",
        [("package.json", pkg(**{"@sveltejs/kit": "2", "svelte": "5", "vite": "6"})), "svelte.config.js", "vite.config.ts", "src/routes/+page.svelte", ".svelte-kit/generated/root.js"],
        ["SvelteKit", "Node.js"],
        ["package.json", "src/routes/+page.svelte", "svelte.config.js", "vite.config.ts"],
    ),
    Case(
        "vite react",
        [("package.json", pkg(react="19", vite="6")), "vite.config.ts", "src/main.tsx", "dist/assets/index.js", "bun.lockb", "tsconfig.app.tsbuildinfo"],
        ["Vite", "Bun", "Node.js"],
        ["package.json", "src/main.tsx", "vite.config.ts"],
    ),
    Case(
        "angular",
        [("package.json", pkg(**{"@angular/core": "20"})), "angular.json", "src/app/app.component.ts", ".angular/cache/x.json", "dist/app/main.js"],
        ["Angular", "Node.js"],
        ["angular.json", "package.json", "src/app/app.component.ts"],
    ),
    Case(
        "gatsby leaves out its own public folder only",
        [("site/package.json", pkg(gatsby="5")), "site/gatsby-config.js", "site/src/pages/index.js", "site/public/index.html", ("api/package.json", pkg(express="5")), "api/public/robots.txt", "api/server.js"],
        ["Node.js", "Gatsby"],
        ["api/package.json", "api/public/robots.txt", "api/server.js", "site/gatsby-config.js", "site/package.json", "site/src/pages/index.js"],
    ),
    Case(
        "expo",
        [("package.json", pkg(expo="54", **{"react-native": "0.82"})), "App.tsx", ".expo/devices.json", "ios/Pods/Manifest.lock", "ios/App.xcodeproj/project.pbxproj", "ios/App/AppDelegate.swift", "android/app/build/outputs/x.txt", ("android/build.gradle", "classpath 'com.android.tools.build:gradle:8.0'"), "android/app/src/main/java/com/app/MainActivity.kt"],
        ["Expo", "React Native", "Node.js"],
        ["App.tsx", "android/app/src/main/java/com/app/MainActivity.kt", "android/build.gradle", "ios/App/AppDelegate.swift", "package.json"],
    ),
    Case(
        "tauri",
        [("package.json", pkg(**{"@tauri-apps/api": "2"})), "src/main.ts", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml", "src-tauri/src/main.rs", "src-tauri/target/debug/app", "src-tauri/Cargo.lock"],
        ["Tauri", "Node.js", "Rust"],
        ["package.json", "src-tauri/Cargo.toml", "src-tauri/src/main.rs", "src-tauri/tauri.conf.json", "src/main.ts"],
    ),
    Case(
        "deno",
        ["deno.json", "main.ts", "deno.lock", "vendor/x/mod.ts"],
        ["Deno"],
        ["deno.json", "main.ts"],
    ),
    # ── Python ────────────────────────────────────────────────────────────
    Case(
        "django",
        [("requirements.txt", "Django==5.2\n"), "manage.py", "shop/models.py", "shop/__pycache__/models.cpython-312.pyc", ".venv/pyvenv.cfg", "db.sqlite3", "staticfiles/admin/base.css"],
        ["Django", "Python"],
        ["manage.py", "requirements.txt", "shop/models.py"],
    ),
    Case(
        "fastapi",
        [("pyproject.toml", '[project]\ndependencies = ["fastapi>=0.115"]\n'), "app/main.py", "uv.lock", ".ruff_cache/x"],
        ["FastAPI", "Python"],
        ["app/main.py", "pyproject.toml"],
    ),
    Case(
        "flask",
        [("requirements.txt", "flask\n"), "app.py", "instance/config.py", "templates/index.html"],
        ["Flask", "Python"],
        ["app.py", "requirements.txt", "templates/index.html"],
    ),
    Case(
        "python machine learning",
        [("requirements.txt", "torch\n"), "train.py", "notebooks/explore.ipynb", "notebooks/.ipynb_checkpoints/explore-checkpoint.ipynb", "checkpoints/model.safetensors", "weights.pt", "wandb/run-1/log.txt"],
        ["Python"],
        ["notebooks/explore.ipynb", "requirements.txt", "train.py"],
    ),
    # ── Systems ───────────────────────────────────────────────────────────
    Case("rust", ["Cargo.toml", "src/main.rs", "target/debug/app", "Cargo.lock"], ["Rust"], ["Cargo.toml", "src/main.rs"]),
    Case("go", ["go.mod", "main.go", "go.sum", "vendor/x/y.go", "coverage.out"], ["Go"], ["go.mod", "main.go"]),
    Case(
        "cmake",
        ["CMakeLists.txt", "src/main.cpp", "include/app.h", "build/CMakeCache.txt", "cmake-build-debug/app", "compile_commands.json", "src/main.o"],
        ["C/C++"],
        ["CMakeLists.txt", "include/app.h", "src/main.cpp"],
    ),
    Case(
        "visual studio c++ solution is not .net",
        [("app.sln", 'Project("{8BC9CEB8}") = "app", "app.vcxproj", "{1}"\n'), "app.vcxproj", "main.cpp", "x64/Debug/app.exe", ".vs/app/v17/Browse.VC.db"],
        ["C/C++"],
        ["app.sln", "app.vcxproj", "main.cpp"],
    ),
    Case("zig", ["build.zig", "src/main.zig", "zig-out/bin/app", ".zig-cache/h/x"], ["Zig"], ["build.zig", "src/main.zig"]),
    # ── Ruby ──────────────────────────────────────────────────────────────
    Case(
        "rails",
        [("Gemfile", 'gem "rails", "~> 8.0"\n'), "bin/rails", "config/application.rb", "app/models/user.rb", "Gemfile.lock", "storage/development.sqlite3", "public/assets/app-abc.js", "vendor/bundle/x.rb"],
        ["Rails", "Ruby"],
        ["Gemfile", "app/models/user.rb", "bin/rails", "config/application.rb"],
    ),
    # ── JVM ───────────────────────────────────────────────────────────────
    Case(
        "spring boot gradle",
        [("build.gradle", "plugins { id 'org.springframework.boot' version '3.5.0' }\n"), "src/main/java/app/App.java", "build/libs/app.jar", ".gradle/8/x.bin", "gradlew"],
        ["Spring Boot", "Java"],
        ["build.gradle", "src/main/java/app/App.java"],
    ),
    Case(
        "android",
        ["settings.gradle.kts", ("app/build.gradle.kts", 'plugins { id("com.android.application"); kotlin("android") }\n'), "app/src/main/AndroidManifest.xml", "app/src/main/java/app/MainActivity.kt", "app/build/outputs/apk/app.apk", "local.properties", "app/google-services.json"],
        ["Java", "Android", "Kotlin"],
        ["app/build.gradle.kts", "app/src/main/AndroidManifest.xml", "app/src/main/java/app/MainActivity.kt", "settings.gradle.kts"],
    ),
    Case("scala", ["build.sbt", "project/build.properties", "src/main/scala/App.scala", "target/scala-3/App.class", "project/target/x", ".bsp/sbt.json"], ["Scala"], ["build.sbt", "project/build.properties", "src/main/scala/App.scala"]),
    Case("clojure", ["deps.edn", "src/app/core.clj", ".cpcache/x.cp", "target/app.jar"], ["Clojure"], ["deps.edn", "src/app/core.clj"]),
    # ── .NET ──────────────────────────────────────────────────────────────
    Case(
        "dotnet",
        [("App.sln", 'Project("{FAE04EC0}") = "Api", "src\\Api\\Api.csproj", "{1}"\n'), "src/Api/Api.csproj", "src/Api/Program.cs", "src/Api/bin/Debug/net9.0/Api.dll", "src/Api/obj/project.assets.json", "src/Api/appsettings.json", ".vs/App/v17/.suo", "src/Api/Api.csproj.user"],
        ["C#/.NET"],
        ["App.sln", "src/Api/Api.csproj", "src/Api/Program.cs", "src/Api/appsettings.json"],
    ),
    Case(
        "unity hides its generated .net project",
        ["ProjectSettings/ProjectVersion.txt", "Assets/Scripts/Player.cs", "Assets/Scripts/Player.cs.meta", "Assets/Scenes/Main.unity", "Library/ArtifactDB", "Assembly-CSharp.csproj", "Game.sln", "Packages/manifest.json"],
        ["Unity"],
        ["Assets/Scripts/Player.cs", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"],
    ),
    # ── PHP ───────────────────────────────────────────────────────────────
    Case(
        "laravel",
        [("composer.json", json.dumps({"require": {"laravel/framework": "^12.0"}})), "artisan", "app/Models/User.php", "routes/web.php", "vendor/autoload.php", "composer.lock", "storage/logs/laravel.txt", "storage/framework/views/x.php", "bootstrap/cache/services.php", "public/build/manifest.json", "public/index.php"],
        ["Laravel", "PHP"],
        ["app/Models/User.php", "artisan", "composer.json", "public/index.php", "routes/web.php"],
    ),
    Case(
        "symfony",
        [("composer.json", json.dumps({"require": {"symfony/framework-bundle": "7.3.*"}})), "bin/console", "src/Controller/HomeController.php", "var/cache/dev/x.php", "vendor/autoload.php", "symfony.lock"],
        ["Symfony", "PHP"],
        ["bin/console", "composer.json", "src/Controller/HomeController.php"],
    ),
    Case(
        "wordpress keeps the theme, drops core and wp-config",
        ["wp-config.php", "wp-load.php", "wp-admin/index.php", "wp-includes/version.php", "wp-content/themes/mine/functions.php", "wp-content/uploads/2026/photo.txt"],
        ["WordPress"],
        ["wp-content/themes/mine/functions.php", "wp-load.php"],
    ),
    # ── Mobile ────────────────────────────────────────────────────────────
    Case(
        "flutter hides its runner projects",
        [
            ("pubspec.yaml", "dependencies:\n  flutter:\n    sdk: flutter\n"), "lib/main.dart", "lib/model.g.dart", ".dart_tool/package_config.json", "build/app.txt", "pubspec.lock",
            "ios/Runner.xcodeproj/project.pbxproj", "ios/Runner/AppDelegate.swift", "ios/Pods/Manifest.lock",
            ("android/app/build.gradle", "apply plugin: 'com.android.application'\n"), "android/local.properties", "android/key.properties",
            "linux/CMakeLists.txt", "windows/runner/main.cpp",
        ],
        ["Flutter", "Dart"],
        ["android/app/build.gradle", "ios/Runner/AppDelegate.swift", "lib/main.dart", "linux/CMakeLists.txt", "pubspec.yaml", "windows/runner/main.cpp"],
    ),
    Case(
        "swift package",
        ["Package.swift", "Sources/App/main.swift", ".build/debug/App", "Package.resolved", "App.xcodeproj/project.pbxproj", "App.xcodeproj/xcuserdata/me.xcuserdatad/x.plist", "Pods/Manifest.lock"],
        ["Swift"],
        ["Package.swift", "Sources/App/main.swift"],
    ),
    # ── BEAM ──────────────────────────────────────────────────────────────
    Case(
        "phoenix",
        [("mix.exs", "defp deps do\n  [{:phoenix, \"~> 1.8\"}]\nend\n"), "lib/app_web/router.ex", "_build/dev/lib/app/x.beam", "deps/phoenix/mix.exs", "mix.lock", "priv/static/assets/app.js"],
        ["Phoenix", "Elixir"],
        ["lib/app_web/router.ex", "mix.exs"],
    ),
    Case("erlang", ["rebar.config", "src/app.erl", "_build/default/lib/app/ebin/app.beam", "rebar.lock"], ["Erlang"], ["rebar.config", "src/app.erl"]),
    # ── Functional and scientific ─────────────────────────────────────────
    Case("haskell", ["app.cabal", "stack.yaml", "src/Main.hs", ".stack-work/x", "dist-newstyle/y", "stack.yaml.lock"], ["Haskell"], ["app.cabal", "src/Main.hs", "stack.yaml"]),
    Case("ocaml", ["dune-project", "bin/main.ml", "_build/default/bin/main.exe", "app.opam"], ["OCaml"], ["app.opam", "bin/main.ml", "dune-project"]),
    Case("r", ["analysis.Rproj", "R/model.R", ".Rhistory", "renv/library/x/DESCRIPTION", "renv.lock"], ["R"], ["R/model.R", "analysis.Rproj"]),
    Case("julia", ["Project.toml", "src/App.jl", "Manifest.toml"], ["Julia"], ["Project.toml", "src/App.jl"]),
    Case("perl", ["cpanfile", "lib/App.pm", "blib/lib/App.pm", "local/lib/perl5/x.pm"], ["Perl"], ["cpanfile", "lib/App.pm"]),
    # ── Smart contracts ───────────────────────────────────────────────────
    Case(
        "foundry leaves out its dependency folder, not src/lib",
        ["foundry.toml", "src/Token.sol", "src/lib/Math.sol", "lib/forge-std/src/Test.sol", "out/Token.sol/Token.json", "cache/solidity-files-cache.json"],
        ["Foundry"],
        ["foundry.toml", "src/Token.sol", "src/lib/Math.sol"],
    ),
    Case(
        "hardhat",
        [("package.json", pkg(hardhat="3")), "hardhat.config.ts", "contracts/Token.sol", "artifacts/contracts/Token.json", "cache/x.json", "typechain-types/Token.ts"],
        ["Node.js", "Hardhat"],
        ["contracts/Token.sol", "hardhat.config.ts", "package.json"],
    ),
    # ── Game engines ──────────────────────────────────────────────────────
    Case("godot", ["project.godot", "player.gd", "player.gd.uid", ".godot/imported/x.ctex", "icon.svg.import", "export_presets.cfg"], ["Godot"], ["player.gd", "project.godot"]),
    Case(
        "unreal",
        ["Game.uproject", "Source/Game/Game.cpp", "Content/Maps/Main.umap", "Binaries/Win64/Game.exe", "Intermediate/Build/x.txt", "Saved/Logs/Game.txt", "Game.sln"],
        ["Unreal Engine"],
        ["Game.uproject", "Source/Game/Game.cpp"],
    ),
    # ── Infrastructure and sites ──────────────────────────────────────────
    Case(
        "terraform",
        ["main.tf", "variables.tf", "terraform.tfvars", "terraform.tfvars.example", ".terraform.lock.hcl", ".terraform/providers/x", "terraform.tfstate"],
        ["Terraform"],
        ["main.tf", "terraform.tfvars.example", "variables.tf"],
    ),
    Case("hugo", ["hugo.toml", "content/posts/hello.md", "public/index.html", "resources/_gen/assets/x.css", "layouts/index.html"], ["Hugo"], ["content/posts/hello.md", "hugo.toml", "layouts/index.html"]),
    # ── Monorepos, .gitignore and projects with no marker file ────────────
    Case(
        "laravel api next to a next.js app",
        [
            ("apps/api/composer.json", json.dumps({"require": {"laravel/framework": "^12.0"}})), "apps/api/artisan", "apps/api/app/Http/Kernel.php", "apps/api/storage/framework/views/x.php",
            ("apps/web/package.json", pkg(next="16")), "apps/web/app/page.tsx", "apps/web/storage/framework/kept.txt",
        ],
        ["Laravel", "PHP", "Next.js", "Node.js"],
        ["apps/api/app/Http/Kernel.php", "apps/api/artisan", "apps/api/composer.json", "apps/web/app/page.tsx", "apps/web/package.json", "apps/web/storage/framework/kept.txt"],
    ),
    Case(
        "a gitignored folder does not decide the stack",
        [("requirements.txt", "requests\n"), "main.py", ("scratch/package.json", pkg(next="16")), "scratch/index.js"],
        ["Python"],
        [".gitignore", "main.py", "requirements.txt"],
        gitignore="scratch/\n",
    ),
    Case(
        "scripts with no project file are recognised by file type",
        ["fetch.py", "clean.py", "report.py", "notes.md", "__pycache__/fetch.cpython-312.pyc"],
        ["Python"],
        ["clean.py", "fetch.py", "notes.md", "report.py"],
    ),
    Case(
        "nothing recognisable gets the universal rules",
        ["notes.md", "todo.txt", ".env", "debug.log"],
        [],
        ["notes.md", "todo.txt"],
    ),
]


class StackTests(TempProject):
    def run_case(self, case: Case):
        for entry in case.files:
            rel, content = entry if isinstance(entry, tuple) else (entry, "x\n")
            self.write(rel, content)
        gitignore = None
        if case.gitignore is not None:
            gitignore = self.write(".gitignore", case.gitignore)

        detection = detect(self.root)
        self.assertEqual(detection.ecosystems, case.detected, "detected")

        spec = build_spec(detection.rule_modules, None, gitignore)
        resolved = resolve_files(self.root, spec)
        included = sorted(p.relative_to(self.root).as_posix() for p in resolved.included)
        self.assertEqual(included, sorted(case.included), "included")

    def test_names_are_unique(self):
        names = [c.name for c in CASES]
        self.assertEqual(len(names), len(set(names)))


def _make(case: Case):
    def test(self):
        self.run_case(case)

    test.__doc__ = case.name
    return test


for _case in CASES:
    setattr(StackTests, "test_" + "".join(ch if ch.isalnum() else "_" for ch in _case.name), _make(_case))


class RegistryTests(unittest.TestCase):
    def test_names_and_slugs_are_unique(self):
        names = [s.name for s in stacks.STACKS]
        slugs = [s.slug for s in stacks.STACKS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_every_rule_set_has_patterns(self):
        for key in stacks.RULE_MODULES:
            self.assertTrue(stacks.patterns_for(key), key)

    def test_no_rule_set_brings_files_back(self):
        # A "!pattern" in a rule set could undo an exclusion from .gitignore.
        for stack in stacks.STACKS:
            for key in stacks.rule_keys(stack):
                for pattern in stacks.patterns_for(key):
                    self.assertFalse(pattern.startswith("!"), f"{key}: {pattern}")

    def test_hidden_stacks_exist(self):
        for stack in stacks.STACKS:
            for name in stack.hides:
                self.assertIn(name, stacks.BY_NAME, stack.name)

    def test_every_stack_can_be_detected(self):
        for stack in stacks.STACKS:
            self.assertTrue(stack.markers or stack.deps or stack.extensions, stack.name)

    def test_every_stack_with_rules_is_covered_by_a_case(self):
        covered = {name for case in CASES for name in case.detected}
        missing = [s.name for s in stacks.STACKS if s.name not in covered]
        # Stacks without a project of their own here yet. Add a Case and remove the name.
        self.assertEqual(sorted(missing), sorted(NOT_COVERED_YET))

    def test_describe(self):
        data = stacks.describe(stacks.BY_NAME["Laravel"])
        self.assertEqual(data["slug"], "laravel")
        self.assertIn("vendor/", data["excludes"])
        self.assertIn("storage/logs/", data["excludes"])
        self.assertEqual(stacks.BY_NAME["C#/.NET"].slug, "csharp-net")
        self.assertEqual(stacks.BY_NAME["C/C++"].slug, "c-cpp")


NOT_COVERED_YET = ["Astro", "Bazel", "Electron", "Jekyll", "Jupyter", "Lua", "NestJS", "Nim", "Remix", "Svelte", "Vue"]


if __name__ == "__main__":
    unittest.main()
