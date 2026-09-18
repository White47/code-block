"""Repeatable local measurements; no wall-time assertion for variable CI hosts."""

import json
import sys
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.codec import decode_analysis
from codeviz.persistence.fingerprint import fingerprint_project
from codeviz.persistence.store import CacheStore
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph

PROJECT = Path(__file__).parent / "fixtures" / "mvp_project"


def _timed(action):
    start = perf_counter()
    result = action()
    return result, round((perf_counter() - start) * 1000, 3)


def main() -> None:
    trace_memory = "--trace-memory" in sys.argv[1:]
    if trace_memory:
        tracemalloc.start()
    analysis, analyze_ms = _timed(lambda: analyze_project(PROJECT))
    if trace_memory:
        analyze_peak_kib = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
    classifier = next(item for item in analysis.model.definitions
                      if item.qualified_name == "model.Classifier")
    training = next(item for item in analysis.training.entry_points
                    if item.name == "train_epoch")
    architecture, architecture_ms = _timed(lambda: build_visible_graph(
        analysis, ViewRequest(ViewKind.ARCHITECTURE, root_id=classifier.symbol_id)))
    execution, execution_ms = _timed(lambda: build_visible_graph(
        analysis, ViewRequest(ViewKind.EXECUTION, root_id=training.id)))
    matches, search_ms = _timed(lambda: search_project(analysis, "loss"))
    fingerprint, fingerprint_ms = _timed(lambda: fingerprint_project(PROJECT))
    with TemporaryDirectory() as data_dir:
        store = CacheStore(Path(data_dir))
        if trace_memory:
            tracemalloc.reset_peak()
        _, save_ms = _timed(lambda: store.save_analysis(PROJECT, analysis, fingerprint))
        cache_bytes = store.analysis_path(PROJECT).stat().st_size
        if trace_memory:
            save_peak_kib = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
            tracemalloc.reset_peak()
        loaded, load_ms = _timed(lambda: store.load_analysis(PROJECT))
        if loaded is None:
            raise RuntimeError("cache round-trip returned no analysis")
        if trace_memory:
            load_peak_kib = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
            tracemalloc.reset_peak()
            payload, json_ms = _timed(lambda: json.loads(
                store.analysis_path(PROJECT).read_bytes()))
            json_peak_kib = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
            tracemalloc.reset_peak()
            _, decode_ms = _timed(lambda: decode_analysis(payload["analysis"]))
            decode_peak_kib = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
            tracemalloc.stop()
    report = {
        "fixture": str(PROJECT),
        "files_discovered": len(analysis.files),
        "files_parsed": sum(file.was_parsed for file in analysis.files),
        "symbols": len(analysis.symbol_index.entries),
        "code_nodes": len(analysis.flow.graph.nodes),
        "semantic_components": len(analysis.semantic.graph.components),
        "architecture_visible_nodes": len(architecture.nodes),
        "execution_visible_nodes": len(execution.nodes),
        "search_results": len(matches.results),
        "cache_bytes": cache_bytes,
        "timings_ms": {
            "analyze": analyze_ms,
            "architecture_view": architecture_ms,
            "execution_view": execution_ms,
            "search": search_ms,
            "fingerprint": fingerprint_ms,
            "cache_save": save_ms,
            "cache_load": load_ms,
        },
    }
    if trace_memory:
        report["timings_ms"].update({"json_parse": json_ms, "domain_decode": decode_ms})
        report["tracemalloc_peak_kib"] = {
            "analyze": analyze_peak_kib,
            "cache_save": save_peak_kib,
            "cache_load": load_peak_kib,
            "json_parse": json_peak_kib,
            "domain_decode": decode_peak_kib,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
