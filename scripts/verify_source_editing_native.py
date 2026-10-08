"""Attach to an owned WebView2 editing fixture; never edit a user's project."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9341)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, default=Path("build/source-editing-native"))
    args = parser.parse_args()
    from playwright.sync_api import expect, sync_playwright

    args.output.mkdir(parents=True, exist_ok=True)
    checks: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = next(page for context in browser.contexts for page in context.pages
                    if page.url.startswith("http://127.0.0.1:"))
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => !!window.pywebview?.api?.get_session")
        session = page.evaluate("() => window.pywebview.api.get_session()")
        assert session["ok"], session
        if session["data"]["projectPath"] is None:
            recent = page.locator(".recent-project").first
            expect(recent).to_be_visible()
            candidate = Path(recent.get_attribute("title") or "").resolve()
            assert candidate.name == "project" and candidate.parent.name.startswith(
                "codeviz-source-edit-smoke-"), "Refusing a non-owned recent project"
            recent.click()
            expect(page.locator(".overview-page h1")).to_have_text("project", timeout=30000)
            session = page.evaluate("() => window.pywebview.api.get_session()")
        root = Path(session["data"]["projectPath"]).resolve()
        assert root.name == "project" and root.parent.name.startswith(
            "codeviz-source-edit-smoke-"), "Refusing to edit a non-owned fixture"
        source = root / "sample.py"
        original = source.read_bytes()
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        if not page.locator(".search-input input").count():
            page.locator(".activity").get_by_role("button", name="搜索", exact=True).click()
        page.get_by_role("textbox", name="搜索", exact=True).fill("atoms")
        result = page.locator(".search-result").filter(has_text="sample.atoms").first
        expect(result).to_be_visible()
        result.get_by_role("button", name="源码", exact=True).click()
        editor = page.locator(".monaco-editor")
        expect(editor).to_be_visible(timeout=30000)
        expect(editor.locator(".view-lines")).to_contain_text("def")
        page.wait_for_function("""() => {
            const spans = [...document.querySelectorAll('.monaco-editor .view-lines span')];
            const keyword = spans.find(span => span.textContent === 'def');
            const comment = spans.find(span => span.children.length === 0 &&
                span.textContent?.includes('coding:'));
            return keyword && comment && getComputedStyle(keyword).color !==
                getComputedStyle(comment).color;
        }""", timeout=15000)
        colors = editor.locator(".view-lines span[class*='mtk']").evaluate_all(
            "elements => [...new Set(elements.map(e => getComputedStyle(e).color))]")
        assert len(colors) >= 3, colors
        checks.append("actual Python syntax tokens have at least three colors")
        page.screenshot(path=str(args.output / "monaco-colors.png"), full_page=True)

        # A real split editor uses a DIV EditContext, not a mocked textarea.
        result.get_by_role("button", name="在图中定位", exact=True).click()
        side_action = page.locator(".inspector .anchor-card").first.locator("button").nth(1)
        expect(side_action).to_be_visible()
        side_action.click()
        side = page.locator(".side-editor .monaco-editor")
        expect(side).to_be_visible(timeout=15000)
        islands_before = page.locator(".detail-island").count()
        side.get_by_role("textbox", name="sample.py", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator(".tabs").get_by_role("tab").filter(
            has_text="sample.py")).to_contain_text("●")
        assert page.locator(".detail-island").count() == islands_before
        page.keyboard.press("Control+z")
        expect(page.locator(".tabs").get_by_role("tab").filter(
            has_text="sample.py")).not_to_contain_text("●")
        page.keyboard.press("Control+w")
        expect(page.locator(".side-editor")).to_have_count(0)
        page.locator(".tabs").get_by_role("tab").filter(has_text="sample.py").click()
        expect(editor).to_be_visible()
        assert source.read_bytes() == original
        checks.append("split shares the draft/undo; EditContext Enter does not expand graph; Ctrl+W closes side")

        def append(text: str) -> None:
            editor.get_by_role("textbox", name="sample.py", exact=True).focus()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame("
                          "() => requestAnimationFrame(resolve)))")
            page.keyboard.press("Control+End")
            page.keyboard.insert_text(text)
            expect(page.locator(".tabs").get_by_role("tab").filter(
                has_text="sample.py")).to_contain_text("●")

        append("\n# saved via Ctrl+S")
        before_undo = editor.locator(".view-lines").inner_text()
        page.keyboard.press("Control+z")
        expect(editor.locator(".view-lines")).not_to_have_text(before_undo, use_inner_text=True)
        assert source.read_bytes() == original
        page.keyboard.press("Control+y")
        expect(editor.locator(".view-lines")).to_have_text(before_undo, use_inner_text=True)
        expect(page.locator(".tabs").get_by_role("tab").filter(
            has_text="sample.py")).to_contain_text("●")
        checks.append("real Monaco undo/redo updates the shared draft without disk writes")
        page.keyboard.press("Control+s")
        expect(page.locator(".tabs").get_by_role("tab").filter(
            has_text="sample.py")).not_to_contain_text("●", timeout=15000)
        saved = source.read_bytes()
        assert b"# saved via Ctrl+S" in saved
        assert b"\n" not in saved.replace(b"\r\n", b""), saved
        expect(page.locator(".statusbar")).to_contain_text("过期")
        checks.append("Ctrl+S saves bytes, preserves CRLF, and marks analysis Outdated")

        append("\n# keep this draft")
        page.keyboard.press("Control+w")
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="取消", exact=True).click()
        expect(editor.locator(".view-lines")).to_contain_text("keep this draft")
        assert source.read_bytes() == saved
        checks.append("closing a dirty source tab can be canceled without saving")

        external = saved + b"\r\n# external edit"
        source.write_bytes(external)
        page.keyboard.press("Control+s")
        expect(page.get_by_role("alert").filter(has_text="SOURCE_CONFLICT").first
               ).to_be_visible(timeout=15000)
        assert source.read_bytes() == external
        expect(page.locator(".tabs").get_by_role("tab").filter(
            has_text="sample.py")).to_contain_text("●")
        checks.append("external edit conflict preserves external bytes and the draft")
        page.screenshot(path=str(args.output / "conflict.png"), full_page=True)

        def native_close() -> None:
            subprocess.run([sys.executable, str(Path(__file__).with_name(
                "close_native_smoke.py")), str(args.pid)], check=True)

        native_close()
        expect(dialog).to_be_visible(timeout=15000)
        dialog.get_by_role("button", name="取消", exact=True).click()
        expect(editor).to_be_visible()
        assert source.read_bytes() == external
        checks.append("real OS close is vetoed and Cancel keeps the window/draft alive")
        worker = next((Path(__file__).resolve().parents[1] / "frontend" / "dist" /
                       "assets").glob("editor.worker-*.js"))
        assert verify_worker_asset(page, worker.name), "Bundled worker did not initialize"
        checks.append("bundled Worker initializes under the real same-origin CSP")
        assert not [url for url in requests if not url.startswith(
            ("http://127.0.0.1:", "data:", "codeviz-source:"))], requests
        checks.append("Monaco loads only local assets")
        assert original != external
        (args.output / "results.json").write_text(
            json.dumps({"checks": checks, "colors": colors, "requests": requests},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        native_close()
        expect(dialog).to_be_visible(timeout=15000)
        with page.expect_event("close", timeout=15000):
            dialog.get_by_role("button", name="放弃", exact=True).click()
        checks.append("Discard approves native exit")
        (args.output / "results.json").write_text(
            json.dumps({"checks": checks, "colors": colors, "requests": requests},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"checks": checks,
                          "output": str(args.output)}, ensure_ascii=False))


def verify_worker_asset(page, filename: str) -> bool:
    """Probe the actual bundled RPC worker; idle editors may not request it yet."""
    return page.evaluate("""filename => new Promise((resolve, reject) => {
        const worker = new Worker(new URL('./assets/' + filename, location.href),
                                  {type: 'module'});
        const finish = (error) => { clearTimeout(timer); worker.terminate();
            if (error) reject(new Error(error)); else resolve(true); };
        const timer = setTimeout(() => finish('Worker initialization timed out'), 10000);
        worker.onerror = event => finish(event.message || 'Worker error');
        worker.onmessage = event => {
            if (event.data.type === 1 && event.data.seq === '1')
                finish(event.data.err ? JSON.stringify(event.data.err) : null);
        };
        worker.postMessage({});
        worker.postMessage({vsWorker: 1, req: '1', type: 0,
                            channel: 'default', method: '$initialize', args: [1]});
    })""", filename)


if __name__ == "__main__":
    main()
