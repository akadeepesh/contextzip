"""
picker.py — `contextzip pick`: a full-screen file picker in the terminal.

A thin Textual layer over picker_model.PickerModel, which holds the tree,
the selection, the totals and the patterns. Textual is imported only
inside build_app(), so importing this module (or running any other
command) never loads it.

    contextzip pick · my-app                    44 of 312 files · 186 KB · 46,210 tokens
    Fits  Claude ≈23% · ChatGPT 36% · Gemini ≈5%
    ─────────────────────────────────────────────────────────────────────
     ▾  [x] src/                                 38 files      41,300
        ▸  [-] components/                     9 of 14 files   12,100
           [x] main.tsx                                           420
     ▸  [ ] docs/                              left out
    ─────────────────────────────────────────────────────────────────────
     2 suggestions: PDFs (3 files, 3.6 MB) · Large files (4 files, 9.3 MB)    s to review
    ─────────────────────────────────────────────────────────────────────
     space tick · → open · ← close · / filter · a all · n none · s suggestions · enter pack · w save as default · q quit
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from contextzip.picker_model import REASON_TEXT, Node, PickerModel

KEYS_PACK = "space tick · → open · ← close · / filter · a all · n none · s suggestions · e ignored · enter pack · w save as default · q quit"
KEYS_SAVE = "space tick · → open · ← close · / filter · a all · n none · s suggestions · e ignored · enter save · q quit"
SAVED = "Saved as this project's default."


def save_selection(model: PickerModel) -> Path:
    """Write the selection into .contextzip/config.json as always_include / always_exclude, keeping every other key."""
    from contextzip.webui.persist import save_config_from_ui

    include, exclude = model.as_patterns()
    return save_config_from_ui(model.project_dir, always_include=include, always_exclude=exclude)


def _size(n: int) -> str:
    from contextzip.cli_display import human_size

    return human_size(n)


def _box(node: Node) -> str:
    if node.state == "in":
        return "[x]"
    if node.state == "partial":
        return "[-]"
    if node.hidden_by_default:
        return "[·]"
    return "[ ]"


def row_columns(node: Node, model: PickerModel) -> tuple[str, str]:
    """(middle, right) text for a row: file counts or why it's out, and tokens or size."""
    counted = model.tokens_counted
    if node.is_dir:
        if node.state == "in":
            middle = f"{node.files_in} file{'' if node.files_in == 1 else 's'}"
        elif node.state == "partial":
            middle = f"{node.files_in} of {node.files_total} files"
        elif node.files_total == 0:
            reasons = {f.reason for f in _files(node)}
            middle = REASON_TEXT.get(reasons.pop(), "left out") if len(reasons) == 1 else "left out"
        else:
            middle = "left out"
        right = (f"{node.tokens:,}" if counted else "…") if node.state != "out" else ""
        return middle, right
    if node.state == "in":
        middle = "large" if node.large else ""
        return middle, (f"{node.tokens:,}" if counted else "…")
    if node.large:
        return "large", _size(node.size)
    return REASON_TEXT.get(node.reason or "", "left out"), ""


def _files(node: Node):
    for child in node.children:
        if child.is_dir:
            yield from _files(child)
        else:
            yield child


def layout(width: int) -> tuple[int, int]:
    """(middle, right) column widths for a tree this wide: the middle goes first, then the size."""
    if width >= 80:
        return 24, 12
    if width >= 60:
        return 0, 12
    return 0, 0


def build_app(
    model: PickerModel,
    *,
    title: str,
    fit_targets: list | None = None,
    show_fit: bool = True,
    save_only: bool = False,
    save: Callable[[PickerModel], object] | None = None,
):
    """
    The picker as a Textual App. Its return value (app.run()) is "pack"
    when Enter confirms a selection to package, "save" when Enter saved
    it (--save), and None when you quit.
    """
    from rich.style import Style
    from rich.text import Text
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.geometry import Region, Size
    from textual.screen import ModalScreen
    from textual.scroll_view import ScrollView
    from textual.strip import Strip
    from textual.widgets import Input, Rule, SelectionList, Static

    save = save or save_selection
    green, yellow, dim = Style(color="green"), Style(color="yellow"), Style(dim=True)

    class FileTree(ScrollView, can_focus=True):
        BINDINGS = [
            Binding("up,k", "cursor(-1)", "up", show=False),
            Binding("down,j", "cursor(1)", "down", show=False),
            Binding("pageup", "page(-1)", show=False),
            Binding("pagedown", "page(1)", show=False),
            Binding("home", "jump(0)", show=False),
            Binding("end", "jump(-1)", show=False),
            Binding("right,l", "open", "open", show=False),
            Binding("left,h", "close", "close", show=False),
            Binding("space", "tick", "tick", show=False),
        ]

        def __init__(self) -> None:
            super().__init__(id="tree")
            self.expanded: set[str] = set()
            self.query = ""
            self.show_hidden = False
            self.rows: list[Node] = []
            self.cursor = 0

        # ── rows ──
        def rebuild(self, keep: str | None = None) -> None:
            current = keep if keep is not None else (self.rows[self.cursor].path if self.rows else None)
            self.rows = model.visible_rows(self.expanded, query=self.query, show_hidden=self.show_hidden)
            index = next((i for i, n in enumerate(self.rows) if n.path == current), None)
            if index is None:
                index = min(self.cursor, max(len(self.rows) - 1, 0))
            self.cursor = index
            self.virtual_size = Size(self.size.width, len(self.rows))
            self.refresh()
            self._follow()

        @property
        def current(self) -> Node | None:
            return self.rows[self.cursor] if self.rows else None

        def _follow(self) -> None:
            height = max(self.size.height, 1)
            top = int(self.scroll_offset.y)
            if self.cursor < top:
                self.scroll_to(y=self.cursor, animate=False)
            elif self.cursor >= top + height:
                self.scroll_to(y=self.cursor - height + 1, animate=False)

        @staticmethod
        def _arrow_col(node: Node) -> int:
            return 1 + (node.depth - 1) * 3

        def render_line(self, y: int) -> Strip:
            width = self.size.width
            index = y + int(self.scroll_offset.y)
            if index >= len(self.rows):
                return Strip.blank(width)
            node = self.rows[index]
            mid_w, right_w = layout(width)
            arrow = ("▾" if node.path in self.expanded or self.query.strip() else "▸") if node.is_dir else " "
            box = _box(node)
            out = node.state == "out"
            row_style = dim if out else Style()
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(" " * self._arrow_col(node))
            text.append(arrow, row_style)
            text.append("  ")
            box_style = green if node.state == "in" else yellow if node.state == "partial" else dim
            text.append(box, box_style)
            text.append(" ")
            name = node.name + ("/" if node.is_dir else "")
            middle, right = row_columns(node, model)
            name_w = max(4, width - text.cell_len - (mid_w + 2 if mid_w else 0) - (right_w + 1 if right_w else 0) - 1)
            shown = name if len(name) <= name_w else name[: max(name_w - 1, 1)] + "…"
            text.append(shown.ljust(name_w), row_style)
            if mid_w:
                mid_style = yellow if middle == "large" else dim
                text.append("  " + middle[:mid_w].ljust(mid_w), mid_style)
            if right_w:
                text.append(" " + right[:right_w].rjust(right_w), yellow if (out and node.large) else row_style)
            if index == self.cursor and self.has_focus:
                text.stylize(Style(reverse=True))
            elif index == self.cursor:
                text.stylize(Style(underline=True))
            segments = list(text.render(self.app.console))
            return Strip(segments).crop_extend(0, width, None)

        # ── actions ──
        def action_cursor(self, step: int) -> None:
            if self.rows:
                self.cursor = max(0, min(len(self.rows) - 1, self.cursor + step))
                self.refresh()
                self._follow()

        def action_page(self, direction: int) -> None:
            self.action_cursor(direction * max(self.size.height - 1, 1))

        def action_jump(self, where: int) -> None:
            if self.rows:
                self.cursor = 0 if where == 0 else len(self.rows) - 1
                self.refresh()
                self._follow()

        def action_open(self) -> None:
            node = self.current
            if node is None or not node.is_dir:
                return
            if node.path in self.expanded or self.query.strip():
                if self.cursor + 1 < len(self.rows) and self.rows[self.cursor + 1].parent is node:
                    self.action_cursor(1)
                return
            self.expanded.add(node.path)
            self.rebuild(keep=node.path)

        def action_close(self) -> None:
            node = self.current
            if node is None:
                return
            if node.is_dir and node.path in self.expanded and not self.query.strip():
                self.expanded.discard(node.path)
                self.rebuild(keep=node.path)
                return
            parent = node.parent
            if parent is not None and parent.path:
                self.rebuild(keep=parent.path)

        def action_tick(self) -> None:
            node = self.current
            if node is not None:
                model.toggle(node.path)
                self.app.changed()

        def toggle_open(self, node: Node) -> None:
            if node.path in self.expanded:
                self.expanded.discard(node.path)
            else:
                self.expanded.add(node.path)
            self.rebuild(keep=node.path)

        def on_click(self, event: events.Click) -> None:
            index = int(event.y) + int(self.scroll_offset.y)
            if not (0 <= index < len(self.rows)):
                return
            node = self.rows[index]
            self.cursor = index
            col = self._arrow_col(node)
            x = int(event.x)
            if node.is_dir and col - 1 <= x <= col + 1:
                self.toggle_open(node)
            elif col + 3 <= x <= col + 5:
                model.toggle(node.path)
                self.app.changed()
            else:
                self.refresh()

        def on_resize(self, _event) -> None:
            self.virtual_size = Size(self.size.width, len(self.rows))
            self.refresh()

        def on_focus(self, _event) -> None:
            self.refresh()

        def on_blur(self, _event) -> None:
            self.refresh()

    class FilterInput(Input):
        BINDINGS = [Binding("escape", "clear_filter", "clear", show=False)]

        def action_clear_filter(self) -> None:
            self.value = ""
            self.app.close_filter()

    class SuggestionsScreen(ModalScreen):
        BINDINGS = [Binding("escape,s,q", "close", "back", show=False)]
        DEFAULT_CSS = """
        SuggestionsScreen { align: center middle; }
        SuggestionsScreen > Static { width: 70; padding: 0 1; background: $panel; }
        SuggestionsScreen > SelectionList { width: 70; height: auto; max-height: 16; }
        """

        def compose(self) -> ComposeResult:
            items = []
            for s in model.suggestions():
                label = f"{s['label']}  ({s['count']} file{'' if s['count'] == 1 else 's'}, {_size(s['bytes'])})"
                items.append((label, s["id"], s["on"]))
            yield Static("Leave these out?  space toggles · esc goes back")
            yield SelectionList(*items, id="suggestion-list")

        def on_mount(self) -> None:
            self.query_one(SelectionList).focus()

        def on_selection_list_selection_toggled(self, event) -> None:
            value = event.selection.value
            on = value in event.selection_list.selected
            model.apply_suggestion(value, on)
            self.app.changed()

        def action_close(self) -> None:
            self.dismiss(None)

    class PickerApp(App):
        TITLE = "contextzip pick"
        ENABLE_COMMAND_PALETTE = False
        CSS = """
        Screen { layout: vertical; }
        #header { height: 2; padding: 0 1; }
        #tree { height: 1fr; }
        #filter { height: 1; border: none; padding: 0 1; display: none; }
        #filter.open { display: block; }
        #suggest { height: 1; padding: 0 1; }
        #keys { height: 1; padding: 0 1; }
        Rule { margin: 0; }
        """
        BINDINGS = [
            Binding("ctrl+c", "quit_picker", "quit", priority=True, show=False),
            Binding("q,escape", "quit_picker", "quit", show=False),
            Binding("enter", "confirm", "pack", show=False),
            Binding("w", "save", "save", show=False),
            Binding("slash", "filter", "filter", show=False),
            Binding("a", "all(True)", "all", show=False),
            Binding("n", "all(False)", "none", show=False),
            Binding("s", "suggestions", "suggestions", show=False),
            Binding("e", "hidden", "ignored", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.message = ""

        def compose(self) -> ComposeResult:
            yield Static(id="header")
            yield Rule()
            yield FileTree()
            yield FilterInput(placeholder="filter: part of a path, esc clears", id="filter")
            yield Rule()
            yield Static(id="suggest")
            yield Rule()
            yield Static(id="keys")

        @property
        def tree(self) -> FileTree:
            return self.query_one(FileTree)

        def on_mount(self) -> None:
            tree = self.tree
            # Open the top level's folders one deep when there are few of them.
            top = [n for n in model.root.children if n.is_dir and not n.hidden_by_default]
            if len(top) <= 3:
                tree.expanded.update(n.path for n in top)
            tree.rebuild()
            tree.focus()
            self.changed()
            self.run_worker(self._count_tokens, thread=True, exclusive=True, name="tokens")

        def _count_tokens(self) -> None:
            try:
                counts, exact = model.compute_tokens()
            except Exception as exc:  # noqa: BLE001 — the picker works without counts
                self.call_from_thread(self._tokens_failed, exc)
                return
            self.call_from_thread(self._tokens_ready, counts, exact)

        def _tokens_ready(self, counts, exact) -> None:
            model.apply_tokens(counts, exact)
            self.changed()

        def _tokens_failed(self, exc: Exception) -> None:
            self.message = f"Couldn't count tokens: {exc}"
            self.changed()

        # ── drawing the frame ──
        def changed(self) -> None:
            """Refresh everything that depends on the selection."""
            from contextzip import jsonout
            from contextzip.cli_display import _share_text
            from contextzip.tokens import format_tokens

            files, size, tokens = model.totals()
            token_text = format_tokens(tokens, exact=model.totals_exact()) if model.tokens_counted else "… tokens"
            head = Text(no_wrap=True, overflow="ellipsis")
            head.append("contextzip pick", "bold")
            head.append(" · ", dim)
            head.append(title, "bold")
            width = max(self.size.width - 2, 20)
            for right in (
                f"{files:,} of {model.file_count():,} files · {_size(size)} · {token_text}",
                f"{files:,} of {model.file_count():,} · {token_text}",
                token_text,
            ):
                gap = width - head.cell_len - len(right)
                if gap >= 2:
                    break
            head.append(" " * max(gap, 2) + right)
            head.truncate(width, overflow="ellipsis")
            lines = [head]
            if show_fit and fit_targets:
                fits = Text(no_wrap=True, overflow="ellipsis")
                fits.append("Fits  ", dim)
                if not model.tokens_counted:
                    fits.append("counting tokens…", dim)
                else:
                    rows = jsonout.fit_data(model.token_report(), fit_targets)
                    for i, row in enumerate(rows):
                        if i:
                            fits.append(" · ", dim)
                        part = f"{row['name']} {'' if row['exact'] else '≈'}{_share_text(row['share'])}"
                        fits.append(part + ("" if row["fits"] else ", too big"), Style() if row["fits"] else yellow)
                fits.truncate(width, overflow="ellipsis")
                lines.append(fits)
            header = Text("\n").join(lines)
            header.no_wrap = True
            header.overflow = "ellipsis"
            self.query_one("#header", Static).update(header)

            suggestions = [s for s in model.suggestions() if not s["on"]]
            line = Text(no_wrap=True, overflow="ellipsis")
            if suggestions:
                n = len(suggestions)
                line.append(f"{n} suggestion{'' if n == 1 else 's'}: ")
                line.append(" · ".join(f"{s['label']} ({s['count']} file{'' if s['count'] == 1 else 's'}, {_size(s['bytes'])})" for s in suggestions[:3]))
                line.append("    s to review", dim)
            else:
                line.append("No suggestions for what's ticked.", dim)
            self.query_one("#suggest", Static).update(line)

            keys = Text(no_wrap=True, overflow="ellipsis")
            if self.message:
                keys.append(self.message, "bold")
            else:
                keys.append(KEYS_SAVE if save_only else KEYS_PACK, dim)
            self.query_one("#keys", Static).update(keys)
            self.tree.refresh()

        def on_resize(self, _event) -> None:
            self.changed()

        def say(self, message: str) -> None:
            self.message = message
            self.changed()

        # ── actions ──
        def action_quit_picker(self) -> None:
            self.exit(None)

        def action_confirm(self) -> None:
            if save_only:
                save(model)
                self.exit("save")
            else:
                self.exit("pack")

        def action_save(self) -> None:
            try:
                save(model)
            except OSError as exc:
                self.say(f"Couldn't save: {exc}")
                return
            self.say(SAVED)

        def action_filter(self) -> None:
            box = self.query_one(FilterInput)
            box.add_class("open")
            box.focus()

        def close_filter(self) -> None:
            box = self.query_one(FilterInput)
            box.remove_class("open")
            self.tree.query = ""
            self.tree.rebuild()
            self.tree.focus()

        def on_input_changed(self, event) -> None:
            self.tree.query = event.value
            self.tree.rebuild()

        def on_input_submitted(self, _event) -> None:
            self.tree.focus()

        def action_all(self, on: bool) -> None:
            tree = self.tree
            files = [n.path for n in tree.rows if not n.is_dir]
            dirs = [n for n in tree.rows if n.is_dir]
            # Every file shown, plus what's inside the folders shown: folders follow the tick rule.
            model.set_all(files + [d.path for d in dirs if not any(r.parent is d for r in tree.rows)], on)
            self.changed()

        def action_suggestions(self) -> None:
            if not model.suggestions():
                self.say("No suggestions for what's ticked.")
                return
            self.push_screen(SuggestionsScreen())

        def action_hidden(self) -> None:
            tree = self.tree
            tree.show_hidden = not tree.show_hidden
            tree.rebuild()
            self.say("Showing files the rules and .gitignore leave out." if tree.show_hidden else "")

        def on_key(self, event) -> None:
            # Any key clears a message from the footer.
            if self.message and event.key not in ("w",):
                self.message = ""
                self.changed()

    return PickerApp()


def run_picker(model: PickerModel, **kwargs) -> str | None:
    """Run the picker full-screen and return what Enter decided ("pack" / "save"), or None for quit."""
    app = build_app(model, **kwargs)
    return app.run()
