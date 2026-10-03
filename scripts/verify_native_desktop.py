"""Inspect a real WebView2 window over CDP, without a mocked desktop bridge."""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_interactions(page, output: Path) -> list[str]:
    """Exercise rendered controls, not just direct bridge calls."""
    checks = []
    side_close = page.locator(".side-title").get_by_role("button", name="关闭", exact=True)
    if side_close.count():
        side_close.click()
    page.locator(".entity-row").filter(has_text="model.Classifier").click()
    encoder = page.locator(".code-node").filter(has_text="encoder").first
    expect(encoder).to_be_visible()
    encoder.click()
    expect(page.locator(".inspector h2")).to_have_text("encoder")
    page.screenshot(path=str(output / "architecture.png"), full_page=True)
    encoder.dblclick()
    expect(page.locator(".detail-island")).to_have_count(1)
    definition = page.locator(".code-node").filter(has_text="model.Encoder").first
    expect(definition).to_be_visible()
    page.get_by_role("button", name="适应画布", exact=True).click()
    expect(definition).to_be_in_viewport()
    page.screenshot(path=str(output / "detail-island.png"), full_page=True)
    checks.append("architecture / inspector / detail island")
    for label in ("上游", "下游", "双向"):
        control = page.locator(".query-toolbar").get_by_role("button", name=label, exact=True)
        control.click()
        expect(control).to_have_class("active")
        expect(page.locator(".code-node").first).to_be_visible()
        expect(page.locator('[role="alert"]')).to_have_count(0)
    checks.append("upstream / downstream / both query controls")
    page.get_by_role("tab", name="项目概览", exact=True).click()
    page.locator(".entity-row").filter(has_text="train_epoch").click()
    for role in ("EpochLoop", "BatchLoop"):
        page.get_by_role("button", name="适应画布", exact=True).click()
        loop = page.locator(f'.code-node[data-role="{role}"]').first
        expect(loop).to_be_visible()
        loop.click()
        page.locator(".inspector").get_by_role("button", name="展开", exact=True).click()
    page.get_by_role("button", name="适应画布", exact=True).click()
    for role in ("ForwardPass", "LossCalculation", "BackwardPass", "ParameterUpdate"):
        operation = page.locator(f'.code-node[data-role="{role}"]').first
        expect(operation).to_be_visible()
        operation.click()
        expect(page.locator(".inspector").get_by_role(
            "button", name="打开源码", exact=True).first).to_be_enabled()
    page.screenshot(path=str(output / "training.png"), full_page=True)
    page.locator(".inspector").get_by_role("button", name="打开源码", exact=True).first.click()
    expect(page.locator(".code-table tr.highlighted pre").first).to_contain_text("optimizer.step()")
    page.keyboard.press("Control+w")
    checks.append("epoch / batch / forward / loss / backward / optimizer / source")
    if not page.get_by_role("textbox", name="搜索", exact=True).count():
        page.get_by_role("button", name="搜索", exact=True).click()
    page.get_by_role("textbox", name="搜索", exact=True).fill("反向传播")
    result = page.locator(".search-result").first
    expect(result).to_be_visible()
    result.get_by_role("button", name="在图中定位", exact=True).click()
    backward = page.locator('.code-node[data-role="BackwardPass"]').first
    expect(backward).to_be_visible()
    backward.click()
    source = page.locator(".inspector").get_by_role(
        "button", name="打开源码", exact=True).first
    expect(source).to_be_enabled()
    source.click()
    expect(page.locator(".code-table tr").first.locator(".line-number")).to_have_text("1")
    expect(page.locator(".code-table tr.highlighted")).not_to_have_count(0)
    expect(page.locator(".code-table tr.highlighted pre").first).to_contain_text("loss.backward()")
    page.screenshot(path=str(output / "source.png"), full_page=True)
    before = page.get_by_role("tab").count()
    page.keyboard.press("Control+w")
    expect(page.get_by_role("tab")).to_have_count(before - 1)
    # Restored tab order may activate Settings; re-reveal to regain local context.
    result.get_by_role("button", name="在图中定位", exact=True).click()
    expect(page.locator('.code-node[data-role="BackwardPass"]').first).to_be_visible()
    page.locator('.code-node[data-role="BackwardPass"]').first.click()
    page.locator(".inspector").get_by_role("button", name="在侧边打开", exact=True).first.click()
    expect(page.locator(".side-editor .code-table")).to_be_visible()
    page.screenshot(path=str(output / "split.png"), full_page=True)
    checks.append("semantic search / reveal / source lines / tab close / split")
    page.get_by_role("button", name="设置", exact=True).click()
    page.locator("#language").select_option("en")
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    expect(page.locator("#ignore-paths")).to_be_enabled()
    page.screenshot(path=str(output / "settings-en.png"), full_page=True)
    response = page.evaluate("() => window.pywebview.api.get_preferences()")
    assert response["ok"] and response["data"]["language"] == "en", response
    page.locator("#language").select_option("zh-CN")
    checks.append("settings / language persisted by Python")
    return checks


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9237)
    parser.add_argument("--output", type=Path, default=Path("build/native-smoke"))
    parser.add_argument("--interactions", action="store_true")
    parser.add_argument("--project", type=Path,
                        help="Open and analyze a fixture through the native UI")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{args.port}")
        page = None
        try:
            page = browser.contexts[0].pages[0]
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.wait_for_load_state("networkidle")
            page.wait_for_function(
                "Boolean(window.pywebview?.api?.get_session)", timeout=30_000)
            if args.project:
                field = page.get_by_role("textbox", name="本地项目绝对路径", exact=True)
                field.fill(str(args.project.resolve()))
                page.locator(".path-form button").click()
                expect(page.locator(".sidebar-project button").last).to_be_enabled()
                opened = page.evaluate("() => window.pywebview.api.get_session()")
                if opened["data"]["status"] in {"NotAnalyzed", "Failed"}:
                    page.get_by_role("button", name="分析项目", exact=True).click()
            overview_tab = page.get_by_role("tab", name="项目概览", exact=True)
            if overview_tab.count():
                overview_tab.click()
            response = page.evaluate("() => window.pywebview.api.get_session()")
            assert response["ok"], response
            assert response["schemaVersion"] == 1, response
            methods = page.evaluate("Object.keys(window.pywebview.api)")
            assert not any("." in method for method in methods), methods
            page.get_by_role("heading", name="mvp_project", exact=True).wait_for()
            response = page.evaluate("() => window.pywebview.api.get_session()")
            page.screenshot(path=str(args.output / "overview.png"), full_page=True)
            print(json.dumps({
                "url": page.url,
                "session": response["data"],
                "methods": methods,
                "buttonCount": page.get_by_role("button").count(),
            }, ensure_ascii=False))
            if args.interactions:
                checks = check_interactions(page, args.output)
                assert not errors, errors
                print(json.dumps({"checks": checks, "pageErrors": errors},
                                 ensure_ascii=False))
        except Exception:
            if page is not None and not page.is_closed():
                page.screenshot(path=str(args.output / "failure.png"), full_page=True)
                print(page.locator("body").inner_text()[:2000])
            raise
        finally:
            # Disconnect automation; the host watchdog owns the native window.
            browser.close()


if __name__ == "__main__":
    main()
