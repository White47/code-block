"""Attach to a caller-owned native host; verify long-lived real folder pickers.

No host launch/termination, direct open/analyze RPC, source edits or cache seeding.
Requires Windows, existing Playwright, and two absolute fixture project paths.
"""

import argparse
import ctypes
import json
import re
import sys
import time
import traceback
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path


class OwnedPicker:
    """Fail closed unless a unique common file dialog belongs to the given PID."""

    def __init__(self, pid):
        self.pid = pid
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetDlgCtrlID": ([wintypes.HWND], ctypes.c_int),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowEnabled": ([wintypes.HWND], wintypes.BOOL),
            "EnumWindows": ([self.callback, wintypes.LPARAM], wintypes.BOOL),
            "EnumChildWindows": ([wintypes.HWND, self.callback, wintypes.LPARAM], wintypes.BOOL),
            "PostMessageW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            "SendMessageTimeoutW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                     ctypes.POINTER(ctypes.c_size_t)], ctypes.c_size_t),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.user, name)
            method.argtypes, method.restype = arguments, result

    def info(self, handle):
        owner = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        text, kind = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(256)
        self.user.GetWindowTextW(handle, text, len(text))
        self.user.GetClassNameW(handle, kind, len(kind))
        return {"handle": int(handle), "pid": owner.value, "title": text.value,
                "class": kind.value, "controlId": self.user.GetDlgCtrlID(handle),
                "visible": bool(self.user.IsWindowVisible(handle)),
                "enabled": bool(self.user.IsWindowEnabled(handle))}

    def windows(self):
        result = []

        @self.callback
        def collect(handle, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == self.pid:
                result.append(self.info(handle))
            return True

        assert self.user.EnumWindows(collect, 0), "native window enumeration failed"
        return result

    def dialogs(self):
        return [window for window in self.windows()
                if window["class"] == "#32770" and window["visible"]]

    def validated(self):
        dialogs = self.dialogs()
        assert len(dialogs) == 1, f"unexpected dialog set for PID {self.pid}: {dialogs}"
        dialog = dialogs[0]
        children = []

        @self.callback
        def collect(handle, _):
            children.append(self.info(handle))
            return True

        self.user.EnumChildWindows(dialog["handle"], collect, 0)
        owned = [item for item in children if item["pid"] == self.pid and item["visible"]]
        edits = [item for item in owned if item["class"] == "Edit"
                 and item["controlId"] in (1148, 1152) and item["enabled"]]
        buttons = {}
        for control_id in (1, 2):
            matches = [item for item in owned if item["class"] == "Button"
                       and item["controlId"] == control_id and item["enabled"]]
            assert len(matches) == 1, f"unexpected picker button {control_id}: {children}"
            buttons[control_id] = matches[0]
        assert len(edits) == 1, f"unexpected filename controls: {children}"
        return {**dialog, "children": children}, edits[0], buttons

    def respond(self, project=None):
        # Re-enumerate immediately before every native write; never act on a
        # remembered handle after another dialog appears or ownership changes.
        dialog, edit, buttons = self.validated()
        if project is not None:
            path = project.resolve(strict=True)
            assert path.is_dir(), path
            text = ctypes.create_unicode_buffer(str(path))
            result = ctypes.c_size_t()
            sent = self.user.SendMessageTimeoutW(
                edit["handle"], 0x000C, 0, ctypes.cast(text, ctypes.c_void_p).value,
                0x0002, 2000, ctypes.byref(result))  # WM_SETTEXT, abort if hung.
            assert sent and result.value, "setting owned picker filename failed"
            current, _, buttons = self.validated()
            assert current["handle"] == dialog["handle"], "picker changed before confirmation"
        button = buttons[1 if project is not None else 2]
        assert self.info(button["handle"])["pid"] == self.pid
        assert self.user.PostMessageW(button["handle"], 0x00F5, 0, 0), "owned button click failed"
        return dialog


class PickerAcceptance:
    def __init__(self, runner, native):
        self.runner, self.native = runner, native

    def choose_button(self):
        button = self.runner.page.locator(".sidebar-project").get_by_role(
            "button", name=re.compile(r"^\s*[＋+]?[ ]*(Open project|打开项目)\s*$"))
        self.runner.expect(button).to_have_count(1)
        return button

    def open_dialog(self, stage, hold_seconds):
        runner, page = self.runner, self.runner.page
        runner.activity("overview")
        runner.inspection(stage)
        assert not self.native.dialogs(), "unexpected existing dialog; no native action taken"
        button = self.choose_button()
        runner.expect(button).to_be_enabled()
        button.click(no_wait_after=True)
        deadline = time.monotonic() + 10
        while not self.native.dialogs() and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        dialog, _, _ = self.native.validated()
        runner.report.setdefault("dialogs", []).append({"stage": stage, **dialog})
        start = time.monotonic()
        while time.monotonic() - start < hold_seconds:
            current, _, _ = self.native.validated()
            assert current["handle"] == dialog["handle"], "dialog replaced while waiting"
            runner.expect(button).to_be_disabled()
            assert "BRIDGE_TIMEOUT" not in page.locator("body").inner_text()
            page.wait_for_timeout(250)
        runner.expect(button).to_be_disabled()
        assert "BRIDGE_TIMEOUT" not in page.locator("body").inner_text()
        runner.report.setdefault("pickerWaits", []).append(
            {"stage": stage, "seconds": time.monotonic() - start, "busy": True})
        runner.screenshot(stage + "-busy")

    def finish_dialog(self, project, stage):
        runner, page = self.runner, self.runner.page
        original = self.native.respond(project)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            # Cancel/confirm can hide children between Win32 observations.
            # Closing is read-only: validate the visible top-level identity,
            # while respond() retains strict live-control checks before writes.
            dialogs = self.native.dialogs()
            if not dialogs:
                break
            assert len(dialogs) == 1, "unexpected follow-up dialog set; no action taken"
            current = dialogs[0]
            assert (current["handle"] == original["handle"]
                    and current["pid"] == original["pid"] == self.native.pid
                    and current["class"] == original["class"] == "#32770"
                    and current["visible"]), "unexpected follow-up dialog; no action taken"
            page.wait_for_timeout(100)
        assert not self.native.dialogs(), "folder dialog did not close"
        runner.expect(self.choose_button()).to_be_enabled(timeout=45_000)
        session = runner.session_for(project or runner.project)
        runner.report.setdefault("sessions", []).append({"stage": stage, **session})
        runner.expect(page.locator(".titlebar-path")).to_have_text(session["projectPath"])
        runner.screenshot(stage)
        return session

    def stale_selection(self, project, status, stage):
        """Observe missing/incompatible analysis without requesting unavailable views."""
        runner, page = self.runner, self.runner.page
        session = runner.session_for(project)
        assert session["status"] == status, session
        assert session.get("hasAnalysis") is False, session
        if status == "Outdated":
            assert any(re.search(r"version.*(changed|mismatch)", reason, re.IGNORECASE)
                       for reason in session.get("outdatedReasons", [])), session
        # Give restored old tabs and their effects a render window. Only read
        # session/view-state DTOs; never probe get_view/overview/problems here.
        page.wait_for_timeout(500)
        page.wait_for_load_state("networkidle")
        runner.expect(page.locator(".error-banner, .inline-error")).to_have_count(0)
        runner.expect(page.locator(".primary-editor .react-flow")).to_have_count(0)
        runner.expect(page.locator(".static-flow-note")).to_have_count(0)
        current = runner.session_for(project)
        assert current["status"] == status and current.get("hasAnalysis") is False, current
        state = runner.read_bridge("get_view_state")
        runner.report.setdefault("staleCacheCases", []).append(
            {"stage": stage, "session": current, "viewState": state,
             "note": "Old source tabs/IDs may remain stored; no unavailable graph RPC was sent by verifier."})
        runner.inspection(stage + "-rendered")
        runner.screenshot(stage + "-rendered")
        runner.checked(stage + ": hasAnalysis=false; restored UI has no errors or old FunctionFlow graph")

    def analyzed_available(self, project, stage):
        session = self.runner.session_for(project, analyzed=True)
        assert session.get("hasAnalysis") is True, session
        self.runner.report.setdefault("analysisAvailability", []).append({"stage": stage, **session})

    def run(self, project_a, project_b, expect_stale_cache_cases=False):
        runner = self.runner
        runner.inspection("initial")
        runner.report["initialSession"] = runner.read_bridge("get_session")
        self.open_dialog("first-picker-17s", 17)
        self.finish_dialog(project_a, "first-project-selected")
        runner.project = project_a
        if expect_stale_cache_cases:
            self.stale_selection(project_a, "NotAnalyzed", "first-missing-cache")
        runner.checked("picker remains busy beyond 17 seconds without BRIDGE_TIMEOUT; selects first project")
        for name in ("first-analysis", "first-reanalysis-1", "first-reanalysis-2"):
            runner.analyze_ui()
            if expect_stale_cache_cases:
                self.analyzed_available(project_a, name)
            runner.screenshot(name)
            runner.checked(name + ": rendered Analyze/Re-analyze hydrates Ready/Partial")
        self.open_dialog("cancel-picker-17s", 17)
        self.finish_dialog(None, "picker-cancelled")
        runner.session_for(project_a, analyzed=True)
        runner.analyze_ui()
        if expect_stale_cache_cases:
            self.analyzed_available(project_a, "reanalyzed-after-cancel")
        runner.screenshot("reanalyzed-after-cancel")
        runner.checked("17-second chooser cancellation keeps project unchanged and re-analysis usable")
        self.open_dialog("third-picker", 0)
        self.finish_dialog(project_b, "second-project-selected")
        runner.project = project_b
        if expect_stale_cache_cases:
            self.stale_selection(project_b, "Outdated", "second-version-mismatch-cache")
        runner.analyze_ui()
        if expect_stale_cache_cases:
            self.analyzed_available(project_b, "second-project-analyzed")
        cache = runner.read_bridge("get_cache_info")
        assert cache.get("exists") and not cache.get("cacheIssue"), cache
        runner.report["secondProjectCache"] = cache
        runner.screenshot("second-project-analyzed")
        runner.checked("third picker switches to second fixture; UI analysis saves cache")
        runner.open_project_ui(project_a, "cache-switch-away")
        session = runner.open_project_ui(project_b, "cache-reopen-second")
        assert session["status"] in {"Ready", "Partial"} and not session.get("cacheIssue"), session
        if expect_stale_cache_cases:
            assert session.get("hasAnalysis") is True, session
        runner.screenshot("second-project-cache-reopened")
        runner.checked("second fixture reopens via rendered Recent Projects using analyzed cache, without Analyze")
        runner.expect(runner.page.locator(".error-banner, .inline-error")).to_have_count(0)
        assert not runner.report["pageErrors"], runner.report["pageErrors"]
        assert not runner.report["consoleErrors"], runner.report["consoleErrors"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True, help="Existing real WebView2 CDP port")
    parser.add_argument("--pid", type=int, required=True, help="Caller-owned native Python host PID")
    parser.add_argument("--project-a", type=Path, required=True, help="Absolute first fixture project path")
    parser.add_argument("--project-b", type=Path, required=True, help="Absolute second fixture project path")
    parser.add_argument("--output", type=Path, default=Path("build/project-picker-native"))
    parser.add_argument("--expect-stale-cache-cases", action="store_true", help=(
        "Require caller-isolated seeds: first fixture missing analysis cache, second fixture "
        "version-mismatch cache; both retain old layouts and report hasAnalysis=false before Analyze"))
    args = parser.parse_args()
    if args.pid < 1 or not 1 <= args.port <= 65535:
        parser.error("positive PID and valid CDP port required")
    projects = [args.project_a, args.project_b]
    if not all(path.is_absolute() and path.is_dir() for path in projects):
        parser.error("both projects must be existing absolute directory paths")
    project_a, project_b = (path.resolve() for path in projects)
    if project_a == project_b:
        parser.error("two different fixture projects required")
    output = args.output.resolve()
    if any(output.is_relative_to(project) for project in (project_a, project_b)):
        parser.error("output must be outside both analyzed projects")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    report = {"startedAt": datetime.now(UTC).isoformat(), "status": "failed",
              "port": args.port, "pid": args.pid, "projects": [str(project_a), str(project_b)],
              "expectStaleCacheCases": args.expect_stale_cache_cases,
              "checks": [], "errors": [], "screenshots": [], "pageErrors": [], "consoleErrors": []}
    page = None
    try:
        # --help does not load Playwright or Win32, or touch a host/cache.
        from playwright.sync_api import expect, sync_playwright
        from verify_general_python_native import NativeAcceptance

        native = OwnedPicker(args.pid)
        assert native.windows(), "specified PID has no native windows"
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}", timeout=20_000)
            try:
                pages = []
                for context in browser.contexts:
                    for candidate in context.pages:
                        candidate.wait_for_load_state("networkidle", timeout=30_000)
                        if candidate.locator(".desktop-shell").count():
                            pages.append(candidate)
                assert len(pages) == 1, "CDP must expose exactly one real CodeViz page"
                page = pages[0]
                page.set_default_timeout(20_000)
                page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
                page.on("console", lambda message: report["consoleErrors"].append(message.text)
                        if message.type == "error" else None)
                page.wait_for_function("Boolean(window.pywebview?.api?.get_session)")
                runner = NativeAcceptance(page, project_a, output, report, expect)
                PickerAcceptance(runner, native).run(
                    project_a, project_b, args.expect_stale_cache_cases)
                report["status"] = "passed"
            except Exception as error:
                report["errors"].append(f"{type(error).__name__}: {error}")
                report["traceback"] = traceback.format_exc()
                report["ownedWindowsAtFailure"] = native.windows()
                if page is not None and not page.is_closed():
                    try:
                        page.screenshot(path=str(output / "failure.png"), full_page=True)
                        report["screenshots"].append(str(output / "failure.png"))
                    except Exception as capture_error:
                        report["errors"].append(f"failure screenshot: {capture_error}")
            finally:
                browser.close()  # CDP disconnect only; never close page/context or host.
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()
    report["finishedAt"] = datetime.now(UTC).isoformat()
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    # Reuse the reviewed atomic report writer; no direct writes to target files.
    from verify_general_python import write_report

    write_report(output / "report.json", serialized, (project_a, project_b))
    print(json.dumps({"status": report["status"], "checks": report["checks"],
                      "errors": report["errors"], "report": str(output / "report.json")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
