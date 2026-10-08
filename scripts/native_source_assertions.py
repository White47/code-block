"""Read-only native Monaco DOM assertions shared by the attach-only verifiers."""

import math
import re
import time


def capture_screenshot(page, *, path, full_page=True, timeout=10_000, timeout_type=None):
    """Retry a native screenshot once on Playwright timeout; preserve final failure."""
    if timeout_type is None:
        from playwright.sync_api import TimeoutError as timeout_type

    for attempt in range(2):
        page.bring_to_front()
        try:
            return page.screenshot(path=path, full_page=full_page, timeout=timeout)
        except timeout_type:
            if attempt == 1:
                raise


_SNAPSHOT = r"""source => {
    const editor = source.querySelector('.monaco-editor');
    const viewport = editor?.querySelector('.overflow-guard') ?? editor;
    const visible = node => {
        if (!node || !viewport) return false;
        const rect = node.getBoundingClientRect(), area = viewport.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
            style.display !== 'none' && rect.bottom > area.top && rect.top < area.bottom &&
            rect.right > area.left && rect.left < area.right;
    };
    const top = node => {
        for (let item = node; item && item !== editor; item = item.parentElement) {
            if (item.style.top) return Number.parseFloat(item.style.top);
        }
        return null;
    };
    const collect = selector => [...(editor?.querySelectorAll(selector) ?? [])].map(node =>
        ({top: top(node), text: node.textContent ?? '', visible: visible(node)}));
    const line = editor?.querySelector('.view-lines .view-line');
    return {
        filePath: source.querySelector('.source-heading span[title]')?.getAttribute('title'),
        range: source.querySelector('.source-heading small')?.textContent,
        editorVisible: visible(editor), viewportHeight: viewport?.clientHeight ?? 0,
        lineHeight: line ? Number.parseFloat(getComputedStyle(line).height) : 0,
        renderedLineCount: editor?.querySelectorAll('.view-lines .view-line').length ?? 0,
        decorations: collect('.source-anchor-line'), numbers: collect('.line-numbers'),
        lines: collect('.view-lines .view-line')
    };
}"""


def anchor_from_card(card):
    """Capture the selected Inspector anchor before the source view opens."""
    label = card.locator("code").inner_text().strip()
    match = re.fullmatch(r"(.+):(\d+)–(\d+)", label)
    assert match, f"Invalid Inspector source anchor: {label!r}"
    return match.group(1), int(match.group(2)), int(match.group(3))


def assert_source_snapshot(snapshot, filename, start_line, end_line, snippet):
    assert snapshot["filePath"] == filename, snapshot
    assert snapshot["range"] == f"L{start_line}–{end_line}", snapshot
    assert snapshot["editorVisible"], "Monaco editor is not visible"
    height, line_height = snapshot["viewportHeight"], snapshot["lineHeight"]
    assert height > 0 and line_height > 0, "Monaco viewport/line geometry unavailable"
    # Include bounded Monaco overscan, never require line 1 to be rendered at a far anchor.
    assert 0 < snapshot["renderedLineCount"] <= math.ceil(height / line_height) + 20, snapshot
    numbers = [item for item in snapshot["numbers"]
               if item["visible"] and item["text"].strip() == str(start_line)]
    assert len(numbers) == 1, f"Anchor line {start_line} is not uniquely visible: {snapshot}"
    top = numbers[0]["top"]
    assert isinstance(top, (int, float)) and math.isfinite(top), snapshot

    def same_line(item):
        return (item["visible"] and isinstance(item["top"], (int, float))
                and abs(item["top"] - top) < 0.5)

    assert any(same_line(item) for item in snapshot["decorations"]), (
        "Anchor decoration does not correspond to requested line number", snapshot)
    lines = [item for item in snapshot["lines"] if same_line(item)]
    assert len(lines) == 1, snapshot
    assert lines[0]["text"].replace("\u00a0", " ") == snippet.replace("\u00a0", " "), (
        "Requested line's exact source snippet differs", lines[0], snippet)


def expect_source_anchor(page, scope, filename, start_line, end_line, snippet, timeout=20):
    """Wait for actual visible DOM; no legacy table or hidden model shortcuts."""
    source = page.locator(f"{scope} .source-view")
    source.wait_for(state="visible", timeout=timeout * 1000)
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        snapshot = source.evaluate(_SNAPSHOT)
        try:
            assert_source_snapshot(snapshot, filename, start_line, end_line, snippet)
            return snapshot
        except AssertionError as error:
            last = error
            page.wait_for_timeout(100)
    raise AssertionError(f"Visible Monaco source anchor did not match: {last}")
