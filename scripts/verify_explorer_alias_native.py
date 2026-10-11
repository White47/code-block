"""Verify dirty Windows source aliases through the real restored PC tab."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-info", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    info = json.loads(args.host_info.read_text(encoding="utf-8").splitlines()[0])
    root = Path(info["project"]).resolve(strict=True)
    assert root.name == "project" and root.parent.name.startswith("codeviz-explorer-smoke-")
    assert sys.platform == "win32", "Native Windows alias fixture only"
    args.output.mkdir(parents=True, exist_ok=True)
    from native_source_assertions import capture_screenshot
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{info['port']}")
        page = next(page for context in browser.contexts for page in context.pages
                    if page.url.startswith("http://127.0.0.1:"))
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => !!window.pywebview?.api?.get_session")
        session = page.evaluate("() => window.pywebview.api.get_session()")
        if session["data"]["projectPath"] is None:
            page.locator(".recent-project").filter(has_text="project").first.click()
        expect(page.locator(".source-heading")).to_contain_text("SRC/HELPER.PY", timeout=30000)
        session = page.evaluate("() => window.pywebview.api.get_session()")
        assert session["data"]["fileSystemCaseSensitive"] is False
        original = (root / "src" / "helper.py").read_bytes()
        old = page.evaluate("""root => window.pywebview.api.open_source_document(root,
            {filePath:'SRC/HELPER.PY',startLine:1,endLine:1,startColumn:null,endColumn:null})""", str(root))
        assert old["ok"]
        editor = page.locator(".monaco-editor")
        expect(editor.locator(".view-lines")).to_contain_text("def helper", timeout=30000)
        editor.click()
        editor.get_by_role("textbox", name="SRC/HELPER.PY", exact=True).focus()
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("\n# Native case alias draft.")
        tree = page.get_by_role("tree", name="项目文件", exact=True)
        tree.get_by_role("treeitem", name="src", exact=True).click()

        def rename() -> None:
            tree.get_by_role("treeitem", name="helper.py", exact=True).click(button="right")
            page.get_by_role("menuitem", name="重命名", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_role("textbox", name="名称", exact=True).fill("renamed.py")
            dialog.get_by_role("button", name="重命名", exact=True).click()

        rename()
        page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        assert (root / "src" / "helper.py").read_bytes() == original
        assert not (root / "src" / "renamed.py").exists()
        expect(editor.locator(".view-lines")).to_contain_text("Native case alias draft")
        rename()
        page.get_by_role("dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.locator(".source-heading")).to_contain_text("src/renamed.py", timeout=30000)
        expect(page.locator(".tabs")).not_to_contain_text("HELPER.PY")
        assert b"Native case alias draft" in (root / "src" / "renamed.py").read_bytes()
        assert not (root / "src" / "helper.py").exists()
        stale = page.evaluate("request => window.pywebview.api.save_source_document(request)",
                              {"projectPath": str(root), "filePath": "SRC/HELPER.PY",
                               "revision": old["data"]["revision"], "text": old["data"]["text"]})
        assert not stale["ok"] and stale["error"]["code"] == "SOURCE_CONFLICT"
        expect(page.get_by_role("dialog")).to_have_count(0)
        capture_screenshot(page, path=str(args.output / "alias.png"), full_page=True)
        checks = ["restored Windows alias receives dirty cancel guard",
                  "Save then rename remaps actual alias tab and preserves draft bytes",
                  "old alias source revision is invalidated"]
        (args.output / "results.json").write_text(json.dumps({"checks": checks}, indent=2), encoding="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("close_native_smoke.py")),
                        str(info["pid"])], check=True)
        print(json.dumps({"checks": checks, "output": str(args.output)}))


if __name__ == "__main__":
    main()
