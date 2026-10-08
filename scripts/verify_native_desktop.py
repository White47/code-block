"""Inspect a real WebView2 window over CDP, without a mocked desktop bridge."""

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def overview_choice(page, category: str, name: str):
    """Keep ML roots distinct from same-named generic functions and classes."""
    labels = {"models": r"^(Models|模型)\s*\d*\s*$",
              "entries": r"^(Entry points|入口)\s*\d*\s*$"}
    card = page.locator(".overview-card").filter(
        has=page.locator(".card-heading").filter(has_text=re.compile(labels[category])))
    expect(card).to_have_count(1)
    choice = card.locator(".entity-row").filter(has_text=name)
    expect(choice).to_have_count(1)
    return choice


def click_canvas_node(page, node, stage: str, measurements: list) -> None:
    """Bring a clipped node into the canvas using real background drags."""
    expect(node).to_be_visible()
    for attempt in range(12):
        geometry = node.evaluate("""node => {
            const canvas = node.closest('.react-flow');
            const rect = e => { const r = e.getBoundingClientRect();
                return {x:r.x, y:r.y, width:r.width, height:r.height}; };
            const area = rect(canvas), target = rect(node);
            const center = {x:target.x + target.width/2, y:target.y + target.height/2};
            const hit = document.elementFromPoint(center.x, center.y);
            const matrix = new DOMMatrixReadOnly(getComputedStyle(
                canvas.querySelector('.react-flow__viewport')).transform);
            return {canvas:area, target, center,
                viewport:{x:matrix.e, y:matrix.f, zoom:matrix.a},
                hit:hit?.className?.baseVal ?? hit?.className ?? null,
                clickable:!!hit && node.contains(hit) && center.x > area.x + 24 &&
                    center.x < area.x + area.width - 24 && center.y > area.y + 24 &&
                    center.y < area.y + area.height - 60};
        }""")
        measurements.append({"stage": stage, "attempt": attempt, **geometry})
        if geometry["clickable"]:
            node.click()
            return
        area, center = geometry["canvas"], geometry["center"]
        dx = max(-area["width"] * .4, min(area["width"] * .4,
                 area["x"] + area["width"] / 2 - center["x"]))
        dy = max(-area["height"] * .4, min(area["height"] * .4,
                 area["y"] + (area["height"] - 36) / 2 - center["y"]))
        assert abs(dx) + abs(dy) > 1, (stage, geometry)
        start = page.locator('.react-flow').evaluate("""(canvas, delta) => {
            const r = canvas.getBoundingClientRect();
            for (const fy of [.5, .3, .7, .2, .8]) {
                for (const fx of [.15, .85, .3, .7, .5]) {
                    const x = r.x + r.width*fx, y = r.y + r.height*fy;
                    const endX = x + delta.x, endY = y + delta.y;
                    if (endX < r.left+24 || endX > r.right-24 ||
                        endY < r.top+24 || endY > r.bottom-60) continue;
                    const hit = document.elementFromPoint(x,y);
                    if (hit?.classList.contains('react-flow__pane')) return {x,y};
                }
            }
            return null;
        }""", {"x": dx, "y": dy})
        assert start is not None, ("No unobstructed canvas background drag", stage, geometry)
        page.mouse.move(start["x"], start["y"])
        page.mouse.down()
        try:
            page.mouse.move(start["x"] + dx, start["y"] + dy, steps=15)
        finally:
            page.mouse.up()
        page.wait_for_function("""before => {
            const m = new DOMMatrixReadOnly(getComputedStyle(
                document.querySelector('.react-flow__viewport')).transform);
            return Math.abs(m.e-before.x) > 1 || Math.abs(m.f-before.y) > 1;
        }""", arg=geometry["viewport"], timeout=5000)
    raise AssertionError(f"{stage}: node did not become clickable after bounded canvas panning")


def check_interactions(page, output: Path, measurements: list) -> list[str]:
    """Exercise rendered controls, not just direct bridge calls."""
    checks = []
    side_close = page.locator(".side-title").get_by_role("button", name="关闭", exact=True)
    if side_close.count():
        side_close.click()
    overview_choice(page, "models", "model.Classifier").click()
    encoder = page.locator(".code-node").filter(has_text="encoder").first
    expect(encoder).to_be_visible()
    encoder.click()
    expect(page.locator(".inspector h2")).to_have_text("encoder")
    encoder_id = encoder.locator("..").get_attribute("data-id")
    assert encoder_id, "main encoder occurrence must have a real canvas ID"
    page.screenshot(path=str(output / "architecture.png"), full_page=True)
    encoder.dblclick()
    expect(page.locator(".detail-island")).to_have_count(1)
    parent_id = page.locator(".react-flow__node-island").get_attribute("data-id")
    assert parent_id and parent_id.startswith("island:"), parent_id
    state = page.evaluate("() => window.pywebview.api.get_view_state()")
    assert state["ok"], state
    island = next(item for item in state["data"]["detailIslands"]
                  if "island:" + item["id"] == parent_id)
    assert island["sourceNodeId"] == encoder_id, island
    detail_root = page.locator(
        f'.react-flow__node-code[data-id={json.dumps(parent_id + ":" + encoder_id)}]')
    expect(detail_root).to_have_count(1)
    expect(detail_root.locator(".code-node code")).to_have_text("model.Classifier.encoder")
    click_canvas_node(page, detail_root, "occurrence-local-encoder-root", measurements)
    expect(page.locator(".inspector-content > code:not(.member-id)")).to_have_text("model.Classifier.encoder")
    expect(page.locator('.react-flow__node-code[data-id^="island:"] .code-node code').filter(
        has_text=re.compile(r"^model\.Encoder\.layers$"))).to_have_count(1)
    page.screenshot(path=str(output / "detail-island.png"), full_page=True)
    checks.append("architecture / inspector / detail island")
    toolbar = page.locator(".query-toolbar")
    expect(toolbar.get_by_role("button", name=re.compile(r"^(上游|下游|双向)$"))).to_have_count(0)
    more = toolbar.get_by_role("button", name=re.compile(r"^更多层"))
    fewer = toolbar.get_by_role("button", name=re.compile(r"^更少层"))
    main_nodes = page.locator('.react-flow__node-code:not([data-id^="island:"])')

    def structure_nodes():
        return main_nodes.evaluate_all("""nodes => nodes.map(node => ({
            id: node.getAttribute('data-id'),
            name: node.querySelector('.code-node strong')?.textContent,
            codeName: node.querySelector('.code-node code')?.textContent
        }))""")

    expect(more).to_have_text("更多层 · 1/5")
    expect(fewer).to_be_disabled()
    shallow = structure_nodes()
    initial_ids = {node["id"] for node in shallow}
    assert len(initial_ids) == 4, shallow
    assert {node["name"] for node in shallow} == {"Classifier", "embedding", "encoder", "head"}, shallow
    previous_ids = initial_ids
    for depth, code_name in ((2, "model.Encoder.layers"), (3, "model.Encoder.layers[*]")):
        expect(more).to_be_enabled()
        more.click()
        expect(more).to_have_text(f"更多层 · {depth}/5")
        expect(main_nodes.locator(".code-node code").filter(
            has_text=re.compile(rf"^{re.escape(code_name)}$"))).to_have_count(1)
        current = structure_nodes()
        current_ids = {node["id"] for node in current}
        assert previous_ids < current_ids, (shallow, current)
        assert any(node["id"] in current_ids - previous_ids and node["codeName"] == code_name
                   for node in current), current
        expect(page.locator(".query-feedback")).to_contain_text(re.compile(
            rf"新增节点:\s*{len(current_ids - previous_ids)}(?!\d)"))
        expect(page.locator('[role="alert"]')).to_have_count(0)
        previous_ids = current_ids
    page.screenshot(path=str(output / "architecture-structure-depth-3.png"), full_page=True)
    for depth, hidden_code_name in ((2, "model.Encoder.layers[*]"), (1, "model.Encoder.layers")):
        expect(fewer).to_be_enabled()
        fewer.click()
        expect(fewer).to_have_text(f"更少层 · {depth}/5")
        expect(main_nodes.locator(".code-node code").filter(
            has_text=re.compile(rf"^{re.escape(hidden_code_name)}$"))).to_have_count(0)
        current = structure_nodes()
        current_ids = {node["id"] for node in current}
        assert current_ids < previous_ids, (previous_ids, current)
        expect(page.locator(".query-feedback")).to_contain_text(re.compile(
            rf"收起节点:\s*{len(previous_ids - current_ids)}(?!\d)"))
        expect(page.locator('[role="alert"]')).to_have_count(0)
        previous_ids = current_ids
    assert previous_ids == initial_ids, (shallow, structure_nodes())
    expect(fewer).to_be_disabled()
    checks.append("architecture structure depth 1 -> 2 -> 3 -> 2 -> 1 / actual source-backed node sets / feedback")
    page.get_by_role("tab", name="项目概览", exact=True).click()
    overview_choice(page, "entries", "train_epoch").click()
    for role in ("EpochLoop", "BatchLoop"):
        page.get_by_role("button", name="适应画布", exact=True).click()
        loop = page.locator(f'.code-node[data-role="{role}"]').first
        expect(loop).to_be_visible()
        click_canvas_node(page, loop, role, measurements)
        page.locator(".inspector").get_by_role("button", name="展开", exact=True).click()
    page.get_by_role("button", name="适应画布", exact=True).click()
    for role in ("ForwardPass", "LossCalculation", "BackwardPass", "ParameterUpdate"):
        operation = page.locator(f'.code-node[data-role="{role}"]').first
        expect(operation).to_be_visible()
        click_canvas_node(page, operation, role, measurements)
        expect(page.locator(".inspector h2")).to_have_text(role)
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
    click_canvas_node(page, backward, "Search reveal BackwardPass", measurements)
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
    click_canvas_node(page, page.locator('.code-node[data-role="BackwardPass"]').first,
                      "Split source BackwardPass", measurements)
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
    report = {"status": "failed", "port": args.port, "checks": [], "nodeInteractions": [],
              "errors": [], "pageErrors": [], "attachOnly": True,
              "interactionMethod": "ordinary clicks and real canvas background drags"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{args.port}")
        page = None
        try:
            page = browser.contexts[0].pages[0]
            errors = []
            report["pageErrors"] = errors
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.wait_for_load_state("networkidle")
            page.wait_for_function(
                "Boolean(window.pywebview?.api?.get_session)", timeout=30_000)
            if args.project:
                from verify_general_python_native import NativeAcceptance

                opener = NativeAcceptance(page, args.project.resolve(), args.output, report, expect)
                opener.open_project_ui(args.project.resolve(), "mvp-regression-open")
                opened = page.evaluate("() => window.pywebview.api.get_session()")
                if opened["data"]["status"] in {"NotAnalyzed", "Failed"}:
                    page.get_by_role("button", name="分析项目", exact=True).click()
            overview_tab = page.get_by_role("tab", name="项目概览", exact=True)
            if overview_tab.count():
                overview_tab.click()
            response = page.evaluate("() => window.pywebview.api.get_session()")
            report["session"] = response["data"]
            report["url"] = page.url
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
                checks = check_interactions(page, args.output, report["nodeInteractions"])
                report["checks"] = checks
                assert not errors, errors
                print(json.dumps({"checks": checks, "pageErrors": errors},
                                 ensure_ascii=False))
            report["status"] = "passed"
        except Exception as error:
            report["errors"].append(f"{type(error).__name__}: {error}")
            report["traceback"] = traceback.format_exc()
            if page is not None and not page.is_closed():
                page.screenshot(path=str(args.output / "failure.png"), full_page=True)
                print(page.locator("body").inner_text()[:2000])
            raise
        finally:
            (args.output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            # Disconnect automation; the host watchdog owns the native window.
            browser.close()


if __name__ == "__main__":
    main()
