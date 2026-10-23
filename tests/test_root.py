from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from contextzip.packager import _workspace_dir
from contextzip.root import find_project_root, to_root_relative
from tests.helpers import TempProject


class FindProjectRootTests(TempProject):
    def test_an_unreadable_sibling_folder_is_not_a_crash(self):
        # On Linux, /tmp/systemd-private-* can't be looked into: running
        # from /tmp/<project> crashed while checking the siblings.
        # (Not directly in self.tmp: that's above the fake home, where
        # siblings are never checked.)
        project = self.tmp / "shop" / "app"
        project.mkdir(parents=True)
        (self.tmp / "shop" / "locked").mkdir()
        real_is_dir = Path.is_dir

        def is_dir(path, *args, **kwargs):
            if "locked" in path.parts and path.name != "locked":
                raise PermissionError(13, "Permission denied", str(path))
            return real_is_dir(path, *args, **kwargs)

        with mock.patch.object(Path, "is_dir", is_dir):
            pr = find_project_root(project)
        self.assertEqual(pr.root, project)

    def test_git_folder_is_the_root_from_a_subfolder(self):
        (self.root / ".git").mkdir()
        sub = self.root / "frontend" / "src"
        sub.mkdir(parents=True)
        pr = find_project_root(sub)
        self.assertEqual(pr.root, self.root)
        self.assertEqual(pr.kind, "git")
        self.assertEqual(pr.subdir, "frontend/src")

    def test_outermost_repo_wins_over_a_nested_one(self):
        # create-next-app runs `git init` inside frontend/ of a monorepo
        (self.root / ".git").mkdir()
        (self.root / "frontend" / ".git").mkdir(parents=True)
        pr = find_project_root(self.root / "frontend")
        self.assertEqual(pr.root, self.root)

    def test_git_file_counts_as_a_repo(self):
        # worktrees and submodules have a .git *file*
        (self.root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x")
        (self.root / "pkg").mkdir()
        self.assertEqual(find_project_root(self.root / "pkg").root, self.root)

    def test_project_config_marks_a_root_without_git(self):
        (self.root / ".contextzip").mkdir()
        (self.root / ".contextzip" / "config.json").write_text("{}")
        (self.root / "a" / "b").mkdir(parents=True)
        pr = find_project_root(self.root / "a" / "b")
        self.assertEqual((pr.root, pr.kind), (self.root, "config"))

    def test_existing_workspace_marks_a_root_without_git(self):
        (self.root / ".contextzip" / "output").mkdir(parents=True)
        (self.root / "a").mkdir()
        self.assertEqual(find_project_root(self.root / "a").root, self.root)

    def test_falls_back_to_the_current_folder(self):
        pr = find_project_root(self.root)
        self.assertEqual((pr.root, pr.kind), (self.root, "cwd"))

    def test_never_climbs_to_home(self):
        home = self.tmp / "home"
        (home / ".git").mkdir()
        project = home / "code" / "app"
        project.mkdir(parents=True)
        self.assertEqual(find_project_root(project).root, project)

    def test_env_override(self):
        os.environ["CONTEXTZIP_ROOT"] = str(self.root)
        sub = self.root / "x"
        sub.mkdir()
        pr = find_project_root(sub)
        self.assertEqual((pr.root, pr.kind), (self.root, "env"))

    def test_workspace_lands_at_the_root_from_a_nested_repo(self):
        (self.root / ".git").mkdir()
        (self.root / "frontend" / ".git").mkdir(parents=True)
        workspace, is_git = _workspace_dir(self.root / "frontend")
        self.assertEqual(workspace, self.root / ".contextzip")
        self.assertTrue(is_git)


class SuggestedRootTests(TempProject):
    def make(self, parent: str, repos: list[str]):
        base = self.root / parent
        for name in repos:
            (base / name / ".git").mkdir(parents=True)
        return base

    def test_sibling_repos_suggest_their_parent(self):
        shop = self.make("shop", ["frontend", "backend"])
        pr = find_project_root(shop / "frontend")
        self.assertEqual(pr.root, shop / "frontend")
        self.assertEqual(pr.suggested_root, shop)

    def test_accepting_makes_it_the_root(self):
        shop = self.make("shop", ["frontend", "backend"])
        (shop / ".contextzip").mkdir()
        (shop / ".contextzip" / "config.json").write_text("{}")
        pr = find_project_root(shop / "frontend")
        self.assertEqual((pr.root, pr.kind), (shop, "config"))
        self.assertIsNone(pr.suggested_root)

    def test_generic_folders_are_never_suggested(self):
        code = self.make("code", ["app-a", "app-b"])
        self.assertIsNone(find_project_root(code / "app-a").suggested_root)

    def test_many_projects_look_like_a_projects_folder(self):
        many = self.make("clients", [f"site{i}" for i in range(7)])
        self.assertIsNone(find_project_root(many / "site1").suggested_root)

    def test_a_single_repo_is_not_a_monorepo(self):
        solo = self.make("solo", ["app"])
        self.assertIsNone(find_project_root(solo / "app").suggested_root)


class SubmoduleTests(TempProject):
    def test_inside_a_submodule(self):
        (self.root / ".git" / "modules" / "libs" / "ui").mkdir(parents=True)
        sub = self.root / "libs" / "ui"
        sub.mkdir(parents=True)
        (sub / ".git").write_text("gitdir: ../../.git/modules/libs/ui\n")
        (sub / "src").mkdir()
        pr = find_project_root(sub / "src")
        self.assertEqual(pr.root, self.root)
        self.assertEqual(pr.submodule, sub)

    def test_a_worktree_is_not_a_submodule(self):
        (self.root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/feature\n")
        self.assertIsNone(find_project_root(self.root).submodule)


class ToRootRelativeTests(TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.write("frontend/src/app.ts", "x")
        self.write("backend/api/main.py", "x")
        self.write("docs/readme.md", "x")
        self.pr = find_project_root(self.root / "frontend")

    def test_paths_are_relative_to_where_you_are(self):
        self.assertEqual(to_root_relative(["src/"], self.pr), ["frontend/src/"])
        self.assertEqual(to_root_relative(["./src"], self.pr), ["frontend/src"])

    def test_parent_paths(self):
        self.assertEqual(to_root_relative(["../backend/api"], self.pr), ["backend/api"])

    def test_root_relative_paths_still_work(self):
        self.assertEqual(to_root_relative(["docs"], self.pr), ["docs"])

    def test_dot_means_the_current_folder(self):
        self.assertEqual(to_root_relative(["."], self.pr), ["frontend"])

    def test_bare_globs_are_left_alone(self):
        self.assertEqual(to_root_relative(["*.log"], self.pr, patterns=True), ["*.log"])

    def test_nothing_changes_at_the_root(self):
        pr = find_project_root(self.root)
        self.assertEqual(to_root_relative(["src/", "*.log"], pr), ["src/", "*.log"])
