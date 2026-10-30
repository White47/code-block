"""Verify the real WebView2 assistant with an owned, fake-only fixture."""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    artifact = parser.add_mutually_exclusive_group(required=True)
    artifact.add_argument("--source", action="store_true")
    artifact.add_argument("--wheel-dir", type=Path)
    artifact.add_argument("--exe", type=Path)
    parser.add_argument("--fake-only", action="store_true", required=True)
    parser.add_argument("--deepseek", action="store_true",
                        help="Fixed production SDK with owned fake HTTP only")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _host_command(args, *, port, output):
    """Select the requested artifact only; never silently fall back to source."""
    host = Path(__file__).with_name("assistant_smoke_host.py").resolve()
    if args.source:
        command = [sys.executable, "-I", "-B", str(host)]
    elif args.exe is not None:
        executable = args.exe.resolve(strict=True)
        if not executable.is_file():
            raise FileNotFoundError("Requested frozen executable is missing")
        command = [str(executable), "--_codeviz-assistant-native-smoke"]
    else:
        environment = args.wheel_dir.resolve().parent / "assistant-wheel-native-env"
        executable = (environment / "Scripts" / "python.exe").resolve(strict=True)
        command = [str(executable), "-I", "-B", str(host)]
    mode = ["--deepseek"] if getattr(args, "deepseek", False) else []
    if args.source and mode:
        mode.append("--source-tree")
    return [*command, *mode, "--port", str(port), "--output", str(output)]


def _validate_wheel_install(wheel_dir, command):
    """Check installed bytes against the requested wheel in its own interpreter."""
    wheels = list(wheel_dir.resolve().glob("code_module_visualizer-*.whl"))
    if len(wheels) != 1:
        raise FileNotFoundError("Expected exactly one application wheel")
    probe = '''import hashlib, json, sys, zipfile
from pathlib import Path
import codeviz
from codeviz.ui.desktop import resolve_frontend_index
root = Path(codeviz.__file__).resolve().parent
environment = Path(sys.executable).resolve().parents[1]
assert root.is_relative_to(environment), "Wheel import escaped the isolated environment"
wheel = Path(sys.argv[1])
with zipfile.ZipFile(wheel) as archive:
    entries = [name for name in archive.namelist() if name.startswith("codeviz/") and not name.endswith("/")]
    assert entries
    for name in entries:
        installed = root.parent / name
        assert installed.is_file() and installed.read_bytes() == archive.read(name), "Stale wheel install"
index = resolve_frontend_index()
assert index.is_relative_to(root)
print(json.dumps({"wheelSHA256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "installedFilesChecked": len(entries), "isolatedImport": True, "localAssets": True}))
'''
    result = subprocess.run([command[0], "-I", "-B", "-c", probe, str(wheels[0])],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return json.loads(result.stdout)


def _close_native(pid):
    subprocess.run([sys.executable, str(Path(__file__).with_name("close_native_smoke.py")),
                    str(pid)], check=True, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _native_pid(output, process, port):
    receipt = json.loads((output / "host-start.json").read_text(encoding="utf-8"))
    pid, parent = receipt.get("pid"), receipt.get("parentPid")
    if (type(pid) is not int or pid <= 0 or type(parent) is not int
            or receipt.get("port") != port
            or pid != process.pid and parent != process.pid):
        raise ValueError("Native PID is not from this owned launch")
    return pid


def _checkpoint(output, report, stage):
    """Persist the current risky stage so a killed driver cannot leave an old run."""
    report["stage"] = stage
    temporary = output / "report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(temporary, output / "report.json")


def _owned_page_url(value, _cdp_port):
    try:
        parsed = urlsplit(value)
        # Ownership comes from this run's private CDP endpoint and host process.
        # pywebview's local asset server independently chooses its own port.
        return (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                and parsed.port is not None)
    except (TypeError, ValueError):
        return False


def _target_summary(target):
    return {name: str(target.get(name, ""))[:512]
            for name in ("id", "type", "title", "url")}


def _wait_for_native_cdp_target(port, process, *, timeout=45, opener=urlopen,
                                sleep=time.sleep, observe=None, observe_error=None):
    """Wait for the owned page target, not merely an early CDP listener."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"Owned WebView2 host exited before page target ({exit_code})")
        try:
            with opener(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                payload = json.load(response)
            if type(payload) is not list:
                raise ValueError("CDP target list was not a list")
            targets = [_target_summary(target) for target in payload
                       if type(target) is dict]
            if observe is not None:
                observe(targets)
            for target in targets:
                if target["type"] == "page" and _owned_page_url(target["url"], port):
                    return target
            last_error = None
        except Exception as error:
            last_error = type(error).__name__
            reason = getattr(error, "reason", None)
            if isinstance(reason, BaseException):
                last_error += f"/{type(reason).__name__}"
            if observe_error is not None:
                observe_error(last_error)
        sleep(.25)
    suffix = f"; last probe {last_error}" if last_error else ""
    raise RuntimeError(f"Owned WebView2 page target did not become ready{suffix}")


def _wait_for_native_page(browser, port, process, *, timeout=15, sleep=time.sleep):
    """Find the exact owned page across all contexts after CDP attachment."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"Owned WebView2 host exited after CDP attach ({exit_code})")
        for context in browser.contexts:
            for page in context.pages:
                try:
                    if _owned_page_url(page.url, port):
                        return page
                except Exception:
                    continue
        sleep(.1)
    raise RuntimeError("CDP attached but did not expose the owned WebView2 page")


class _OwnedNativeProcess:
    """Keep an OS handle pinned; a later recycled PID is never a kill target."""

    def __init__(self, kernel, handle, pid):
        self._kernel, self._handle, self.pid = kernel, handle, pid

    def poll(self):
        try:
            return self.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return None

    def wait(self, timeout):
        result = self._kernel.WaitForSingleObject(self._handle, int(timeout * 1000))
        if result == 0:
            return 0
        if result == 258:
            raise subprocess.TimeoutExpired("owned native host", timeout)
        import ctypes
        raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self._kernel.TerminateProcess(self._handle, 1):
            import ctypes
            raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def close(self):
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _claim_native_process(pid, launcher):
    """Pin only this launcher's actual child, never an arbitrary PID/tree."""
    if pid == launcher.pid:
        return None  # Popen already owns the frozen host's handle.
    import ctypes
    from ctypes import wintypes

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("reserved", ctypes.c_void_p * 4),
                    ("pid", ctypes.c_size_t), ("parent_pid", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    native.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG,
        ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    native.NtQueryInformationProcess.restype = ctypes.c_long
    # SYNCHRONIZE | QUERY_LIMITED_INFORMATION | TERMINATE, for this handle only.
    handle = kernel.OpenProcess(0x101001, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = ProcessInfo()
        status = native.NtQueryInformationProcess(handle, 0, ctypes.byref(info),
                                                  ctypes.sizeof(info), None)
        if status < 0 or info.parent_pid != launcher.pid:
            raise ValueError("Native handle parent is not this owned launcher")
        return _OwnedNativeProcess(kernel, handle, pid)
    except Exception:
        kernel.CloseHandle(handle)
        raise


def _cleanup_process(process, report, *, terminate_timeout=15, kill_timeout=5):
    """Bound cleanup of the verifier-owned host and record any escalation."""
    try:
        if process.poll() is not None:
            return
    except Exception as error:
        report["cleanupErrorType"] = type(error).__name__
    report["forcedCleanup"] = True
    try:
        process.terminate()
        process.wait(timeout=terminate_timeout)
        return
    except subprocess.TimeoutExpired:
        report["cleanupEscalated"] = True
    except Exception as error:
        report["cleanupErrorType"] = type(error).__name__
        report["cleanupEscalated"] = True
    try:
        process.kill()
        process.wait(timeout=kill_timeout)
    except Exception as error:
        report["cleanupErrorType"] = type(error).__name__


def _cleanup_owned_processes(launcher, native_process, report):
    try:
        if native_process is not None:
            try:
                _cleanup_process(native_process, report)
            finally:
                native_process.close()
    except Exception as error:
        report["cleanupErrorType"] = type(error).__name__
    finally:
        _cleanup_process(launcher, report)


def _exercise(page, pid, output, *, checks=None, observe=lambda _stage: None, deepseek=False):
    from playwright.sync_api import expect

    checks = [] if checks is None else checks
    observe("initial settings")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !!window.pywebview?.api?.assistant_get_settings")
    page.get_by_role("heading", name="project", exact=True).wait_for(timeout=30000)
    session = page.evaluate("() => window.pywebview.api.get_session()")
    root = Path(session["data"]["projectPath"]).resolve()
    assert root.name == "project" and root.parent.name.startswith("codeviz-assistant-native-")
    original = (root / "sample.py").read_bytes()
    settings = page.evaluate("() => window.pywebview.api.assistant_get_settings()")
    assert settings["ok"] and not settings["data"]["enabled"] and not settings["data"]["keySet"]
    checks.append("normal initial settings: no key, disabled")
    page.locator(".titlebar").get_by_role("button", name="AI Assistant", exact=True).click()
    panel = page.locator(".assistant-panel")
    panel.get_by_role("button", name="AI settings", exact=True).click()
    config = page.locator(".assistant-settings")
    config.get_by_label("API URL (HTTPS)", exact=True).fill(
        "https://api.deepseek.com/v1" if deepseek else "https://provider.invalid/v1")
    config.get_by_label("Model", exact=True).fill("deepseek-flash" if deepseek else "probe-model")
    config.get_by_label("API key (memory only)", exact=True).fill("synthetic-native-only")
    config.get_by_role("checkbox").check()
    config.get_by_role("button", name="Save AI settings", exact=True).click()
    expect(config.get_by_label("API key (memory only)", exact=True)).to_have_value("")
    expect(config).to_contain_text("Key set")
    if deepseek:
        observe("source-free SDK connection")
        config.get_by_role("button", name="Test connection (no source)", exact=True).click()
        expect(config.get_by_role("status")).to_have_text("Completed", timeout=30000)
        checks.append("rendered source-free production SDK connection")
    observe("new conversation and consent")
    panel.get_by_role("button", name="New conversation", exact=True).click()
    expect(panel.get_by_role("combobox")).to_have_value(re.compile(r".+"))
    panel.get_by_role("button", name="Authorize files", exact=True).click()
    consent = page.locator(".assistant-consent")
    consent.get_by_role("textbox").fill("sample.py")
    consent.get_by_role("checkbox").check()
    consent.get_by_role("button", name="Confirm authorization", exact=True).click()
    expect(consent).to_have_count(0)
    checks.append("rendered settings, memory credential clearing, explicit file consent")

    observe("search and graph")
    page.locator(".activity").get_by_role("button", name="Search", exact=True).click()
    page.get_by_role("textbox", name="Search", exact=True).fill("process_record")
    result = page.locator(".search-result").filter(has_text="sample.process_record").first
    expect(result).to_be_visible()
    result.get_by_role("button", name="Reveal in graph", exact=True).click()
    expect(page.locator(".react-flow__node-code").first).to_be_visible()
    page.screenshot(path=str(output / "graph.png"), full_page=True)
    result.get_by_role("button", name="Source", exact=True).click()
    editor = page.locator(".primary-editor .monaco-editor")
    expect(editor).to_be_visible(timeout=30000)
    page.get_by_role("button", name="Explain saved function", exact=True).click()
    observe("source explanation")
    panel.get_by_role("textbox", name="Ask about the project", exact=True).fill("Explain the saved function.")
    panel.get_by_role("button", name="Send", exact=True).click()
    expect(panel.locator(".validated_answer")).to_be_visible(timeout=30000)
    expect(panel.locator(".validated_answer h3")).to_have_count(7)
    expect(panel.locator(".assistant-evidence .anchor-card")).to_have_count(1)
    expect(panel.get_by_role("alert")).to_have_count(0)
    page.screenshot(path=str(output / "assistant-answer.png"), full_page=True)
    checks.append("explicit source-context send / validated seven sections / real evidence card")
    evidence = panel.locator(".assistant-evidence")
    observe("evidence navigation")
    evidence.get_by_role("button", name="Open source", exact=True).click()
    expect(editor).to_be_visible(timeout=15000)
    expect(editor.locator(".view-lines")).to_contain_text("process_record")
    evidence.get_by_role("button", name="Reveal in graph", exact=True).click()
    expect(page.locator(".react-flow__node-code.selected")).to_have_count(1)
    checks.append("retained source and graph navigation through real bridge")
    page.get_by_role("button", name="Close AI Assistant", exact=True).click()
    expect(panel).to_have_count(0)
    page.locator(".titlebar").get_by_role("button", name="AI Assistant", exact=True).click()
    expect(panel.locator(".validated_answer")).to_have_count(1)
    checks.append("sidebar close / reopen retains memory answer")

    observe("dirty source and native close")
    result.get_by_role("button", name="Source", exact=True).click()
    expect(editor).to_be_visible()
    editor.get_by_role("textbox", name="sample.py", exact=True).focus()
    page.keyboard.press("Control+End")
    page.keyboard.insert_text("\n# native unsaved draft")
    source_tabs = page.locator(".tabs").get_by_role("tab").filter(has_text="sample.py")
    # Source navigation may keep two anchors open for the same shared draft.
    # Assert every matching tab is dirty rather than treating them as one DOM node.
    expect(source_tabs.first).to_contain_text("●")
    for source_tab in source_tabs.all():
        expect(source_tab).to_contain_text("●")
    assert (root / "sample.py").read_bytes() == original
    _close_native(pid)
    dialog = page.locator(".source-dialog")
    expect(dialog).to_be_visible(timeout=15000)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(editor.locator(".view-lines")).to_contain_text("native unsaved draft")
    assert (root / "sample.py").read_bytes() == original
    page.screenshot(path=str(output / "dirty-preserved.png"), full_page=True)
    checks.append("OS close veto / Cancel preserves unsaved draft, disk unchanged")
    _close_native(pid)
    expect(dialog).to_be_visible(timeout=15000)
    with page.expect_event("close", timeout=15000):
        dialog.get_by_role("button", name="Discard", exact=True).click()
    checks.append("OS close / Discard exits real WebView2")
    return checks


def _validate_host_receipt(value, *, deepseek):
    flags = {"fakeOnly", "providerClosed", "runtimeShutdown"}
    counters = {"sends": 4 if deepseek else 2}
    if deepseek:
        flags.update(("productionSDK", "originalRequests"))
        counters["externalAttempts"] = 0
    keys = flags | counters.keys() | (set() if deepseek else {"cancelCalls"})
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("Invalid native receipt fields")
    if any(value[name] is not True for name in flags):
        raise ValueError("Invalid native receipt flag")
    if any(type(value[name]) is not int or value[name] != count for name, count in counters.items()):
        raise ValueError("Invalid native receipt counter")
    if not deepseek and (type(value["cancelCalls"]) is not int or not 0 <= value["cancelCalls"] <= 64):
        raise ValueError("Invalid native cancellation count")
    return value


def _read_host_exit(path, *, deepseek):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate native receipt field")
            value[key] = item
        return value

    def nonfinite(_value):
        raise ValueError("Invalid native receipt number")

    with path.open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096 or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("Invalid native receipt encoding or size")
    value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=nonfinite)
    return _validate_host_receipt(value, deepseek=deepseek)


def _final_result(report, candidate):
    if report["status"] == "skipped":
        return 2
    cleanup_failed = (report.get("forcedCleanup") is True or report.get("cleanupEscalated") is True
                      or any(key.startswith("cleanup") and key.endswith("ErrorType") for key in report))
    passed = candidate and not cleanup_failed
    report["status"] = "passed" if passed else "failed"
    return 0 if passed else 1


def main(argv=None):
    args = _parse_args(argv)
    kind = "source" if args.source else "wheel" if args.wheel_dir else "exe"
    output = (args.output or Path("build") / f"assistant-native-{kind}").resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "artifact": kind, "fakeOnly": True,
              "startedAtUnix": time.time(), "checks": []}
    process = native_process = None
    candidate = False
    try:
        _checkpoint(output, report, "initialize")
        if args.exe is not None and not args.exe.is_file():
            report.update(status="skipped", reason="Requested frozen artifact is missing")
            return 2
        if os.name != "nt":
            report.update(status="skipped", reason="WebView2 verification requires Windows")
            return 2
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        try:
            command = _host_command(args, port=port, output=output)
            if args.wheel_dir is not None:
                report["wheelInstall"] = _validate_wheel_install(args.wheel_dir, command)
        except FileNotFoundError:
            report.update(status="skipped", reason="Requested artifact or isolated wheel environment is missing")
            return 2
        from playwright.sync_api import sync_playwright

        with (output / "host.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            report["pid"] = process.pid
            _checkpoint(output, report, "wait native CDP target")

            def observe(targets):
                if report.get("cdpTargets") != targets:
                    report["cdpTargets"] = targets
                    _checkpoint(output, report, "wait native CDP target")

            def observe_error(error_type):
                if report.get("cdpProbeErrorType") != error_type:
                    report["cdpProbeErrorType"] = error_type
                    _checkpoint(output, report, "wait native CDP target")

            target = _wait_for_native_cdp_target(
                port, process, observe=observe, observe_error=observe_error)
            report["cdpTarget"] = target
            native_pid = _native_pid(output, process, port)
            native_process = _claim_native_process(native_pid, process)
            report["nativePid"] = native_pid
            with sync_playwright() as playwright:
                _checkpoint(output, report, "attach WebView2")
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}", timeout=10000)
                try:
                    _checkpoint(output, report, "select native page")
                    page = _wait_for_native_page(browser, port, process)
                    errors = []
                    page.on("pageerror", lambda _error: errors.append("renderer error"))
                    try:
                        _checkpoint(output, report, "exercise UI")
                        _exercise(page, native_pid, output, checks=report["checks"],
                                  observe=lambda stage: _checkpoint(output, report, stage),
                                  **({"deepseek": True} if args.deepseek else {}))
                    except Exception:
                        report["failedStage"] = report["stage"]
                        if not page.is_closed():
                            page.screenshot(path=str(output / "failure.png"), full_page=True)
                        raise
                    assert not errors, errors
                finally:
                    _checkpoint(output, report, "disconnect WebView2")
                    browser.close()
            _checkpoint(output, report, "wait native exit")
            report["exitCode"] = process.wait(timeout=20)
        _checkpoint(output, report, "validate native resources")
        exit_report = _read_host_exit(output / "host-exit.json", deepseek=args.deepseek)
        assert report["exitCode"] == 0, report
        report["resources"] = exit_report
        candidate = True
    except Exception as error:
        # Error text may contain an assertion's key/input. Persist only its type.
        report["errorType"] = type(error).__name__
        print(f"Native check failed at {report.get('stage', 'unknown')} "
              f"({type(error).__name__}); inspect owned screenshots and host.log.")
    finally:
        if process is not None:
            if native_process is None and process.poll() is None:
                try:
                    native_pid = _native_pid(output, process, port)
                    native_process = _claim_native_process(native_pid, process)
                except Exception as error:
                    report["cleanupOwnershipErrorType"] = type(error).__name__
            _cleanup_owned_processes(process, native_process, report)
        result = _final_result(report, candidate)
        _checkpoint(output, report, "complete" if result == 0 else report.get("stage", "finished"))
        print(json.dumps(report))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
