"""Release 0.9 step 5: the picker's model (no Textual needed)."""

from __future__ import annotations

from contextzip.detector import detect
from contextzip.filters import build_force_include_spec, build_spec, normalize_pattern, resolve_files
from contextzip.picker_model import PickerModel, literal_pattern
from contextzip.project_config import load_project_config
from tests.helpers import TempProject


class PickerModelCase(TempProject):
    def make_project(self) -> None:
        self.write("package.json", '{"name": "demo"}\n')
        self.write(".gitignore", "logs/\n*.tmp\n")
        self.write("src/index.ts", "export const a = 1;\n")
        self.write("src/util.ts", "export const b = 2;\n" * 5)
        self.write("src/components/Button.tsx", "export const Button = () => null;\n")
        self.write("src/components/Card.tsx", "export const Card = () => null;\n")
        self.write("docs/guide.md", "# Guide\n")
        self.write("docs/spec.pdf", b"%PDF-1.4 fake")
        self.write("logs/today.log", "x\n")
        self.write("logs/keep.txt", "keep\n")
        self.write("scratch.tmp", "tmp\n")
        self.write(".env", "TOKEN=abc\n")
        self.write("app/[id]/page.tsx", "export default function Page() { return null; }\n")
        self.write("node_modules/dep/index.js", "module.exports = 1;\n")

    def model(self, **kwargs) -> PickerModel:
        return PickerModel(self.root, detect(self.root), load_project_config(self.root), **kwargs)

    def rels(self, model: PickerModel) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in model.included()]

    def assert_round_trip(self, model: PickerModel) -> None:
        include, exclude = model.as_patterns()
        detection = detect(self.root)
        spec = build_spec(
            detection.rule_modules,
            extra_exclude=[normalize_pattern(p) for p in exclude] or None,
            gitignore_path=self.root / ".gitignore",
        )
        force = build_force_include_spec([normalize_pattern(p) for p in include] or None)
        resolved = resolve_files(self.root, spec, force_include=force)
        got = sorted(p.relative_to(self.root).as_posix() for p in resolved.included)
        self.assertEqual(got, sorted(self.rels(model)), f"include={include} exclude={exclude}")


class TreeTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_default_selection_is_a_normal_run(self):
        m = self.model()
        self.assertEqual(
            self.rels(m),
            [".gitignore", "app/[id]/page.tsx", "docs/guide.md", "docs/spec.pdf", "package.json",
             "src/components/Button.tsx", "src/components/Card.tsx", "src/index.ts", "src/util.ts"],
        )

    def test_nodes_and_order(self):
        m = self.model()
        top = [n.name for n in m.root.children]
        self.assertEqual(top[:4], ["app", "docs", "logs", "src"])  # folders first
        self.assertNotIn("node_modules", m.nodes)  # never scanned
        util = m.nodes["src/util.ts"]
        self.assertFalse(util.is_dir)
        self.assertEqual(util.path, "src/util.ts")
        self.assertEqual(util.depth, 2)
        self.assertIs(util.parent, m.nodes["src"])
        self.assertEqual(m.nodes["src"].size, sum(f.size for f in m.nodes["src"].children if not f.is_dir) + m.nodes["src/components"].size)

    def test_reasons(self):
        m = self.model()
        self.assertEqual(m.nodes["logs/today.log"].reason, "gitignore")
        self.assertEqual(m.nodes["scratch.tmp"].reason, "gitignore")
        self.assertEqual(m.nodes[".env"].reason, "rule")
        self.assertIsNone(m.nodes["src/util.ts"].reason)

    def test_config_reason_and_existing_config(self):
        self.write(".contextzip/config.json", '{"always_exclude": ["docs/"], "always_include": ["logs/keep.txt"]}')
        m = self.model()
        self.assertEqual(m.nodes["docs/guide.md"].reason, "config")
        self.assertTrue(m.is_selected("logs/keep.txt"))
        self.assertFalse(m.is_selected("docs/guide.md"))
        self.assert_round_trip(m)

    def test_large_files(self):
        self.write("data/big.json", "x" * (1024 * 1024 + 1))
        m = self.model()
        self.assertTrue(m.nodes["data/big.json"].large)
        self.assertFalse(m.nodes["src/util.ts"].large)


class ToggleTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_toggle_file(self):
        m = self.model()
        m.toggle("src/util.ts")
        self.assertFalse(m.is_selected("src/util.ts"))
        self.assertEqual(m.nodes["src"].state, "partial")
        m.toggle("src/util.ts")
        self.assertEqual(m.nodes["src"].state, "in")

    def test_toggle_folder_out_and_in(self):
        m = self.model()
        m.toggle("src")
        self.assertEqual(m.nodes["src"].state, "out")
        self.assertEqual(m.nodes["src/components"].state, "out")
        m.toggle("src")
        self.assertEqual(m.nodes["src"].state, "in")
        self.assertTrue(m.is_selected("src/components/Card.tsx"))

    def test_partial_folder_tick_brings_everything_in(self):
        m = self.model()
        m.toggle("src/components/Card.tsx")
        self.assertEqual(m.nodes["src/components"].state, "partial")
        self.assertEqual(m.nodes["src"].state, "partial")
        self.assertEqual((m.nodes["src"].files_in, m.nodes["src"].files_total), (3, 4))
        m.toggle("src")
        self.assertEqual(m.nodes["src"].state, "in")

    def test_folder_tick_does_not_drag_in_ignored_files(self):
        m = self.model(include_only=[])
        m.set_all([n.path for n in m.visible_rows(set())], True)  # what `a` does
        self.assertTrue(m.is_selected("src/util.ts"))
        self.assertFalse(m.is_selected(".env"))
        self.assertFalse(m.is_selected("scratch.tmp"))
        self.assertFalse(m.is_selected("logs/today.log"))

    def test_a_folder_of_only_ignored_files_comes_in_when_ticked(self):
        m = self.model(include_only=[])
        m.toggle("logs")
        self.assertTrue(m.is_selected("logs/today.log"))
        self.assertEqual(m.nodes["logs"].state, "in")
        m.toggle("logs")
        self.assertFalse(m.is_selected("logs/keep.txt"))

    def test_mixed_folder_keeps_rule_files_out(self):
        self.write("conf/settings.py", "DEBUG = True\n")
        self.write("conf/.env.local", "SECRET=1\n")
        m = self.model(include_only=[])
        m.toggle("conf")
        self.assertTrue(m.is_selected("conf/settings.py"))
        self.assertFalse(m.is_selected("conf/.env.local"))
        self.assertEqual(m.nodes["conf"].state, "in")  # the left-out secret doesn't make it partial
        m.toggle("conf")
        self.assertEqual(m.nodes["conf"].state, "out")

    def test_explicit_tick_of_a_secret(self):
        m = self.model()
        m.toggle(".env")
        self.assertTrue(m.is_selected(".env"))
        self.assertEqual(m.nodes[".env"].state, "in")
        self.assert_round_trip(m)

    def test_set_all(self):
        m = self.model(include_only=[])
        m.set_all(["src/util.ts", "docs"], True)
        self.assertEqual(self.rels(m), ["docs/guide.md", "docs/spec.pdf", "src/util.ts"])
        m.set_all(["docs"], False)
        self.assertEqual(self.rels(m), ["src/util.ts"])

    def test_unknown_path(self):
        with self.assertRaises(KeyError):
            self.model().toggle("nope/missing.ts")

    def test_totals(self):
        m = self.model(include_only=["src/util.ts"])
        files, size, tokens = m.totals()
        self.assertEqual(files, 1)
        self.assertEqual(size, (self.root / "src/util.ts").stat().st_size)
        self.assertEqual(tokens, 0)  # not counted yet
        m.count_tokens()
        self.assertGreater(m.totals()[2], 0)
        self.assertEqual(m.nodes["src"].tokens, m.nodes["src/util.ts"].tokens)


class StartTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_include_only_paths(self):
        m = self.model(include_only=["src/components", ".env"])
        self.assertEqual(self.rels(m), [".env", "src/components/Button.tsx", "src/components/Card.tsx"])

    def test_nothing(self):
        m = self.model(include_only=[])
        self.assertEqual(m.included(), [])
        self.assertEqual(m.nodes["src"].state, "out")


class FilterAndRowsTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_filter_keeps_ancestors(self):
        m = self.model()
        self.assertEqual(m.filter("butt"), ["src", "src/components", "src/components/Button.tsx"])
        self.assertEqual(m.filter("BUTTON"), m.filter("button"))
        self.assertEqual(len(m.filter("")), len(m.nodes) - 1)

    def test_rows_follow_expansion_and_hidden(self):
        m = self.model()
        top = [n.path for n in m.visible_rows(set())]
        self.assertIn("src", top)
        self.assertNotIn("src/util.ts", top)
        self.assertNotIn(".env", top)  # out by the rules: hidden until `e`
        self.assertNotIn("logs", top)  # every file in it is git-ignored
        self.assertIn(".env", [n.path for n in m.visible_rows(set(), show_hidden=True)])
        self.assertIn("src/util.ts", [n.path for n in m.visible_rows({"src"})])

    def test_rows_with_a_query_open_the_way(self):
        m = self.model()
        self.assertEqual([n.path for n in m.visible_rows(set(), query="card")], ["src", "src/components", "src/components/Card.tsx"])

    def test_a_ticked_ignored_file_is_shown(self):
        m = self.model()
        m.toggle(".env")
        self.assertIn(".env", [n.path for n in m.visible_rows(set())])


class SuggestionTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_on_and_off(self):
        m = self.model()
        pdf = next(s for s in m.suggestions() if s["id"] == "ext:.pdf")
        self.assertFalse(pdf["on"])
        self.assertEqual(pdf["count"], 1)
        m.apply_suggestion("ext:.pdf", True)
        self.assertFalse(m.is_selected("docs/spec.pdf"))
        self.assertTrue(next(s for s in m.suggestions() if s["id"] == "ext:.pdf")["on"])
        m.apply_suggestion("ext:.pdf", False)
        self.assertTrue(m.is_selected("docs/spec.pdf"))
        self.assertFalse(next(s for s in m.suggestions() if s["id"] == "ext:.pdf")["on"])

    def test_large_files_suggestion(self):
        self.write("data/big.json", "x" * (1024 * 1024 + 1))
        m = self.model()
        m.apply_suggestion("large-files", True)
        self.assertFalse(m.is_selected("data/big.json"))

    def test_unknown_suggestion(self):
        with self.assertRaises(KeyError):
            self.model().apply_suggestion("ext:.nope", True)


class PatternTests(PickerModelCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_project()

    def test_unchanged_selection_needs_nothing(self):
        self.assertEqual(self.model().as_patterns(), ([], []))

    def test_folder_out_becomes_an_anchored_folder(self):
        m = self.model()
        m.toggle("docs")
        self.assertEqual(m.as_patterns(), ([], ["/docs/"]))
        self.assert_round_trip(m)

    def test_ignored_folder_in_becomes_an_include(self):
        m = self.model()
        m.toggle("logs")
        self.assertEqual(m.as_patterns(), (["/logs/"], []))
        self.assert_round_trip(m)

    def test_single_files(self):
        m = self.model()
        m.toggle("src/util.ts")
        m.toggle("logs/keep.txt")
        self.assertEqual(m.as_patterns(), (["/logs/keep.txt"], ["/src/util.ts"]))
        self.assert_round_trip(m)

    def test_brackets_are_literal(self):
        self.write("app/layout.tsx", "export default 1;\n")
        m = self.model()
        m.toggle("app/[id]/page.tsx")
        include, exclude = m.as_patterns()
        self.assertEqual(exclude, ["/app/[[]id[]]/"])
        self.assert_round_trip(m)
        self.assertEqual(literal_pattern("a*b?.txt", False), "/a[*]b[?].txt")

    def test_same_name_elsewhere_is_untouched(self):
        self.write("src/docs/notes.md", "# notes\n")
        m = self.model()
        m.toggle("docs")
        self.assert_round_trip(m)
        self.assertTrue(m.is_selected("src/docs/notes.md"))

    def test_many_selections_round_trip(self):
        selections = [
            ["src/components"],
            ["src/index.ts", ".env", "docs/spec.pdf"],
            ["logs", "scratch.tmp"],
            ["app", "src"],
        ]
        for toggles in selections:
            m = self.model()
            for path in toggles:
                m.toggle(path)
            with self.subTest(toggles=toggles):
                self.assert_round_trip(m)
        for start in ([], ["src/util.ts"], ["logs/keep.txt", "docs"]):
            with self.subTest(start=start):
                self.assert_round_trip(self.model(include_only=start))

    def test_a_folder_with_pruned_subfolders_is_listed_file_by_file(self):
        self.write("libs/lib.js", "x\n")
        self.write("libs/node_modules/x/i.js", "y\n")
        self.write(".gitignore", "logs/\n*.tmp\nlibs/\n")
        m = self.model()
        m.toggle("libs")
        include, _exclude = m.as_patterns()
        self.assertEqual(include, ["/libs/lib.js"])  # "/libs/" would bring node_modules in too
        self.assert_round_trip(m)


class MonorepoTests(PickerModelCase):
    def test_round_trip_in_a_monorepo(self):
        self.write(".git/HEAD", "ref: refs/heads/main\n")
        self.write("apps/web/package.json", '{"name": "web", "dependencies": {"next": "1"}}\n')
        self.write("apps/web/.gitignore", "/generated/\n")
        self.write("apps/web/app/page.tsx", "export default 1;\n")
        self.write("apps/web/generated/types.ts", "export type A = 1;\n")
        self.write("apps/web/.next/cache.json", "{}\n")
        self.write("apps/api/pyproject.toml", "[project]\nname='api'\n")
        self.write("apps/api/main.py", "print(1)\n")
        self.write("apps/api/build/out.txt", "x\n")
        m = self.model()
        self.assertEqual(m.nodes["apps/web/generated/types.ts"].reason, "gitignore")
        self.assertFalse(m.is_selected("apps/web/generated/types.ts"))
        for toggles in (["apps/api"], ["apps/web/generated"], ["apps/web/app/page.tsx", "apps/api/main.py"]):
            m = self.model()
            for path in toggles:
                m.toggle(path)
            with self.subTest(toggles=toggles):
                self.assert_round_trip(m)
