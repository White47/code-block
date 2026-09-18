"""Local read-only Tk desktop shell for project understanding."""

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any

from codeviz.core import SourceAnchor, ViewKind, VisibleGraph

from . import theme
from .controller import AppController, SourceDocument
from .graph_canvas import GraphCanvas
from .presenter import OverviewChoice, build_inspector, build_overview, build_problems

ACTIVITIES = ("Explorer", "Search", "Architecture", "Execution", "Symbols", "Settings")


class CodeVizApp:
    """Tk widgets only; parsing, graph selection and file safety live elsewhere."""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("Code Map — Python project understanding")
        self.root.geometry("1440x880")
        self.root.minsize(1000, 650)
        self.root.configure(bg=theme.BACKGROUND)
        self.controller = AppController()
        self._messages: Queue[tuple[str, str | None]] = Queue()
        self._analyzing = False
        self._views: dict[ViewKind, VisibleGraph] = {}
        self._graph_tabs: dict[ViewKind, tuple[Any, GraphCanvas]] = {}
        self._roots: dict[ViewKind, str | None] = {}
        self._selected_id: str | None = None
        self._active_kind: ViewKind | None = None
        self._side_open = False
        self._source_tabs: dict[tuple[str, bool], Any] = {}
        self._source_widgets: dict[tuple[str, bool], Any] = {}
        self._problems_tab: Any | None = None
        self._problem_anchors: dict[str, SourceAnchor] = {}
        self._configure_style()
        self._build_shell()
        self._render_overview()
        self.root.after(100, self._poll_messages)

    def _configure_style(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TNotebook", background=theme.BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", background=theme.PANEL, foreground=theme.MUTED,
                        padding=(15, 9), font=(theme.UI_FONT, 10))
        style.map("TNotebook.Tab", background=[("selected", theme.SURFACE)],
                  foreground=[("selected", theme.TEXT)])
        style.configure("Treeview", background=theme.PANEL, fieldbackground=theme.PANEL,
                        foreground=theme.TEXT, borderwidth=0, rowheight=27,
                        font=(theme.UI_FONT, 10))
        style.map("Treeview", background=[("selected", theme.SURFACE_SELECTED)])
        style.configure("Treeview.Heading", background=theme.SURFACE,
                        foreground=theme.TEXT, font=(theme.UI_FONT, 10, "bold"))

    def _button(self, parent: Any, label: str, command: Any, *, accent: bool = False) -> Any:
        return self.tk.Button(parent, text=label, command=command,
                              bg=theme.ACCENT if accent else theme.SURFACE,
                              fg=theme.BACKGROUND if accent else theme.TEXT,
                              activebackground=theme.SURFACE_SELECTED,
                              activeforeground=theme.TEXT, relief="flat",
                              padx=12, pady=7, cursor="hand2",
                              font=(theme.UI_FONT, 10, "bold" if accent else "normal"))

    def _build_shell(self) -> None:
        tk = self.tk
        header = tk.Frame(self.root, bg=theme.PANEL, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="CODE / MAP", bg=theme.PANEL, fg=theme.ACCENT,
                 font=(theme.UI_FONT, 15, "bold")).pack(side="left", padx=18)
        self.path_label = tk.Label(header, text="No project open", bg=theme.PANEL,
                                   fg=theme.MUTED, font=(theme.CODE_FONT, 9))
        self.path_label.pack(side="left", padx=20)
        self.analyze_button = self._button(header, "Analyze Project", self._start_analyze,
                                           accent=True)
        self.analyze_button.pack(side="right", padx=14, pady=5)
        self._button(header, "Open Folder", self._choose_project).pack(side="right", pady=5)

        body = tk.PanedWindow(self.root, orient="horizontal", bg=theme.BORDER,
                              sashwidth=4, bd=0)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=theme.PANEL, width=300)
        body.add(left, minsize=210)
        self._build_sidebar(left)

        editor = tk.Frame(body, bg=theme.BACKGROUND)
        body.add(editor, minsize=450)
        self.editor_panes = tk.PanedWindow(editor, orient="horizontal", bg=theme.BORDER,
                                           sashwidth=4, bd=0)
        self.editor_panes.pack(fill="both", expand=True)
        self.main_tabs: Any = self.ttk.Notebook(self.editor_panes)
        self.editor_panes.add(self.main_tabs, minsize=400)
        self.side_tabs: Any = self.ttk.Notebook(self.editor_panes)
        self.main_tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.overview_tab = tk.Frame(self.main_tabs, bg=theme.BACKGROUND)
        self.main_tabs.add(self.overview_tab, text="Project Overview")

        inspector = tk.Frame(body, bg=theme.PANEL, width=340)
        body.add(inspector, minsize=270)
        self._build_inspector(inspector)
        self.status_label = tk.Label(self.root, text="Open a local Python project to begin",
                                     anchor="w", bg=theme.SURFACE, fg=theme.MUTED,
                                     padx=14, pady=5, font=(theme.UI_FONT, 9))
        self.status_label.pack(fill="x")

    def _build_sidebar(self, parent: Any) -> None:
        tk = self.tk
        activity = tk.Frame(parent, bg=theme.BACKGROUND, width=70)
        activity.pack(side="left", fill="y")
        activity.pack_propagate(False)
        self.sidebar = tk.Frame(parent, bg=theme.PANEL)
        self.sidebar.pack(side="left", fill="both", expand=True)
        labels = ("EX", "⌕", "◇", "↳", "{}", "⚙")
        for name, label in zip(ACTIVITIES, labels, strict=True):
            self._button(activity, label,
                         lambda activity_name=name: self._set_activity(activity_name)).pack(
                             fill="x", padx=8, pady=4)
        self.sidebar_title = tk.Label(self.sidebar, text="EXPLORER", bg=theme.PANEL,
                                      fg=theme.MUTED, anchor="w", padx=14, pady=14,
                                      font=(theme.UI_FONT, 10, "bold"))
        self.sidebar_title.pack(fill="x")
        self.sidebar_tree = self.ttk.Treeview(self.sidebar, show="tree", selectmode="browse")
        self.sidebar_tree.pack(fill="both", expand=True, padx=7)
        self.sidebar_tree.bind("<Double-Button-1>", self._activate_sidebar)
        self.sidebar_tree.bind("<Return>", self._activate_sidebar)
        self._sidebar_targets: dict[str, tuple[str, str]] = {}
        self._set_activity("Explorer")

    def _build_inspector(self, parent: Any) -> None:
        tk = self.tk
        tk.Label(parent, text="INSPECTOR", bg=theme.PANEL, fg=theme.MUTED,
                 anchor="w", padx=14, pady=14,
                 font=(theme.UI_FONT, 10, "bold")).pack(fill="x")
        self.inspector_text = tk.Text(parent, bg=theme.PANEL, fg=theme.TEXT,
                                      insertbackground=theme.ACCENT, relief="flat",
                                      wrap="word", padx=15, pady=12,
                                      font=(theme.UI_FONT, 10), state="disabled")
        self.inspector_text.pack(fill="both", expand=True)
        actions = tk.Frame(parent, bg=theme.PANEL)
        actions.pack(fill="x", padx=9, pady=9)
        self.source_button = self._button(actions, "Open Source",
                                          lambda: self._open_selected_source(False))
        self.source_button.pack(side="left", padx=3)
        self.side_button = self._button(actions, "Open to Side",
                                        lambda: self._open_selected_source(True))
        self.side_button.pack(side="left", padx=3)
        self._set_inspector_message("Select a model, step or data node to inspect its source.")

    def _set_inspector_message(self, message: str) -> None:
        self.inspector_text.configure(state="normal")
        self.inspector_text.delete("1.0", "end")
        self.inspector_text.insert("end", message)
        self.inspector_text.configure(state="disabled")
        self.source_button.configure(state="disabled")
        self.side_button.configure(state="disabled")

    def _set_activity(self, name: str) -> None:
        self.sidebar_title.configure(text=name.upper())
        tree = self.sidebar_tree
        tree.delete(*tree.get_children())
        self._sidebar_targets.clear()
        if name == "Architecture":
            self._open_graph(ViewKind.ARCHITECTURE)
        elif name == "Execution":
            self._open_graph(ViewKind.EXECUTION)
        elif name in ("Search", "Symbols"):
            tree.insert("", "end", text=f"{name} navigation arrives in Phase 9")
        elif name == "Settings":
            tree.insert("", "end", text="Persistent settings arrive in Phase 10")
        elif name == "Explorer":
            self._refresh_explorer()

    def _refresh_explorer(self) -> None:
        analysis = self.controller.analysis
        if analysis is None:
            if self.controller.project_root is not None:
                self.sidebar_tree.insert("", "end", text="Click Analyze Project")
            return
        models = self.sidebar_tree.insert("", "end", text="Models", open=True)
        for definition in analysis.model.definitions if analysis.model else ():
            item = self.sidebar_tree.insert(models, "end", text=definition.qualified_name)
            self._sidebar_targets[item] = ("model", definition.symbol_id)
        entries = self.sidebar_tree.insert("", "end", text="Entry Points", open=True)
        for entry in analysis.training.entry_points if analysis.training else ():
            item = self.sidebar_tree.insert(entries, "end", text=f"{entry.kind.value} · {entry.name}")
            self._sidebar_targets[item] = ("entry", entry.id)
        files = self.sidebar_tree.insert("", "end", text="Files", open=False)
        for parsed in analysis.files:
            item = self.sidebar_tree.insert(files, "end", text=parsed.relative_path)
            self._sidebar_targets[item] = ("file", parsed.relative_path)
        problems = self.sidebar_tree.insert("", "end", text=f"Problems ({len(analysis.issues)})")
        self._sidebar_targets[problems] = ("problems", "")
        for problem in build_problems(analysis):
            item = self.sidebar_tree.insert(
                problems, "end", text=f"{problem.code}: {problem.file_path}:{problem.line or 1}")
            self._sidebar_targets[item] = ("problem", f"{problem.file_path}:{problem.line or 1}")

    def _activate_sidebar(self, _event: Any) -> None:
        selected = self.sidebar_tree.selection()
        if not selected or selected[0] not in self._sidebar_targets:
            return
        kind, value = self._sidebar_targets[selected[0]]
        if kind == "model":
            self._open_graph(ViewKind.ARCHITECTURE, value)
        elif kind == "entry":
            self._open_graph(ViewKind.EXECUTION, value)
        elif kind == "file":
            self._open_source(SourceAnchor(value, 1), False)
        elif kind == "problem":
            path, line = value.rsplit(":", 1)
            self._open_source(SourceAnchor(path, int(line)), False)
        elif kind == "problems":
            self._open_problems()

    def _reset_editors(self) -> None:
        for tab in self.main_tabs.tabs():
            if tab != str(self.overview_tab):
                widget = self.main_tabs.nametowidget(tab)
                self.main_tabs.forget(tab)
                widget.destroy()
        for tab in self.side_tabs.tabs():
            widget = self.side_tabs.nametowidget(tab)
            self.side_tabs.forget(tab)
            widget.destroy()
        if self._side_open:
            self.editor_panes.forget(self.side_tabs)
            self._side_open = False
        self._views.clear()
        self._graph_tabs.clear()
        self._roots.clear()
        self._source_tabs.clear()
        self._source_widgets.clear()
        self._problems_tab = None
        self._problem_anchors.clear()
        self._selected_id = None
        self._active_kind = None
        self._set_inspector_message("Select a model, step or data node to inspect its source.")

    def _choose_project(self) -> None:
        if self._analyzing:
            return
        selected = self.filedialog.askdirectory(title="Open a local Python project")
        if not selected:
            return
        try:
            self.controller.open_project(Path(selected))
        except (OSError, ValueError) as error:
            self.messagebox.showerror("Cannot open project", str(error))
            return
        self._reset_editors()
        self.path_label.configure(text=str(self.controller.project_root))
        self.status_label.configure(text="Project opened · click Analyze Project to read source")
        self._set_activity("Explorer")
        self._render_overview()

    def _start_analyze(self) -> None:
        if self._analyzing:
            return
        if self.controller.project_root is None:
            self._choose_project()
            if self.controller.project_root is None:
                return
        self._reset_editors()
        self._analyzing = True
        self.analyze_button.configure(state="disabled")
        self.status_label.configure(text="Analyzing Python source · no project code is executed")
        self._render_overview()

        def worker() -> None:
            try:
                self.controller.analyze()
            except Exception as error:
                self._messages.put(("error", f"{type(error).__name__}: {error}"))
            else:
                self._messages.put(("done", None))

        Thread(target=worker, daemon=True).start()

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, detail = self._messages.get_nowait()
                self._analyzing = False
                self.analyze_button.configure(state="normal")
                if kind == "error":
                    self.status_label.configure(text=f"Analysis failed · {detail}")
                    self.messagebox.showerror("Analysis failed", detail or "Unknown error")
                else:
                    analysis = self.controller.analysis
                    issue_count = len(analysis.issues) if analysis else 0
                    self.status_label.configure(
                        text=f"{self.controller.status.value} · {issue_count} analysis issues")
                    self._set_activity("Explorer")
                self._render_overview()
        except Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _render_overview(self) -> None:
        tk = self.tk
        for child in self.overview_tab.winfo_children():
            child.destroy()
        top = tk.Frame(self.overview_tab, bg=theme.BACKGROUND)
        top.pack(fill="x", padx=34, pady=(35, 18))
        tk.Label(top, text="PROJECT OVERVIEW", bg=theme.BACKGROUND,
                 fg=theme.ACCENT, font=(theme.UI_FONT, 11, "bold")).pack(anchor="w")
        project = self.controller.project_root
        tk.Label(top, text=project.name if project else "Your next codebase, made legible",
                 bg=theme.BACKGROUND, fg=theme.TEXT,
                 font=(theme.UI_FONT, 24, "bold")).pack(anchor="w", pady=(7, 3))
        tk.Label(top, text=str(project) if project else "Open a local folder to begin",
                 bg=theme.BACKGROUND, fg=theme.MUTED,
                 font=(theme.CODE_FONT, 10)).pack(anchor="w")

        actions = tk.Frame(self.overview_tab, bg=theme.BACKGROUND)
        actions.pack(fill="x", padx=30, pady=(0, 20))
        self._button(actions, "Analyze Project", self._start_analyze, accent=True).pack(
            side="left", padx=4)
        self._button(actions, "Open Architecture",
                     lambda: self._open_graph(ViewKind.ARCHITECTURE)).pack(side="left", padx=4)
        self._button(actions, "Open Training Flow",
                     lambda: self._open_graph(ViewKind.EXECUTION)).pack(side="left", padx=4)
        self._button(actions, "Browse Symbols",
                     lambda: self._set_activity("Symbols")).pack(side="left", padx=4)
        self._button(actions, "Analysis Issues", self._open_problems).pack(
            side="left", padx=4)

        analysis = self.controller.analysis
        if analysis is None or project is None:
            tk.Label(self.overview_tab,
                     text="Static analysis is explicit. Open a folder, then choose Analyze Project.",
                     bg=theme.BACKGROUND, fg=theme.MUTED,
                     font=(theme.UI_FONT, 11)).pack(anchor="w", padx=35, pady=25)
            return
        overview = build_overview(analysis, project)
        health = overview.health
        summary = tk.Frame(self.overview_tab, bg=theme.BACKGROUND)
        summary.pack(fill="x", padx=34)
        self._card(summary, "LANGUAGE / FRAMEWORK",
                   f"{overview.language}  /  {overview.framework or 'Generic Python'}").pack(
                       side="left", fill="x", expand=True, padx=5)
        self._card(summary, "ANALYSIS HEALTH",
                   f"{health.files_parsed}/{health.files_total} files · {health.issue_count} issues\n"
                   f"Exact {health.exact}  ·  Probable {health.probable}  ·  "
                   f"Unresolved {health.unresolved}").pack(
                       side="left", fill="x", expand=True, padx=5)
        columns = tk.Frame(self.overview_tab, bg=theme.BACKGROUND)
        columns.pack(fill="both", expand=True, padx=34, pady=24)
        self._choice_card(columns, "MODELS", overview.models, "No model detected",
                          ViewKind.ARCHITECTURE).pack(
                              side="left", fill="both", expand=True, padx=5)
        self._choice_card(columns, "ENTRY POINTS", overview.entries,
                          "No entry point detected", ViewKind.EXECUTION).pack(
                              side="left", fill="both", expand=True, padx=5)
        areas = ", ".join(overview.main_areas) or "Project root"
        tk.Label(self.overview_tab, text=f"Main areas  ·  {areas}", bg=theme.BACKGROUND,
                 fg=theme.MUTED, font=(theme.UI_FONT, 10)).pack(anchor="w", padx=40, pady=16)

    def _card(self, parent: Any, title: str, body: str) -> Any:
        frame = self.tk.Frame(parent, bg=theme.PANEL, highlightbackground=theme.BORDER,
                              highlightthickness=1, padx=18, pady=16)
        self.tk.Label(frame, text=title, bg=theme.PANEL, fg=theme.ACCENT,
                      font=(theme.UI_FONT, 10, "bold")).pack(anchor="w")
        self.tk.Label(frame, text=body, bg=theme.PANEL, fg=theme.TEXT,
                      justify="left", anchor="nw", wraplength=250,
                      font=(theme.UI_FONT, 11)).pack(anchor="w", pady=(11, 0))
        return frame

    def _open_problems(self) -> None:
        analysis = self.controller.analysis
        if analysis is None:
            self.status_label.configure(text="Analyze the project before opening issues")
            return
        if self._problems_tab is None:
            frame = self.tk.Frame(self.main_tabs, bg=theme.BACKGROUND)
            self.tk.Label(frame, text="ANALYSIS ISSUES", bg=theme.BACKGROUND,
                          fg=theme.ACCENT, anchor="w", padx=18, pady=15,
                          font=(theme.UI_FONT, 12, "bold")).pack(fill="x")
            tree = self.ttk.Treeview(frame, columns=("code", "location", "message"),
                                     show="headings", selectmode="browse")
            for column, width in (("code", 180), ("location", 220), ("message", 560)):
                tree.heading(column, text=column.upper())
                tree.column(column, width=width, stretch=column == "message")
            tree.pack(fill="both", expand=True, padx=15, pady=12)
            tree.bind("<Double-Button-1>", self._activate_problem)
            tree.bind("<Return>", self._activate_problem)
            self.problems_tree = tree
            self._problems_tab = frame
            self.main_tabs.add(frame, text="Problems")
        self.problems_tree.delete(*self.problems_tree.get_children())
        self._problem_anchors.clear()
        for issue in build_problems(analysis):
            item = self.problems_tree.insert(
                "", "end", values=(issue.code,
                                   f"{issue.file_path}:{issue.line or 1}", issue.message))
            try:
                self._problem_anchors[item] = SourceAnchor(issue.file_path, issue.line or 1)
            except ValueError:
                pass
        self.main_tabs.select(self._problems_tab)

    def _activate_problem(self, _event: Any) -> None:
        selected = self.problems_tree.selection()
        if selected and selected[0] in self._problem_anchors:
            self._open_source(self._problem_anchors[selected[0]], False)

    def _choice_card(self, parent: Any, title: str,
                     choices: tuple[OverviewChoice, ...], empty: str,
                     kind: ViewKind) -> Any:
        frame = self.tk.Frame(parent, bg=theme.PANEL,
                              highlightbackground=theme.BORDER,
                              highlightthickness=1, padx=18, pady=16)
        self.tk.Label(frame, text=title, bg=theme.PANEL, fg=theme.ACCENT,
                      font=(theme.UI_FONT, 10, "bold")).pack(anchor="w")
        if not choices:
            self.tk.Label(frame, text=empty, bg=theme.PANEL, fg=theme.MUTED,
                          font=(theme.UI_FONT, 11)).pack(anchor="w", pady=(11, 0))
        for choice in choices:
            self._button(frame, choice.name,
                         lambda selected=choice.id: self._open_graph(kind, selected)).pack(
                             fill="x", pady=(9, 0))
        return frame

    def _open_graph(self, kind: ViewKind, root_id: str | None = None) -> None:
        if self.controller.analysis is None:
            self.status_label.configure(text="Analyze the project before opening a graph")
            return
        if root_id != self._roots.get(kind):
            self._selected_id = None
        try:
            view = self.controller.view(kind, root_id, self._selected_id)
        except (ValueError, RuntimeError) as error:
            self.messagebox.showerror("Cannot open view", str(error))
            return
        self._views[kind] = view
        self._roots[kind] = root_id
        self._active_kind = kind
        if kind not in self._graph_tabs:
            frame = self.tk.Frame(self.main_tabs, bg=theme.BACKGROUND)
            bar = self.tk.Frame(frame, bg=theme.PANEL)
            bar.pack(fill="x")
            self._button(bar, "← Back", self._go_back).pack(side="left", padx=8, pady=5)
            self.tk.Label(bar, text="Double-click or Enter to drill down · source opens separately",
                          bg=theme.PANEL, fg=theme.MUTED,
                          font=(theme.UI_FONT, 9)).pack(side="left", padx=12)
            graph = GraphCanvas(frame, on_select=self._on_select,
                                on_drill_down=self._on_drill_down, on_move=self._on_move)
            graph.widget.pack(fill="both", expand=True)
            self._graph_tabs[kind] = (frame, graph)
            self.main_tabs.add(frame, text="Architecture" if kind == ViewKind.ARCHITECTURE
                               else "Training Flow")
        frame, graph = self._graph_tabs[kind]
        graph.render(view, self._selected_id)
        self.main_tabs.select(frame)
        self.status_label.configure(
            text=f"{kind.value} · {len(view.nodes)} visible nodes · "
                 f"{view.hidden_counts.get('nodes', 0)} hidden · "
                 f"{len(view.edges)} relations")
        if self._selected_id is not None:
            self._update_inspector()

    def _tab_changed(self, _event: Any) -> None:
        selected = self.main_tabs.select()
        for kind, (frame, _graph) in self._graph_tabs.items():
            if str(frame) == selected:
                self._active_kind = kind
                return
        self._active_kind = None

    def _on_select(self, node_id: str) -> None:
        self._selected_id = node_id
        self._update_inspector()

    def _on_drill_down(self, node_id: str) -> None:
        kind = self._active_kind
        view = self._views.get(kind) if kind is not None else None
        if kind is None or view is None:
            return
        node = next((item for item in view.nodes if item.id == node_id), None)
        if node is None:
            return
        if node.metadata.get("has_children"):
            self.controller.expand(node_id)
            self._selected_id = node_id
            self._open_graph(kind, self._roots.get(kind))
        elif node.source_anchors:
            self._open_source(node.source_anchors[0], False)

    def _go_back(self) -> None:
        kind = self._active_kind
        if kind is None or not self.controller.view_state.expanded_ids:
            self.main_tabs.select(self.overview_tab)
            return
        self.controller.collapse(self.controller.view_state.expanded_ids[-1])
        self._selected_id = None
        self._open_graph(kind, self._roots.get(kind))

    def _on_move(self, node_id: str, point: tuple[float, float]) -> None:
        self.controller.move_node(node_id, point)
        kind = self._active_kind
        if kind is not None:
            self._open_graph(kind, self._roots.get(kind))

    def _update_inspector(self) -> None:
        kind = self._active_kind
        if (kind is None or self._selected_id is None or self.controller.analysis is None
                or kind not in self._views):
            return
        try:
            inspector = build_inspector(self._views[kind], self._selected_id,
                                        self.controller.analysis)
        except StopIteration:
            self._set_inspector_message("Select a visible node to inspect its source.")
            return
        lines = ["OVERVIEW", inspector.display_name,
                 f"Code name  ·  {inspector.code_name or '—'}",
                 f"Role  ·  {inspector.role or 'Generic'}",
                 f"Confidence  ·  {inspector.confidence.value}", ""]
        if inspector.evidence:
            lines.extend(("WHY THIS MATCHED", *inspector.evidence, ""))
        lines.extend(("STRUCTURE / FLOW",
                      f"{len(inspector.children)} child components",
                      f"{len(inspector.member_ids)} aggregated members", ""))
        lines.append("RELATIONS")
        lines.extend(f"{item.direction} · {item.type.value} · {item.other_id}"
                     for item in inspector.relations)
        if not inspector.relations:
            lines.append("No visible local relations")
        lines.extend(("", "DATA"))
        lines.extend(f"{key}  ·  {value}" for key, value in inspector.data)
        if not inspector.data:
            lines.append("No static data details")
        lines.extend(("", "SOURCE"))
        for anchor in inspector.source_anchors:
            lines.append(f"{anchor.file_path}:{anchor.start_line}–{anchor.end_line}")
        if inspector.source_anchors:
            try:
                document = self.controller.read_source(inspector.source_anchors[0])
                source_lines = document.text.splitlines()
                start = max(0, document.start_line - 2)
                end = min(len(source_lines), document.end_line + 1)
                lines.extend(f"{index + 1:>4}  {source_lines[index]}"
                             for index in range(start, end))
            except (OSError, ValueError):
                lines.append("Source is no longer available")
        else:
            lines.append("Synthetic group · inspect a member for source")
        self.inspector_text.configure(state="normal")
        self.inspector_text.delete("1.0", "end")
        self.inspector_text.insert("end", "\n".join(lines))
        self.inspector_text.configure(state="disabled")
        button_state = "normal" if inspector.can_open_source else "disabled"
        self.source_button.configure(state=button_state)
        self.side_button.configure(state=button_state)

    def _open_selected_source(self, to_side: bool) -> None:
        kind = self._active_kind
        if kind is None or self._selected_id is None or self.controller.analysis is None:
            return
        inspector = build_inspector(self._views[kind], self._selected_id,
                                    self.controller.analysis)
        if inspector.source_anchors:
            self._open_source(inspector.source_anchors[0], to_side)

    def _open_source(self, anchor: SourceAnchor, to_side: bool) -> None:
        try:
            document = self.controller.read_source(anchor)
        except (OSError, ValueError, RuntimeError) as error:
            self.messagebox.showerror("Cannot open source", str(error))
            return
        tabs = self.side_tabs if to_side else self.main_tabs
        if to_side and not self._side_open:
            self.editor_panes.add(self.side_tabs, minsize=350)
            self._side_open = True
        key = (document.file_path, to_side)
        if key not in self._source_tabs:
            frame = self.tk.Frame(tabs, bg=theme.BACKGROUND)
            text = self.tk.Text(frame, bg=theme.BACKGROUND, fg=theme.TEXT,
                                insertbackground=theme.ACCENT, relief="flat",
                                padx=18, pady=14, wrap="none",
                                font=(theme.CODE_FONT, 10))
            scrollbar = self.tk.Scrollbar(frame, command=text.yview)
            text.configure(yscrollcommand=scrollbar.set)
            text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            self._source_tabs[key] = frame
            self._source_widgets[key] = text
            tabs.add(frame, text=Path(document.file_path).name)
        frame = self._source_tabs[key]
        self._fill_source(self._source_widgets[key], document)
        tabs.select(frame)

    def _fill_source(self, widget: Any, document: SourceDocument) -> None:
        lines = document.text.splitlines()
        rendered = "\n".join(f"{index:>5}  {line}" for index, line in enumerate(lines, 1))
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", rendered)
        widget.tag_configure("focus", background=theme.SURFACE_SELECTED,
                             foreground=theme.TEXT)
        widget.tag_add("focus", f"{document.start_line}.0",
                       f"{document.end_line + 1}.0")
        widget.see(f"{document.start_line}.0")
        widget.configure(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    CodeVizApp().run()
