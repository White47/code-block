"""Optional, disposable multi-file baseline; generates only temporary source."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import fingerprint_project
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph


def _timed(action):
    start = perf_counter()
    result = action()
    return result, round((perf_counter() - start) * 1000, 3)


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    if not 1 <= count <= 200:
        raise SystemExit("file count must be between 1 and 200")
    with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
        project = Path(project_dir)
        for index in range(count):
            (project / f"module_{index:03}.py").write_text(
                f"def helper_{index}(value):\n"
                "    result = value + 1\n"
                "    return result\n\n"
                f"def run_{index}(item):\n"
                f"    return helper_{index}(item)\n", encoding="utf-8")
        analysis, analyze_ms = _timed(lambda: analyze_project(project))
        first = next(entry for entry in analysis.symbol_index.entries.values()
                     if entry.name == "run_0")
        view, view_ms = _timed(lambda: build_visible_graph(
            analysis, ViewRequest(ViewKind.SYMBOL, root_id=first.id, selected_id=first.id)))
        _, search_ms = _timed(lambda: search_project(analysis, "helper"))
        fingerprint, fingerprint_ms = _timed(lambda: fingerprint_project(project))
        store = CacheStore(Path(data_dir))
        _, save_ms = _timed(lambda: store.save_analysis(project, analysis, fingerprint))
        loaded, load_ms = _timed(lambda: store.load_analysis(project))
        if loaded is None:
            raise RuntimeError("cache round-trip returned no analysis")
        print(json.dumps({
            "files": len(analysis.files),
            "symbols": len(analysis.symbol_index.entries),
            "code_nodes": len(analysis.flow.graph.nodes),
            "visible_nodes": len(view.nodes),
            "cache_bytes": store.analysis_path(project).stat().st_size,
            "timings_ms": {"analyze": analyze_ms, "symbol_view": view_ms,
                           "search": search_ms, "fingerprint": fingerprint_ms,
                           "cache_save": save_ms, "cache_load": load_ms},
        }, indent=2))


if __name__ == "__main__":
    main()
