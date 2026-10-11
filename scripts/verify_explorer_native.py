"""Exercise Explorer in an actual owned WebView2 fixture, not a fake bridge."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def recycle_receipt(name: str, original_directory: Path) -> dict[str, str]:
    """Find only this fixture's unique item in the system recycle namespace."""
    escaped_name = name.replace("'", "''")
    escaped_directory = str(original_directory).replace("'", "''")
    command = (
        "$shell = New-Object -ComObject Shell.Application; "
        "$found = @(); foreach ($item in $shell.Namespace(10).Items()) { "
        f"if ($item.ExtendedProperty('System.Recycle.DeletedFrom') -eq '{escaped_directory}' "
        f"-and $item.Name.StartsWith('{escaped_name.removesuffix('.txt')}')) {{ "
        "$found += @{path=$item.Path; name=$item.Name; "
        "deletedFrom=$item.ExtendedProperty('System.Recycle.DeletedFrom')} } }; "
        "ConvertTo-Json -InputObject @($found) -Compress"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            check=True, capture_output=True, text=True, timeout=30)
    entries = json.loads(result.stdout)
    assert len(entries) == 1, "Fixture recycle receipt was not uniquely found"
    return entries[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9370)
    parser.add_argument("--host-info", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("build/explorer-native"))
    args = parser.parse_args()
    information = json.loads(args.host_info.read_text(encoding="utf-8").splitlines()[0])
    root = Path(information["project"]).resolve(strict=True)
    assert root.name == "project" and root.parent.name.startswith("codeviz-explorer-smoke-")
    assert information["port"] == args.port
    recycle_name = information["recycleName"]
    assert recycle_name.startswith("codeviz-explorer-recycle-") and recycle_name.endswith(".txt")
    args.output.mkdir(parents=True, exist_ok=True)
    from native_source_assertions import capture_screenshot
    from playwright.sync_api import expect, sync_playwright

    checks: list[str] = []
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = next(page for context in browser.contexts for page in context.pages
                    if page.url.startswith("http://127.0.0.1:"))
        page.wait_for_load_state("networkidle")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.wait_for_function("() => !!window.pywebview?.api?.list_project_directory")
        session = page.evaluate("() => window.pywebview.api.get_session()")
        if session["data"]["projectPath"] is None:
            recent = page.locator(".recent-project").filter(has_text="project").first
            assert Path(recent.get_attribute("title") or "").resolve() == root
            recent.click()
        tree = page.get_by_role("tree", name="项目文件", exact=True)
        expect(tree).to_be_visible(timeout=30000)

        def row(name: str):
            value = tree.get_by_role("treeitem", name=name, exact=True)
            expect(value).to_be_visible(timeout=15000)
            return value

        def context(name: str, action: str) -> None:
            row(name).click(button="right")
            page.get_by_role("menuitem", name=action, exact=True).click()

        def create(name: str, kind: str) -> None:
            if row("project").get_attribute("aria-expanded") == "false":
                row("project").click()
            page.get_by_role("button", name="新建文件夹" if kind == "directory" else "新建文件",
                             exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_role("textbox", name="名称", exact=True).fill(name)
            dialog.get_by_role("button", name="创建", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(row(name)).to_be_visible()

        def rename(name: str, destination: str) -> None:
            context(name, "重命名")
            dialog = page.get_by_role("dialog")
            dialog.get_by_role("textbox", name="名称", exact=True).fill(destination)
            dialog.get_by_role("button", name="重命名", exact=True).click()

        def append(file_path: str, text: str) -> None:
            pane = page.locator(".monaco-editor")
            expect(pane.locator(".view-lines")).to_be_visible(timeout=30000)
            # EditContext can be zero-width while a tree row owns focus. A real
            # user clicks the visible editor before typing; don't require an
            # unfocused internal input DIV to have visible geometry.
            pane.click()
            editor = pane.get_by_role("textbox", name=file_path, exact=True)
            editor.focus()
            page.keyboard.press("Control+End")
            page.keyboard.insert_text(text)

        expect(row("src")).to_have_attribute("aria-expanded", "false")
        row("src").click()
        expect(row("helper.py")).to_be_visible()
        row("src").click()
        expect(tree.get_by_role("treeitem", name="helper.py", exact=True)).to_have_count(0)
        checks.append("real lazy directory tree expands and collapses")
        # External changes are made only inside this host-owned disposable tree.
        # A root refresh must invalidate already-loaded child directory caches.
        (root / "src" / "external-refresh.txt").write_text("External fixture change.\n", encoding="utf-8")
        page.get_by_role("button", name="刷新", exact=True).click()
        if row("src").get_attribute("aria-expanded") == "false":
            row("src").click()
        expect(row("external-refresh.txt")).to_be_visible()
        row("src").click()
        checks.append("root refresh reloads previously cached child directories")
        session = page.evaluate("() => window.pywebview.api.get_session()")
        assert session["data"]["status"] == "NotAnalyzed", session
        row("README.md").click()
        expect(page.locator(".monaco-editor .view-lines")).to_contain_text("中文文档", timeout=30000)
        append("README.md", "\nNative Explorer text save.")
        page.keyboard.press("Control+s")
        expect(page.locator(".tabs").get_by_role("tab").filter(has_text="README.md")
               ).not_to_contain_text("●", timeout=15000)
        assert b"Native Explorer text save." in (root / "README.md").read_bytes()
        checks.append("unanalysed project opens and saves actual generic text")
        page.locator(".sidebar").get_by_role("button", name="重新分析", exact=True).click()
        page.wait_for_function("""async () => {
            const value = await window.pywebview.api.get_session();
            return value.ok && ['Ready', 'Partial'].includes(value.data.status);
        }""", timeout=30000)
        create("native-new.txt", "file")
        assert (root / "native-new.txt").read_bytes() == b""
        create("native-folder", "directory")
        assert (root / "native-folder").is_dir()
        checks.append("new file and folder use exclusive native filesystem creation")
        row("native-new.txt").click()
        append("native-new.txt", "Draft survives cancellation.")
        rename("native-new.txt", "native-renamed.txt")
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("button", name="保存", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="取消", exact=True).click()
        expect(page.locator(".monaco-editor .view-lines")).to_contain_text("Draft survives")
        assert (root / "native-new.txt").exists() and not (root / "native-renamed.txt").exists()
        checks.append("dirty rename Cancel preserves draft and original disk path")
        rename("native-new.txt", "native-renamed.txt")
        page.get_by_role("dialog").get_by_role("button", name="保存", exact=True).click()
        expect(row("native-renamed.txt")).to_be_visible()
        expect(page.locator(".tabs")).to_contain_text("native-renamed.txt")
        assert (root / "native-renamed.txt").read_bytes() == b"Draft survives cancellation."
        assert not (root / "native-new.txt").exists()
        checks.append("Save then rename updates actual file and open tab identity")
        rename("native-folder", "native-directory")
        expect(row("native-directory")).to_be_visible()
        assert (root / "native-directory").is_dir() and not (root / "native-folder").exists()
        checks.append("directory rename applies to the actual filesystem")
        row(recycle_name).click()
        original = (root / recycle_name).read_bytes()
        context(recycle_name, "删除")
        page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        assert (root / recycle_name).read_bytes() == original
        checks.append("delete confirmation Cancel performs no disk mutation")
        context(recycle_name, "删除")
        page.get_by_role("dialog").get_by_role("button", name="移到回收站", exact=True).click()
        expect(tree.get_by_role("treeitem", name=recycle_name, exact=True)).to_have_count(0, timeout=15000)
        assert not (root / recycle_name).exists()
        receipt = recycle_receipt(recycle_name, root)
        assert Path(receipt["path"]).read_bytes() == original
        expect(page.locator(".tabs")).not_to_contain_text(recycle_name)
        checks.append("actual system recycle-bin receipt preserves deleted fixture bytes")
        row(".git").click(button="right")
        expect(page.get_by_role("menuitem", name="重命名", exact=True)).to_be_disabled()
        expect(page.get_by_role("menuitem", name="删除", exact=True)).to_be_disabled()
        page.keyboard.press("Escape")
        listing = page.evaluate("""root => window.pywebview.api.list_project_directory(
            {projectPath: root, directory: ''})""", str(root))
        invalid = page.evaluate("""request => window.pywebview.api.create_project_entry(request)""",
                                {"projectPath": str(root), "projectEpoch": listing["data"]["projectEpoch"],
                                 "parent": "", "parentRevision": listing["data"]["directoryRevision"],
                                 "name": "../escape.py", "kind": "file"})
        assert not invalid["ok"] and not (root.parent / "escape.py").exists()
        checks.append("protected entries are disabled and native path traversal is rejected")
        row(".git").click()
        row("protected.py").click()
        expect(page.locator(".monaco-editor .view-lines")).to_contain_text("protected fixture", timeout=30000)
        expect(page.locator(".source-heading").get_by_role("button", name="保存", exact=True)).to_be_disabled()
        protected_bytes = (root / ".git" / "protected.py").read_bytes()
        protected_editor = page.locator(".monaco-editor")
        protected_editor.click()
        page.keyboard.press("Control+End")
        page.keyboard.insert_text("Must not modify a protected source.")
        expect(protected_editor.locator(".view-lines")).not_to_contain_text("Must not modify")
        assert (root / ".git" / "protected.py").read_bytes() == protected_bytes
        checks.append("protected safe text opens in the actual readonly Monaco editor")
        expect(page.locator(".statusbar")).to_contain_text("过期")
        assert not errors, errors
        capture_screenshot(page, path=str(args.output / "explorer.png"), full_page=True)
        (args.output / "results.json").write_text(
            json.dumps({"checks": checks, "pageErrors": errors, "recycleReceipt": receipt},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("close_native_smoke.py")),
                        str(information["pid"])], check=True)
        print(json.dumps({"checks": checks, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
