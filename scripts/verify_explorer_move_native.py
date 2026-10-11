"""Exercise real PC tree drag/drop and its accessible move command."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def install_drag_diagnostics(page, source: str, destination: str) -> None:
    """Observe real events and layout without dispatching or accepting a drop."""
    page.evaluate("""({source, destination}) => {
        window.__explorerPointerDiagnosticsCleanup?.();
        const find = name => [...document.querySelectorAll('[role="treeitem"]')]
            .find(node => node.getAttribute('aria-label') === name);
        const describe = node => node ? {tag: node.tagName,
            path: node.closest?.('[role="treeitem"]')?.dataset.path,
            className: typeof node.className === 'string' ? node.className : ''} : null;
        const geometry = node => {
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
            const ancestors = [];
            for (let parent = node.parentElement; parent; parent = parent.parentElement) {
                const style = getComputedStyle(parent);
                if (style.overflowX !== 'visible' || style.overflowY !== 'visible') {
                    ancestors.push({node: describe(parent), rect: parent.getBoundingClientRect().toJSON(),
                        overflowX: style.overflowX, overflowY: style.overflowY,
                        scrollTop: parent.scrollTop, scrollLeft: parent.scrollLeft});
                }
            }
            const hit = document.elementFromPoint(x, y);
            return {rect: rect.toJSON(), center: {x, y}, hit: describe(hit),
                centerHitsRow: Boolean(hit && node.contains(hit)), ancestors};
        };
        const snapshot = () => ({source: geometry(find(source)), target: geometry(find(destination)),
            feedback: [...document.querySelectorAll('.project-explorer > [role="status"]')]
                .map(node => ({text: node.textContent, rect: node.getBoundingClientRect().toJSON()}))});
        const log = window.__explorerPointerDiagnostics = {
            source, destination, viewport: {width: innerWidth, height: innerHeight},
            initial: snapshot(), events: []};
        const listeners = [];
        const record = phase => event => {
            const data = event.dataTransfer;
            let payloadLength = null;
            try { if (data) payloadLength = data.getData('application/x-codeviz-project-entry').length; }
            catch { payloadLength = 'unavailable'; }
            log.events.push({phase, type: event.type, time: performance.now(),
                x: event.clientX, y: event.clientY, buttons: event.buttons,
                trusted: event.isTrusted, prevented: event.defaultPrevented,
                eventTarget: describe(event.target), hit: describe(document.elementFromPoint(event.clientX, event.clientY)),
                types: data ? [...data.types] : null, files: data?.files.length,
                effectAllowed: data?.effectAllowed, dropEffect: data?.dropEffect,
                payloadLength, ...snapshot()});
        };
        const types = ['pointerdown', 'pointermove', 'pointerup', 'pointercancel',
            'dragstart', 'dragenter', 'dragover', 'dragleave', 'drop', 'dragend'];
        for (const type of types) {
            const capture = record('capture');
            document.addEventListener(type, capture, true);
            listeners.push(() => document.removeEventListener(type, capture, true));
            // React delegates to #root. A later listener on that same node sees
            // preventDefault/dropEffect even when React stops propagation.
            const root = document.getElementById('root');
            if (root) {
                const afterReact = record('after-react');
                root.addEventListener(type, afterReact);
                listeners.push(() => root.removeEventListener(type, afterReact));
            }
        }
        const observer = new MutationObserver(() => {
            log.events.push({phase: 'layout', time: performance.now(), ...snapshot()});
        });
        const explorer = document.querySelector('.project-explorer');
        if (explorer) observer.observe(explorer, {childList: true, subtree: true, attributes: true});
        window.__explorerPointerDiagnosticsCleanup = () => {
            observer.disconnect();
            for (const remove of listeners) remove();
            log.final = snapshot();
        };
    }""", {"source": source, "destination": destination})


def collect_drag_diagnostics(page):
    return page.evaluate("""() => {
        window.__explorerPointerDiagnosticsCleanup?.();
        return window.__explorerPointerDiagnostics;
    }""")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-info", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    info = json.loads(args.host_info.read_text(encoding="utf-8").splitlines()[0])
    root = Path(info["project"]).resolve(strict=True)
    assert root.name == "project" and root.parent.name.startswith("codeviz-explorer-smoke-")
    unrelated_bytes = (root / "README.md").read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    from native_source_assertions import capture_screenshot
    from playwright.sync_api import expect, sync_playwright

    checks: list[str] = []
    errors: list[str] = []
    drag_reports: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{info['port']}")
        page = next(page for context in browser.contexts for page in context.pages
                    if page.url.startswith("http://127.0.0.1:"))
        page.wait_for_load_state("networkidle")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.wait_for_function("() => !!window.pywebview?.api?.get_session")
        session = page.evaluate("() => window.pywebview.api.get_session()")
        if session["data"]["projectPath"] is None:
            page.locator(".recent-project").filter(has_text="project").first.click()
        tree = page.get_by_role("tree", name="项目文件", exact=True)
        expect(tree).to_be_visible(timeout=30000)

        def row(name: str):
            value = tree.get_by_role("treeitem", name=name, exact=True)
            expect(value).to_be_visible(timeout=15000)
            return value

        def context(name: str, action: str) -> None:
            row(name).click(button="right")
            page.get_by_role("menuitem", name=action, exact=True).click()

        def create(parent: str, name: str, folder: bool = False) -> None:
            context(parent, "新建文件夹" if folder else "新建文件")
            dialog = page.get_by_role("dialog")
            dialog.get_by_role("textbox", name="名称", exact=True).fill(name)
            dialog.get_by_role("button", name="创建", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(tree).to_have_attribute("aria-busy", "false")

        def expand(name: str) -> None:
            if row(name).get_attribute("aria-expanded") == "false":
                row(name).click()

        def drag(source: str, destination: str, expect_drop: bool = True) -> None:
            # One genuine pointer gesture, not a synthetic drop or a retried write.
            # Two moves over the target ensure WebView2 delivers dragover before up.
            page.bring_to_front()
            row(source).scroll_into_view_if_needed()
            row(destination).scroll_into_view_if_needed()
            origin, target = row(source).bounding_box(), row(destination).bounding_box()
            assert origin and target
            viewport_height = page.evaluate("() => innerHeight")
            assert all(0 <= box["y"] and box["y"] + box["height"] <= viewport_height
                       for box in (origin, target)), (origin, target, viewport_height)
            install_drag_diagnostics(page, source, destination)
            initial = page.evaluate("() => window.__explorerPointerDiagnostics.initial")
            assert all(initial[name]["centerHitsRow"] for name in ("source", "target")), initial
            before = page.evaluate("() => window.__explorerDragEvents.length")
            page.mouse.move(origin["x"] + origin["width"] / 2, origin["y"] + origin["height"] / 2)
            page.mouse.down()
            x, y = target["x"] + target["width"] / 2, target["y"] + target["height"] / 2
            page.mouse.move(x, y, steps=15)
            page.mouse.move(x, y + 1)
            page.mouse.up()
            diagnostics = collect_drag_diagnostics(page)
            drag_reports.append(diagnostics)
            (args.output / "drag-diagnostics.json").write_text(
                json.dumps(drag_reports, ensure_ascii=False, indent=2), encoding="utf-8")
            events = page.evaluate("n => window.__explorerDragEvents.slice(n)", before)
            failure = {"events": events, "pointerDiagnostics": diagnostics}
            initial = diagnostics["initial"]["target"]["rect"]
            for event in diagnostics["events"]:
                if event.get("type") == "drop":
                    break
                target_geometry = event.get("target")
                # Inspect the actual target-row geometry, not just event delivery.
                if target_geometry and "rect" in target_geometry:
                    assert abs(target_geometry["rect"]["y"] - initial["y"]) < 0.5, failure
            assert "dragstart" in events and "dragend" in events, failure
            if expect_drop:
                assert "drop" in events, failure

        page.locator(".sidebar").get_by_role("button", name="重新分析", exact=True).click()
        page.wait_for_function("""async () => {
            const reply = await window.pywebview.api.get_session();
            return reply.ok && ['Ready','Partial'].includes(reply.data.status);
        }""", timeout=30000)
        page.evaluate("""() => {
            window.__explorerDragEvents = [];
            for (const type of ['dragstart', 'drop', 'dragend']) {
                document.addEventListener(type, () => window.__explorerDragEvents.push(type), true);
            }
            window.__explorerMoveCalls = {prepare_project_entry_operation: 0,
                apply_project_entry_operation: 0, analyze: 0};
            for (const method of Object.keys(window.__explorerMoveCalls)) {
                const original = window.pywebview.api[method];
                window.pywebview.api[method] = (...args) => {
                    window.__explorerMoveCalls[method] += 1;
                    return original(...args);
                };
            }
        }""")
        graph_before = page.evaluate("() => window.pywebview.api.get_view('ProjectStructure', null, null)")
        layout_before = page.evaluate("() => window.pywebview.api.get_view_state()")
        assert graph_before["ok"] and layout_before["ok"], (graph_before, layout_before)
        create("project", "native-destination", True)
        create("project", "native-move.txt")
        row("README.md").click()
        unrelated_editor = page.locator(".monaco-editor")
        expect(unrelated_editor.locator(".view-lines")).to_contain_text("中文文档", timeout=30000)
        unrelated_editor.click()
        unrelated_editor.get_by_role("textbox", name="README.md", exact=True).focus()
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("\nUnrelated dirty draft must remain.")
        row("native-move.txt").click()
        editor = page.locator(".monaco-editor")
        expect(editor.locator(".view-lines")).to_be_visible(timeout=30000)
        editor.click()
        editor.get_by_role("textbox", name="native-move.txt", exact=True).focus()
        page.keyboard.insert_text("Native move draft survives cancel.")
        drag("native-move.txt", "native-destination")
        page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        assert (root / "native-move.txt").read_bytes() == b""
        assert not (root / "native-destination" / "native-move.txt").exists()
        expect(editor.locator(".view-lines")).to_contain_text("Native move draft")
        checks.append("actual internal drag receives dirty Cancel without disk mutation or draft loss")
        old = page.evaluate("request => window.pywebview.api.open_source_document(request.projectPath, request.anchor)",
                            {"projectPath": str(root), "anchor": {"filePath": "native-move.txt",
                             "startLine": 1, "endLine": 1, "startColumn": None, "endColumn": None}})
        assert old["ok"], old
        drag("native-move.txt", "native-destination")
        page.get_by_role("dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.locator(".source-heading")).to_contain_text("native-destination/native-move.txt", timeout=30000)
        expect(tree).to_have_attribute("aria-busy", "false")
        moved = root / "native-destination" / "native-move.txt"
        assert b"Native move draft" in moved.read_bytes() and not (root / "native-move.txt").exists()
        checks.append("Save then genuine drag moves bytes and remaps the actual source tab")
        stale = page.evaluate("request => window.pywebview.api.save_source_document(request)",
                              {"projectPath": str(root), "filePath": "native-move.txt",
                               "revision": old["data"]["revision"], "text": "Stale save must not recreate source."})
        assert not stale["ok"] and stale["error"]["code"] == "SOURCE_CONFLICT", stale
        assert not (root / "native-move.txt").exists()
        checks.append("a pre-move source revision cannot recreate the old disk path")
        expand("native-destination")
        context("native-move.txt", "侧边打开")
        expect(page.locator(".side-editor .view-lines")).to_contain_text("Native move draft", timeout=30000)
        before_root_move = page.evaluate("request => window.pywebview.api.open_source_document(request.projectPath, request.anchor)",
                                        {"projectPath": str(root), "anchor": {"filePath": "native-destination/native-move.txt",
                                         "startLine": 1, "endLine": 1, "startColumn": None, "endColumn": None}})
        assert before_root_move["ok"], before_root_move
        context("native-move.txt", "移动到…")
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("textbox", name="目标目录", exact=True).fill("")
        dialog.get_by_role("button", name="移动", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(tree).to_have_attribute("aria-busy", "false")
        expect(page.locator(".side-editor .source-heading span[title]")).to_have_attribute("title", "native-move.txt")
        expect(page.locator(".tabs")).not_to_contain_text("native-destination/native-move.txt")
        assert (root / "native-move.txt").read_bytes() == b"Native move draft survives cancel."
        assert not moved.exists()
        checks.append("accessible move-to-root preserves source tab and split identity")
        stale = page.evaluate("request => window.pywebview.api.save_source_document(request)",
                              {"projectPath": str(root), "filePath": "native-destination/native-move.txt",
                               "revision": before_root_move["data"]["revision"], "text": "No stale recreation."})
        assert not stale["ok"] and stale["error"]["code"] == "SOURCE_CONFLICT", stale
        assert not moved.exists()
        checks.append("a fresh revision taken immediately before clean move is invalidated by move itself")
        # Close only the clean split so the next source assertion is unique.
        page.locator(".side-editor").get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator(".side-editor")).to_have_count(0)
        expand("src")
        create("src", "nested", True)
        expand("nested")
        create("nested", "deep.txt")
        row("helper.py").click()
        expect(page.locator(".monaco-editor .view-lines")).to_contain_text("def helper", timeout=30000)
        context("helper.py", "侧边打开")
        expect(page.locator(".side-editor .view-lines")).to_contain_text("def helper", timeout=30000)
        original = (root / "src" / "helper.py").read_bytes()
        drag("src", "native-destination")
        for scope in (".primary-editor", ".side-editor"):
            expect(page.locator(f"{scope} .source-heading span[title]")).to_have_attribute(
                "title", "native-destination/src/helper.py", timeout=30000)
        expect(tree).to_have_attribute("aria-busy", "false")
        assert not (root / "src").exists()
        assert (root / "native-destination" / "src" / "helper.py").read_bytes() == original
        assert (root / "native-destination" / "src" / "nested" / "deep.txt").read_bytes() == b""
        checks.append("multilevel directory drag preserves bytes and remaps child source tab and split")
        page.locator(".side-editor").get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator(".side-editor")).to_have_count(0)
        expand("native-destination")
        expand("src")
        calls_before = page.evaluate("() => ({...window.__explorerMoveCalls})")
        drag("src", "nested", expect_drop=False)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert page.evaluate("() => window.__explorerMoveCalls") == calls_before
        assert (root / "native-destination" / "src" / "nested").is_dir()
        assert not (root / "native-destination" / "src" / "nested" / "src").exists()
        expect(page.get_by_role("dialog")).to_have_count(0)
        checks.append("a directory cannot be dragged into its own descendant")
        assert (root / "README.md").read_bytes() == unrelated_bytes
        row("README.md").click()
        expect(page.locator(".primary-editor .monaco-editor .view-lines")).to_contain_text("Unrelated dirty draft must remain", timeout=30000)
        expect(page.locator(".primary-editor .source-heading span[title='README.md']")).to_contain_text("●")
        checks.append("unrelated open dirty document and original disk bytes survive all moves")
        page.locator('.tabs button[aria-label*="README.md"]').click()
        page.get_by_role("dialog").get_by_role("button", name="放弃", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        graph_after = page.evaluate("() => window.pywebview.api.get_view('ProjectStructure', null, null)")
        layout_after = page.evaluate("() => window.pywebview.api.get_view_state()")
        assert graph_after == graph_before
        for field in ("positions", "positionsByView", "graphRoots", "viewportByView", "detailIslands"):
            assert layout_after["data"][field] == layout_before["data"][field], field
        assert page.evaluate("() => window.__explorerMoveCalls.analyze") == 0
        checks.append("old graph and graph layout remain intact without automatic analysis")
        expect(page.locator(".statusbar")).to_contain_text("过期")
        assert not errors, errors
        capture_screenshot(page, path=str(args.output / "move.png"), full_page=True)
        (args.output / "results.json").write_text(json.dumps({"checks": checks, "pageErrors": errors},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("close_native_smoke.py")),
                        str(info["pid"])], check=True)
        print(json.dumps({"checks": checks, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
