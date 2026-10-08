"""Read-only static verification; JSON evidence is not feature-completion proof.

Usage: python -B scripts/verify_general_python.py [--project PATH ...]
       [--expect relative/file.py::qualified.suffix] [--expect-issue CODE]
       [--output PATH]  UTF-8 JSON copy outside analyzed project roots.
Exit 0: requested checks verified; 1: failed; 2: missing/unverified contracts.
Default corpus roots have pinned symbol expectations; custom roots need --expect.
Samples and FunctionFlow projections are bounded, analysis covers the whole root.
Only codeviz is imported, never target modules. Cache round-trip is in memory.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import tokenize
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECTS = (
    "work/general-python-corpus/click/click-06b2a678741131fd577ce170e23e5ca0aeba0309/src",
    "work/general-python-corpus/requests/requests-611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60/src",
    "work/general-python-corpus/httpx/httpx-b5addb64f0161ff6bfe94c124ef76f6a1fba5254",
    "tests/fixtures/mvp_project",
)
PROFILES = {
    DEFAULT_PROJECTS[0]: (("click/core.py", "click.core.Command.invoke"),
                          ("click/decorators.py", "click.decorators.pass_context")),
    DEFAULT_PROJECTS[1]: (("requests/sessions.py", "requests.sessions.Session.request"),
                          ("requests/api.py", "requests.api.request")),
    DEFAULT_PROJECTS[2]: (("httpx/_client.py", "httpx._client.Client.send"),
                          ("httpx/_api.py", "httpx._api.request")),
    DEFAULT_PROJECTS[3]: (
        ("model.py", "Classifier.forward"),
        ("run.py", "train_epoch"),
    ),
}
PACKAGE_SCOPES = dict(zip(DEFAULT_PROJECTS[:3], ("click", "requests", "httpx"), strict=True))


def source_fingerprint(root):
    """Hash every regular target file, membership and mtime; never follow links."""
    digest = hashlib.sha256()
    identities = hashlib.sha256()
    count = 0

    def unreadable(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        dirs.sort()
        for name in sorted((*dirs, *files)):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                digest.update(
                    json.dumps([relative, "link", os.readlink(path)]).encode()
                )
            elif path.is_file():
                stat = path.stat()
                identities.update(json.dumps([relative, stat.st_dev, stat.st_ino]).encode())
                content = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(block)
                digest.update(
                    json.dumps(
                        [relative, stat.st_size, stat.st_mtime_ns, content.hexdigest()]
                    ).encode()
                )
                count += 1
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
    return {"sha256": digest.hexdigest(), "identity_sha256": identities.hexdigest(), "files": count}


def write_report(output, serialized, roots):
    """Replace the report directory entry, never write through an existing link."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        raise OSError("--output must be outside analyzed project roots")
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", dir=output.parent, prefix=f".{output.stem}-",
                                suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((serialized + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_api():
    """Load only trusted repository code; no target path enters sys.path."""
    sys.dont_write_bytecode = True
    source_root = str(REPO_ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from codeviz.analysis.project import analyze_project
    from codeviz.core import NodeKind, ViewKind
    from codeviz.persistence.codec import decode_analysis, encode_analysis
    from codeviz.ui.presenter import build_overview
    from codeviz.ui.web_contract import graph_payload, overview_payload
    from codeviz.views import ViewRequest, build_visible_graph

    return SimpleNamespace(
        analyze_project=analyze_project,
        NodeKind=NodeKind,
        ViewKind=ViewKind,
        decode_analysis=decode_analysis,
        encode_analysis=encode_analysis,
        build_overview=build_overview,
        graph_payload=graph_payload,
        overview_payload=overview_payload,
        ViewRequest=ViewRequest,
        build_visible_graph=build_visible_graph,
    )


def anchor_data(anchor):
    return {
        "file_path": anchor.file_path,
        "start_line": anchor.start_line,
        "end_line": anchor.end_line,
        "start_column": anchor.start_column,
        "end_column": anchor.end_column,
    }


def anchor_valid(root, anchor):
    """Validate source ranges using text reads only, including encoding cookies."""
    path = root / anchor.file_path
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        return False
    try:
        with tokenize.open(path) as stream:
            lines = stream.read().splitlines()
        return 1 <= anchor.start_line <= anchor.end_line <= max(1, len(lines))
    except (OSError, UnicodeError, SyntaxError, LookupError):
        return False


def verify_project(
    project,
    *,
    expected_symbols=(),
    expected_issues=(),
    sample_size=3,
    max_nodes=40,
    max_edges=80,
    package_name=None,
):
    """Return evidence, limitations and checks; failure dominates unverified."""
    for value in (sample_size, max_nodes, max_edges):
        if type(value) is not int or value < 1:
            raise ValueError("sample size and graph budgets must be positive integers")
    if package_name is not None and (not isinstance(package_name, str) or not package_name.isidentifier()):
        raise ValueError("package name must be a single Python identifier")
    root = Path(project).resolve()
    report = {
        "project": str(root),
        "status": "unverified",
        "mode": "static",
        "runtime_trace": False,
        "full_graph_returned": False,
        "feature_complete": False,
        "checks": [],
        "metrics": {},
        "expected_symbols": [],
        "symbol_samples": [],
        "flow_graphs": [],
        "cache": {},
        "analysis_elapsed_seconds": None,
        "scope": {"mode": "package_context" if package_name else "project",
                  "source_root": str(root), "module_prefix": package_name, "ignore_paths": []},
    }

    def check(name, valid, detail="", *, unavailable=False):
        report["checks"].append(
            {
                "name": name,
                "status": (
                    "unverified" if unavailable else "verified" if valid else "failed"
                ),
                "detail": detail,
            }
        )

    if not root.is_dir():
        check("project_available", False, "directory does not exist", unavailable=True)
        return report
    if package_name is not None and not (root / package_name).is_dir():
        check("package_available", False, "included package directory does not exist", unavailable=True)
        return report
    ignored = tuple(sorted(path.name for path in root.iterdir() if path.name != package_name)) \
        if package_name else ()
    report["scope"]["ignore_paths"] = list(ignored)
    try:
        before = source_fingerprint(root)
    except OSError as error:
        check("source_fingerprint", False, str(error), unavailable=True)
        return report
    report["fingerprints"] = {"before": before, "after": None}
    try:
        try:
            api = load_api()
        except (ImportError, AttributeError) as error:
            check("analysis_contract", False, str(error), unavailable=True)
            return report
        start = time.perf_counter()
        try:
            analysis = api.analyze_project(root, ignore_paths=ignored) if ignored else api.analyze_project(root)
        finally:
            report["analysis_elapsed_seconds"] = time.perf_counter() - start
        entries = sorted(
            analysis.symbol_index.entries.values(),
            key=lambda item: (item.file_path, item.source_anchor.start_line, item.id),
        )
        issues = Counter(item.code for item in analysis.issues)
        if package_name:
            check("package_context", all(
                item.relative_path.startswith(package_name + "/")
                and (item.module_name == package_name or item.module_name.startswith(package_name + "."))
                for item in analysis.files), "only included package sources, with qualified module prefix")
        report["metrics"] = {
            "files_total": len(analysis.files),
            "files_parsed": sum(item.was_parsed for item in analysis.files),
            "symbol_count": len(entries),
            "issues_by_code": dict(sorted(issues.items())),
            "generic_entries": None,
            "overview_functions": None,
            "flow_graphs": 0,
        }
        check(
            "parsed_sources",
            any(item.was_parsed for item in analysis.files),
            "partial parse failures remain visible in issues_by_code",
        )
        check(
            "analysis_internal_errors",
            not any(
                code.endswith("_ERROR")
                and code not in {"SYNTAX_ERROR", "DECODE_ERROR", "READ_ERROR"}
                for code in issues
            ),
            "internal analyzer errors must not pass as diagnostic counts",
        )
        for code in expected_issues:
            matches = [item for item in analysis.issues if item.code == code]
            check(
                "diagnostic:" + code,
                bool(matches)
                and all(
                    item.source_anchor is not None
                    and anchor_valid(root, item.source_anchor)
                    for item in matches
                ),
                "expected code must have valid source anchors",
            )
        report["issue_samples"] = [
            {
                "code": item.code,
                "source_anchor": anchor_data(item.source_anchor)
                if item.source_anchor
                else None,
            }
            for item in sorted(
                analysis.issues,
                key=lambda item: (
                    item.file_path,
                    item.code,
                    item.source_anchor.start_line if item.source_anchor else 0,
                ),
            )[:sample_size]
        ]
        functions = [
            item
            for item in entries
            if item.definition
            and item.kind in (api.NodeKind.FUNCTION, api.NodeKind.METHOD)
        ]
        report["symbol_samples"] = [
            {
                "name": item.qualified_name,
                "source_anchor": anchor_data(item.source_anchor),
            }
            for item in functions[:sample_size]
        ]
        expected_ids = []
        for file_path, name in expected_symbols:
            matches = [
                item
                for item in functions
                if item.file_path == file_path
                and (
                    item.qualified_name == name
                    or item.qualified_name.endswith("." + name)
                )
            ]
            found = len(matches) == 1
            record = {"file": file_path, "name": name, "found": found}
            if found:
                record["source_anchor"] = anchor_data(matches[0].source_anchor)
                expected_ids.append(matches[0].id)
            report["expected_symbols"].append(record)
            # Verify actual def text, not just a nonempty index and line range.
            valid = found and anchor_valid(root, matches[0].source_anchor)
            if valid:
                with tokenize.open(root / file_path) as stream:
                    line = stream.read().splitlines()[
                        matches[0].source_anchor.start_line - 1
                    ]
                valid = ("def " + matches[0].name + "(") in line.replace(" (", "(")
            check(
                "symbol:" + file_path + "::" + name,
                valid,
                "unique function/method with source def at its anchor",
            )
        if not expected_symbols:
            check(
                "expected_symbols",
                False,
                "no pinned expectations for this root",
                unavailable=True,
            )
        kind = getattr(api.ViewKind, "FUNCTION_FLOW", None)
        if kind is None or not hasattr(analysis, "generic_entries"):
            check(
                "general_python_contracts",
                False,
                "FunctionFlow or generic_entries unavailable",
                unavailable=True,
            )
            return report
        overview = api.build_overview(analysis, root)
        if not hasattr(overview, "functions"):
            check("overview_functions", False, "catalog unavailable", unavailable=True)
            return report
        generic = analysis.generic_entries
        required_entry_roots = {
            item.id
            for item in entries
            if item.definition
            and (
                (
                    item.kind == api.NodeKind.MODULE
                    and Path(item.file_path).name == "__main__.py"
                )
                or (
                    item.kind == api.NodeKind.FUNCTION
                    and item.name == "main"
                    and item.parent_symbol in analysis.symbol_index.entries
                    and analysis.symbol_index.entries[item.parent_symbol].kind
                    == api.NodeKind.MODULE
                )
            )
        }
        check(
            "generic_entry_presence",
            required_entry_roots <= {item.root_symbol_id for item in generic},
            "module-level main and __main__.py must retain structural entry candidates",
        )
        report["metrics"]["generic_entries"] = len(generic)
        report["metrics"]["overview_functions"] = len(overview.functions)
        check(
            "overview_catalog",
            {item.id for item in overview.functions} == {item.id for item in functions},
            "all function/method definitions catalogued",
        )
        overview_entries = {item.id: item for item in overview.entries}
        check(
            "generic_entry_evidence",
            all(
                item.evidence
                and anchor_valid(root, item.source_anchor)
                and item.root_symbol_id in analysis.symbol_index.entries
                and item.id in overview_entries
                and overview_entries[item.id].view_kind == kind
                for item in generic
            ),
            "structural evidence and FunctionFlow dispatch; empty is valid for libraries",
        )
        requests = [
            api.ViewRequest(
                kind, root_id=root_id, max_nodes=max_nodes, max_edges=max_edges
            )
            for root_id in dict.fromkeys(
                (*expected_ids, *(item.id for item in functions))
            )
        ]
        requests = requests[:sample_size]
        live_views = []
        for request in requests:
            graph = api.build_visible_graph(analysis, request)
            payload = api.graph_payload(graph)
            ids = {item.id for item in graph.nodes}
            source_backed = all(
                item.member_ids
                or (
                    item.source_anchors
                    and all(
                        anchor_valid(root, anchor) for anchor in item.source_anchors
                    )
                )
                for item in graph.nodes
            )
            bounded = len(graph.nodes) <= max_nodes and len(graph.edges) <= max_edges
            valid = (
                bounded
                and source_backed
                and request.root_id in ids
                and all(
                    edge.source in ids and edge.target in ids for edge in graph.edges
                )
            )
            check(
                "flow:" + request.root_id,
                valid,
                "bounded, rooted, source-backed projection",
            )
            check(
                "graph_payload:" + request.root_id,
                isinstance(payload.get("nodes"), list)
                and len(payload["nodes"]) == len(graph.nodes)
                and not any(
                    key in payload
                    for key in ("analysis", "symbol_index", "fullGraph", "runtimeTrace")
                ),
                "public payload contains bounded view only; no runtime trace",
            )
            live_views.append(payload)
            report["flow_graphs"].append(
                {
                    "root_id": request.root_id,
                    "nodes": len(graph.nodes),
                    "edges": len(graph.edges),
                    "source_backed": bool(source_backed),
                    "bounded": bounded,
                    "hidden_counts": dict(graph.hidden_counts),
                    "issues_by_code": dict(Counter(item.code for item in graph.issues)),
                }
            )
        report["metrics"]["flow_graphs"] = len(live_views)
        check(
            "sampled_function_flows",
            bool(live_views),
            "at least one ordinary function projection",
        )
        encoded = api.encode_analysis(analysis)
        restored = api.decode_analysis(json.loads(json.dumps(encoded)))
        no_ast = all(item.tree is None for item in restored.files)
        facts_equal = api.encode_analysis(restored) == encoded
        views_equal = live_views == [
            api.graph_payload(api.build_visible_graph(restored, request))
            for request in requests
        ]
        overview_equal = api.overview_payload(overview) == api.overview_payload(
            api.build_overview(restored, root)
        )
        report["cache"] = {
            "no_ast": no_ast,
            "facts_equal": facts_equal,
            "views_equal": views_equal,
            "overview_equal": overview_equal,
        }
        check(
            "cache_round_trip",
            no_ast and facts_equal and views_equal and overview_equal,
            "JSON decode drops AST and retains facts, catalog and sampled views",
        )
    except (ImportError, AttributeError, NotImplementedError) as error:
        check(
            "contract_available",
            False,
            f"{type(error).__name__}: {error}",
            unavailable=True,
        )
    except Exception as error:
        check("verification_exception", False, f"{type(error).__name__}: {error}")
    finally:
        try:
            after = source_fingerprint(root)
            report["fingerprints"]["after"] = after
            check(
                "target_unchanged",
                before == after,
                "all target files hashed before and after",
            )
        except OSError as error:
            check("target_unchanged", False, str(error))
        statuses = {item["status"] for item in report["checks"]}
        report["status"] = (
            "failed"
            if "failed" in statuses
            else ("unverified" if "unverified" in statuses else "verified")
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project",
        action="append",
        type=Path,
        help="repeatable; replaces default roots",
    )
    parser.add_argument("--expect", action="append", default=[], metavar="FILE::SYMBOL")
    parser.add_argument("--expect-issue", action="append", default=[], metavar="CODE")
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--max-nodes", type=int, default=40)
    parser.add_argument("--max-edges", type=int, default=80)
    parser.add_argument("--output", type=Path,
                        help="optional UTF-8 JSON report; stdout and verification exit codes remain unchanged")
    args = parser.parse_args(argv)
    if min(args.sample_size, args.max_nodes, args.max_edges) < 1:
        parser.error("sample size and budgets must be positive")
    expected = []
    for value in args.expect:
        parts = value.split("::")
        if len(parts) != 2 or not all(parts):
            parser.error("--expect requires FILE::SYMBOL")
        expected.append(tuple(parts))
    roots = args.project or [REPO_ROOT / item for item in DEFAULT_PROJECTS]
    output = args.output.resolve() if args.output is not None else None
    if output is not None and any(output.is_relative_to(root.resolve()) for root in roots):
        parser.error("--output must be outside analyzed project roots")
    reports = []
    for root in roots:
        profile = next(
            (
                path
                for path in DEFAULT_PROJECTS
                if root.resolve().as_posix().endswith("/" + path)
            ),
            None,
        )
        reports.append(
            verify_project(
                root,
                expected_symbols=tuple(expected) or PROFILES.get(profile, ()),
                expected_issues=tuple(args.expect_issue)
                or (("SYNTAX_ERROR",) if profile == DEFAULT_PROJECTS[3] else ()),
                sample_size=args.sample_size,
                max_nodes=args.max_nodes,
                max_edges=args.max_edges,
                package_name=PACKAGE_SCOPES.get(profile),
            )
        )
    statuses = {report["status"] for report in reports}
    serialized = json.dumps(
        {
            "projects": reports,
            "feature_complete": False,
            "limitations": "Static sampled checks only; no runtime execution or trace; not full Python semantics.",
        },
        ensure_ascii=True,
        indent=2,
    )
    print(serialized)
    if output is not None:
        try:
            write_report(output, serialized, roots)
            for report in reports:
                before = report.get("fingerprints", {}).get("before")
                if before is not None and source_fingerprint(Path(report["project"])) != before:
                    raise OSError("target changed after report output")
        except OSError as error:
            print(f"cannot write report: {error}", file=sys.stderr)
            return 1
    return 1 if "failed" in statuses else 2 if "unverified" in statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
