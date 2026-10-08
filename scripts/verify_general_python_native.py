"""Verify general Python UI in an existing real WebView2 host over CDP.

Start scripts/general_python_smoke_host.py separately after building desktop assets.
This script attaches only; its caller owns the native host's lifecycle.
"""

import argparse
import ast
import json
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.native_source_assertions import (
        anchor_from_card,
        capture_screenshot,
        expect_source_anchor,
    )
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    from native_source_assertions import (
        anchor_from_card,
        capture_screenshot,
        expect_source_anchor,
    )


class NativeAcceptance:
    def __init__(self, page, project, output, report, expect):
        self.page = page
        self.project = project
        self.output = output
        self.report = report
        self.expect = expect

    def inspection(self, stage):
        """Wait for rendered state and record DOM before choosing controls."""
        self.report["stage"] = stage
        self.page.wait_for_load_state("networkidle", timeout=30_000)
        self.page.locator(".desktop-shell").wait_for(state="visible")
        self.expect(self.page.locator(".statusbar")).to_contain_text("Python bridge")
        (self.output / f"{stage}.html").write_text(self.page.content(), encoding="utf-8")
        controls = self.page.locator("button, input, select").evaluate_all("""elements =>
            elements.map(e => ({tag: e.tagName, text: e.textContent?.trim(),
                label: e.getAttribute('aria-label'), class: e.className,
                placeholder: e.getAttribute('placeholder'), disabled: e.disabled}))""")
        self.report.setdefault("domControls", {})[stage] = controls

    def screenshot(self, name):
        path = self.output / f"{name}.png"
        capture_screenshot(self.page, path=str(path), full_page=True)
        self.report.setdefault("screenshots", []).append(str(path))

    def checked(self, name):
        self.report["checks"].append(name)

    def read_bridge(self, method):
        allowed = {"get_session", "get_overview", "get_preferences",
                   "get_cache_info", "get_view_state"}
        assert method in allowed, "only read-only bridge assertions are permitted"
        response = self.page.evaluate("method => window.pywebview.api[method]()", method)
        assert response.get("ok"), response
        return response["data"]

    def session_for(self, project, analyzed=False):
        wanted = str(project.resolve()).replace("\\", "/").casefold()
        return self.wait_bridge(
            "get_session",
            lambda session: isinstance(session.get("projectPath"), str)
            and session["projectPath"].replace("\\", "/").casefold() == wanted
            and (not analyzed or session.get("status") in {"Ready", "Partial"}),
            f"project {project} {'analyzed' if analyzed else 'opened'}", timeout=45)

    def wait_bridge(self, method, predicate, description, timeout=20):
        # page.evaluate awaits the API Promise. wait_for_function's polling
        # predicate must not return an unresolved Promise (always truthy).
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.read_bridge(method)
            if predicate(last):
                return last
            self.page.wait_for_timeout(100)
        raise AssertionError(f"Timed out waiting for {description}; last {method}: {last}")

    def activity(self, name):
        labels = {"overview": r"^(Overview|项目概览)$", "search": r"^(Search|搜索)$",
                  "settings": r"^(Settings|设置)$"}
        button = self.page.locator(".activity").get_by_role("button", name=re.compile(labels[name]))
        self.expect(button).to_have_count(1)
        if name == "settings" or "active" not in (button.get_attribute("class") or "").split():
            button.click()

    def open_project_ui(self, project, stage):
        self.activity("overview")
        self.inspection(stage)
        resolved = str(project.resolve())
        recent = self.page.locator(".recent-project")
        matches = [index for index in range(recent.count())
                   if (recent.nth(index).get_attribute("title") or "").casefold() == resolved.casefold()]
        assert len(matches) <= 1, f"duplicate rendered Recent Projects for {resolved}"
        if matches:
            button = recent.nth(matches[0])
            self.expect(button).to_be_visible()
            self.expect(button).to_be_enabled()
            button.click()
            self.expect(recent.nth(matches[0])).to_be_enabled(timeout=45_000)
            self.report.setdefault("projectSwitches", []).append(
                {"project": resolved, "control": "recent-project", "stage": stage})
            session = self.session_for(project)
            self.expect(self.page.locator(".titlebar-path")).to_have_text(session["projectPath"])
            return session
        forms = self.page.locator(".path-form").filter(has=self.page.get_by_role("textbox"))
        if forms.count() != 1:
            raise AssertionError(
                f"No rendered recent-project button for {resolved}, and no single project path form. "
                "Start general_python_smoke_host.py with its empty workspace and seeded recents; "
                "no bridge bypass used.")
        form = forms.first
        field = form.get_by_role("textbox")
        self.expect(field).to_be_visible()
        field.fill(str(project.resolve()))
        button = form.get_by_role("button")
        self.expect(button).to_have_count(1)
        self.expect(button).to_be_enabled()
        button.click()
        self.report.setdefault("projectSwitches", []).append(
            {"project": resolved, "control": "path-form", "stage": stage})
        session = self.session_for(project)
        self.expect(self.page.locator(".titlebar-path")).to_have_text(session["projectPath"])
        return session

    def analyze_ui(self):
        self.inspection("project-opened")
        # Sidebar re-analyze is intentionally usable for a fresh project too.
        buttons = self.page.locator(".sidebar-project").get_by_role(
            "button", name=re.compile(r"^(Re-analyze|重新分析|Analyze project|分析项目)$"))
        if not buttons.count():
            buttons = self.page.locator(".primary-editor").get_by_role(
                "button", name=re.compile(r"^(Analyze project|分析项目)$"))
        self.expect(buttons).to_have_count(1)
        self.expect(buttons).to_be_enabled()
        buttons.click()
        # A cached Ready/Partial snapshot can precede this new analysis. Wait
        # for rendered busy controls to finish before accepting that snapshot.
        self.expect(buttons).to_be_enabled(timeout=45_000)
        self.report["analyzedSession"] = self.session_for(self.project, analyzed=True)
        self.expect(self.page.locator(".analysis-progress")).to_have_count(0)

    def overview(self):
        self.activity("overview")
        tab = self.page.get_by_role("tab", name=re.compile(r"^(Overview|项目概览)$"))
        if tab.count():
            tab.click()
            self.expect(tab).to_have_attribute("aria-selected", "true")
        else:
            self.page.locator(".view-link").filter(has_text=re.compile(r"(Overview|项目概览)")).click()
        self.expect(self.page.locator(".overview-page h1")).to_have_text(self.project.name)

    def function_row(self, qualified_name):
        rows = self.page.locator(".overview-card .entity-row").filter(has_text=qualified_name)
        self.expect(rows).to_have_count(1)
        return rows

    def graph_tab(self):
        tab = self.page.get_by_role("tab", name=re.compile(r"^(Function flow|函数流程)$"))
        self.expect(tab).to_have_count(1)
        tab.click()
        self.expect(self.page.locator(".graph-editor")).to_be_visible()
        return tab

    def open_source(self, scope, filename, line, text):
        cards = self.page.locator(".inspector .anchor-card").filter(
            has_text=re.compile(rf"{re.escape(filename)}:{line}–"))
        self.expect(cards).to_have_count(1)
        self.expect(cards.get_by_role("button").first).to_be_enabled()
        anchor = anchor_from_card(cards)
        assert anchor[:2] == (filename, line), anchor
        cards.get_by_role("button").first.click()
        expect_source_anchor(self.page, scope, *anchor, text)

    def check_flow_source(self, assignment_line, assignment_text):
        self.overview()
        self.inspection("overview")
        overview = self.read_bridge("get_overview")
        names = {item["name"] for item in overview.get("functions", [])}
        assert {"helpers.native_normalize_record", "helpers.native_format_summary",
                "helpers.NativeReportBuilder.native_build_report", "helpers.native_async_preview"} <= names, names
        self.function_row("helpers.native_normalize_record").click()
        tab = self.graph_tab()
        self.expect(tab).to_have_attribute("aria-selected", "true")
        state = self.wait_bridge("get_view_state",
                                 lambda state: state.get("activeView") == "FunctionFlow",
                                 "persisted FunctionFlow tab")
        assert state.get("activeView") == "FunctionFlow", state
        self.inspection("function-flow")
        step = self.page.locator('.code-node[data-role="FlowStep"]').filter(
            has=self.page.locator("strong").get_by_text("Assign", exact=True))
        self.expect(step).to_have_count(1)
        fit = self.page.locator(".graph-toolbar").get_by_role(
            "button", name=re.compile(r"^(Fit canvas|适应画布)$"))
        if fit.count():
            fit.click()
        step.click()
        self.expect(self.page.locator(".inspector h2")).to_have_text("Assign")
        self.screenshot("function-flow-inspector")
        self.open_source(".primary-editor", "helpers.py", assignment_line, assignment_text)
        self.screenshot("assignment-source")
        source_tab = self.page.locator(".tab.active")
        self.expect(source_tab.get_by_role("tab")).to_have_text("helpers.py")
        source_tab.locator(".close-tab").click()
        self.graph_tab()
        self.checked("Overview ordinary catalog -> FunctionFlow -> Assign Inspector -> actual highlighted source")
        self.checked("source tab open/close restores graph tab")

    def check_search_split(self, definition_line, definition_text):
        self.activity("search")
        self.inspection("search")
        query = self.page.locator(".search-input").get_by_role("textbox")
        self.expect(query).to_be_enabled()
        query.fill("native_format_summary")
        result = self.page.locator(".search-result").filter(
            has=self.page.locator("small", has_text=re.compile(r"^helpers\.native_format_summary$")))
        self.expect(result).to_have_count(1)
        self.expect(result).to_contain_text(f"helpers.py:{definition_line}")
        result.get_by_role("button").first.click()
        self.expect(self.page.locator(".inspector code").first).to_have_text("helpers.native_format_summary")
        self.inspection("search-revealed")
        self.open_source(".primary-editor", "helpers.py", definition_line, definition_text)
        self.screenshot("search-source")
        self.page.locator(".tab.active .close-tab").click()
        # Search reveal may target Symbol or FunctionFlow; return via selected tab.
        result.get_by_role("button").first.click()
        self.expect(self.page.locator(".inspector code").first).to_have_text("helpers.native_format_summary")
        card = self.page.locator(".inspector .anchor-card").filter(
            has_text=re.compile(rf"helpers\.py:{definition_line}–"))
        self.expect(card).to_have_count(1)
        anchor = anchor_from_card(card)
        assert anchor[:2] == ("helpers.py", definition_line), anchor
        card.get_by_role("button").nth(1).click()
        expect_source_anchor(self.page, ".side-editor", *anchor, definition_text)
        self.expect(self.page.locator(".primary-editor .graph-editor")).to_be_visible()
        self.screenshot("split-source")
        self.page.locator(".side-title").get_by_role("button").click()
        self.expect(self.page.locator(".side-editor")).to_have_count(0)
        self.checked("ordinary-name search -> reveal correct definition/source -> split and close")

    def check_language(self):
        self.activity("settings")
        self.inspection("settings")
        language = self.page.locator("#language")
        self.expect(language).to_be_visible()
        original = language.input_value()
        self.report["originalLanguage"] = original
        try:
            for value in ("en", "zh-CN"):
                language.select_option(value)
                self.expect(self.page.locator("html")).to_have_attribute("lang", value)
                self.wait_bridge("get_preferences", lambda prefs, expected=value: prefs.get("language") == expected,
                                 f"persisted language {value}")
                self.screenshot(f"settings-{value}")
        finally:
            language.select_option(original)
            self.expect(self.page.locator("html")).to_have_attribute("lang", original)
            self.wait_bridge("get_preferences", lambda prefs: prefs.get("language") == original,
                             f"restored language {original}")
        self.checked("language switches persist through Python and original language is restored")

    def check_cache_reopen(self, previous):
        info = self.read_bridge("get_cache_info")
        self.report["cacheBeforeReopen"] = info
        assert info.get("exists"), "native host must save analyzed fixture to its cache"
        self.overview()
        self.function_row("helpers.native_normalize_record").click()
        self.graph_tab()
        state = self.wait_bridge(
            "get_view_state", lambda state: state.get("activeView") == "FunctionFlow"
            and bool(state.get("graphRoots", {}).get("FunctionFlow")),
            "persisted FunctionFlow root")
        if not previous or Path(previous).resolve() == self.project:
            self.report["skipped"].append("cache reopen: no different original or seeded recent project available")
            return
        self.open_project_ui(Path(previous), "cache-switch-away")
        session = self.open_project_ui(self.project, "cache-reopen")
        assert session["status"] in {"Ready", "Partial"}, session
        assert not session.get("cacheIssue"), session
        restored = self.read_bridge("get_view_state")
        assert restored.get("graphRoots", {}).get("FunctionFlow") == state["graphRoots"]["FunctionFlow"], restored
        self.graph_tab()
        self.expect(self.page.locator('.code-node[data-role="Function"]')).to_contain_text("native_normalize_record")
        self.report["reopenedSession"] = session
        self.screenshot("cache-reopened-function-flow")
        self.checked("UI switches away and reopens analyzed cache without re-analysis; FunctionFlow root restored")


def cache_switch_project(session, project):
    """Choose another rendered recent even when a retry starts on this fixture."""
    current = project.resolve()
    candidates = [session.get("projectPath"), *session.get("recentProjects", [])]
    return next((path for path in candidates if path and Path(path).resolve() != current), None)


def fixture_lines(project):
    """Read/parse fixture text for expected lines; never import or run it."""
    path = project / "helpers.py"
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    definitions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    normalize = definitions["native_normalize_record"]
    assignment = next(node for node in normalize.body if isinstance(node, ast.Assign))
    definition = definitions["native_format_summary"]
    return assignment.lineno, lines[assignment.lineno - 1], definition.lineno, lines[definition.lineno - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9237, help="Existing native host's CDP port")
    parser.add_argument("--project", type=Path,
                        default=Path(__file__).resolve().parents[1] / "tests/fixtures/general_python_project",
                        help="Own-written fixture project to open through rendered controls")
    parser.add_argument("--output", type=Path, default=Path("build/general-python-native"),
                        help="Directory for JSON report, DOM snapshots and screenshots")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"startedAt": datetime.now(UTC).isoformat(), "status": "failed",
              "project": str(args.project.resolve()), "port": args.port,
              "checks": [], "skipped": [], "errors": [], "pageErrors": [], "consoleErrors": []}
    browser = None
    page = None
    try:
        # Keep --help usable without importing Playwright or opening a connection.
        from playwright.sync_api import expect, sync_playwright

        expected = fixture_lines(args.project.resolve())
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}", timeout=20_000)
                pages = [candidate for context in browser.contexts for candidate in context.pages]
                for candidate in pages:
                    candidate.wait_for_load_state("networkidle", timeout=30_000)
                    if candidate.locator(".desktop-shell").count():
                        page = candidate
                        break
                assert page is not None, "CDP endpoint has no rendered CodeViz desktop page"
                page.set_default_timeout(20_000)
                page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
                page.on("console", lambda message: report["consoleErrors"].append(message.text)
                        if message.type == "error" else None)
                page.wait_for_function("Boolean(window.pywebview?.api?.get_session)")
                runner = NativeAcceptance(page, args.project.resolve(), output, report, expect)
                runner.inspection("initial-native")
                runner.screenshot("initial-native")
                session = runner.read_bridge("get_session")
                report["initialSession"] = session
                previous = cache_switch_project(session, runner.project)
                runner.open_project_ui(runner.project, "switch-project")
                runner.analyze_ui()
                runner.check_flow_source(*expected[:2])
                runner.check_search_split(*expected[2:])
                runner.check_language()
                runner.check_cache_reopen(previous)
                expect(page.locator(".error-banner, .inline-error")).to_have_count(0)
                assert not report["pageErrors"], report["pageErrors"]
                assert not report["consoleErrors"], report["consoleErrors"]
                report["status"] = "passed"
            except Exception as error:
                report["errors"].append(f"{type(error).__name__}: {error}")
                report["traceback"] = traceback.format_exc()
                if page is not None and not page.is_closed():
                    try:
                        capture_screenshot(page, path=str(output / "failure.png"), full_page=True)
                        report.setdefault("screenshots", []).append(str(output / "failure.png"))
                        (output / "failure.html").write_text(page.content(), encoding="utf-8")
                        report["failureDomText"] = page.locator("body").inner_text()[:12000]
                    except Exception as capture_error:
                        report["errors"].append(f"Failure capture: {capture_error}")
            finally:
                if browser is not None:
                    # CDP browser.close disconnects this client; do not close
                    # pages/contexts or terminate the caller-owned native host.
                    browser.close()
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()
    report["finishedAt"] = datetime.now(UTC).isoformat()
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"],
                      "skipped": report["skipped"], "errors": report["errors"],
                      "report": str(path)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
