"""Private, versioned JSON analysis cache; never writes to analyzed projects."""

import json
import os
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from codeviz.analysis.human_tasks import (
    MAX_HUMAN_TASK_FACTS,
    ArtifactAccessFact,
    HumanTaskEntryFact,
    StaticTaskFact,
    has_valid_human_task_fact_id,
)
from codeviz.analysis.human_understanding import (
    MAX_FACTS,
    AuthorStatementFact,
    HumanOperationKind,
    OperationFact,
    external_operation_kind,
    has_valid_human_fact_id,
)
from codeviz.analysis.model_parameter_sharing import (
    MAX_PARAMETER_SHARING_FACTS,
    has_valid_parameter_sharing_id,
)
from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import Confidence, EdgeType, NodeKind, NodeOrigin, SourceAnchor
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.persistence.fingerprint import (
    ANALYSIS_VERSION,
    PARSER_VERSION,
    SEMANTIC_VERSION,
    ProjectFingerprint,
)
from codeviz.views import ViewState

_SCHEMA_VERSION = 1
_MAX_CACHE_BYTES = 64_000_000
_MAX_STATE_BYTES = 1_000_000


class CacheError(ValueError):
    """A cache cannot be safely read or written; analysis can still proceed."""


class CacheVersionMismatch(CacheError):
    """Old analysis requires reanalysis, not corruption recovery."""


def _validate_source_path(path: str) -> None:
    try:
        if (type(path) is not str or SourceAnchor(path, 1).file_path != path
                or not path.casefold().endswith(".py")):
            raise ValueError("invalid source path")
    except (TypeError, ValueError):
        raise CacheError("analysis cache source inventory mismatch") from None


def _validate_analysis_sources(analysis: ProjectAnalysis,
                               fingerprint: ProjectFingerprint) -> None:
    """Verify persisted provenance without reading current project sources."""
    expected: dict[str, str] = {}
    for path, digest in fingerprint.files:
        _validate_source_path(path)
        if path in expected:
            raise CacheError("analysis cache source inventory mismatch")
        expected[path] = digest
    seen: set[str] = set()
    for parsed in analysis.files:
        path = parsed.relative_path
        _validate_source_path(path)
        if path in seen:
            raise CacheError("analysis cache source inventory mismatch")
        seen.add(path)
        if path not in expected:
            raise CacheError("analysis cache source inventory mismatch")
        if parsed.content_hash is None:
            if parsed.was_parsed:
                raise CacheError("analysis cache source hash mismatch")
        elif parsed.content_hash != expected[path]:
            raise CacheError("analysis cache source hash mismatch")
    if seen != expected.keys():
        raise CacheError("analysis cache source inventory mismatch")
    _validate_human_understanding(analysis)
    _validate_human_tasks(analysis)
    _validate_parameter_sharings(analysis)


def _anchor_contains(owner: SourceAnchor, fact: SourceAnchor) -> bool:
    if owner.file_path != fact.file_path or owner.end_line is None or fact.end_line is None:
        return False
    if fact.start_line < owner.start_line or fact.end_line > owner.end_line:
        return False
    if (fact.start_line == owner.start_line and owner.start_column is not None
            and (fact.start_column is None or fact.start_column < owner.start_column)):
        return False
    return not (
        fact.end_line == owner.end_line and owner.end_column is not None
        and (fact.end_column is None or fact.end_column > owner.end_column)
    )


def _return_kind_from_source_witness(anchor: SourceAnchor) -> HumanOperationKind | None:
    if anchor.start_column is None or anchor.end_column is None or anchor.end_line is None:
        return None
    if anchor.end_line == anchor.start_line and anchor.end_column - anchor.start_column == 6:
        return HumanOperationKind.RETURN_EMPTY
    return HumanOperationKind.RETURN_VALUE


def _validate_human_understanding(analysis: ProjectAnalysis) -> None:
    """Cross-check derived fact references against this exact analysis snapshot."""
    parsed_files = {parsed.relative_path: parsed for parsed in analysis.files}
    owners = analysis.symbol_index.entries
    allowed_owner_kinds = {
        NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION,
        NodeKind.METHOD, NodeKind.LEXICAL_SCOPE,
    }
    facts = analysis.human_understanding
    all_facts: tuple[AuthorStatementFact | OperationFact, ...] = (
        *facts.author_statements, *facts.operations,
    )
    truncation_issues = [issue for issue in analysis.issues
                         if issue.code == "UNDERSTANDING_FACTS_TRUNCATED"]
    if facts.omitted_fact_count == 0:
        truncation_consistent = not truncation_issues
    else:
        truncation_consistent = (
            len(all_facts) == MAX_FACTS
            and len(truncation_issues) == 1
            and truncation_issues[0].message == (
                f"omitted {facts.omitted_fact_count} human-understanding facts"
            )
            and truncation_issues[0].file_path in parsed_files
            and truncation_issues[0].source_anchor
            == SourceAnchor(truncation_issues[0].file_path, 1)
        )
    if not truncation_consistent:
        raise CacheError("human-understanding provenance mismatch")
    graph = analysis.flow.graph if analysis.flow is not None else None
    external_ids = ({node.id for node in analysis.resolution.external_symbols}
                    if analysis.resolution is not None else set())
    for fact in all_facts:
        owner = owners.get(fact.owner_symbol_id)
        parsed = parsed_files.get(fact.source_anchor.file_path)
        if (owner is None or owner.kind not in allowed_owner_kinds
                or owner.file_path != fact.source_anchor.file_path
                or parsed is None or not parsed.was_parsed
                or fact.source_anchor.end_line is None
                or fact.source_anchor.end_line > parsed.line_count
                or not _anchor_contains(owner.source_anchor, fact.source_anchor)
                or not has_valid_human_fact_id(fact)):
            raise CacheError("human-understanding provenance mismatch")
    if any(owners[fact.owner_symbol_id].kind is NodeKind.LEXICAL_SCOPE
           for fact in facts.author_statements):
        raise CacheError("human-understanding provenance mismatch")
    for operation in facts.operations:
        if operation.kind in (HumanOperationKind.RETURN_VALUE, HumanOperationKind.RETURN_EMPTY):
            if (graph is None
                    or owners[operation.owner_symbol_id].kind in (NodeKind.MODULE, NodeKind.CLASS)):
                raise CacheError("human-understanding provenance mismatch")
            return_steps = [
                node for node in graph.nodes.values()
                if node.kind is NodeKind.FLOW_STEP
                and node.metadata.get("statement_type") == "Return"
                and node.source_anchors == (operation.source_anchor,)
            ]
            if (_return_kind_from_source_witness(operation.source_anchor) is not operation.kind
                    or not any(
                        edge.source == operation.owner_symbol_id
                        and edge.target == step.id
                        and edge.type is EdgeType.CONTAINS
                        and edge.source_anchor == operation.source_anchor
                        for step in return_steps for edge in graph.edges
                    )):
                raise CacheError("human-understanding provenance mismatch")
            continue
        if graph is None or operation.callsite_id is None or operation.target_symbol_id is None:
            raise CacheError("human-understanding provenance mismatch")
        callsite = graph.nodes.get(operation.callsite_id)
        target = graph.nodes.get(operation.target_symbol_id)
        call_edges = [edge for edge in graph.edges
                      if edge.source == operation.callsite_id
                      and edge.target == operation.target_symbol_id
                      and edge.type is EdgeType.CALLS]
        references = [reference for reference in analysis.symbol_index.references
                      if reference.scope_symbol_id == operation.owner_symbol_id
                      and callsite is not None and reference.name == callsite.name
                      and reference.source_anchor.start_line == operation.source_anchor.start_line
                      and reference.source_anchor.start_column == operation.source_anchor.start_column
                      and _anchor_contains(operation.source_anchor, reference.source_anchor)]
        reference_targets: tuple[str, ...] = ()
        if len(references) == 1:
            reference_targets = ((references[0].resolved_definition_id,)
                                 if references[0].resolved_definition_id is not None
                                 else references[0].possible_target_ids)
        if (callsite is None or callsite.kind is not NodeKind.CALL_SITE
                or callsite.metadata.get("scope_symbol_id") != operation.owner_symbol_id
                or callsite.source_anchors != (operation.source_anchor,)
                or target is None or target.origin is not NodeOrigin.EXTERNAL
                or target.id not in external_ids
                or external_operation_kind(target.qualified_name) is not operation.kind
                or len(call_edges) != 1 or call_edges[0].confidence is not operation.confidence
                or len(references) != 1 or reference_targets != (target.id,)
                or references[0].confidence is not operation.confidence):
            raise CacheError("human-understanding provenance mismatch")


def _validate_human_tasks(analysis: ProjectAnalysis) -> None:
    facts = analysis.human_tasks
    all_facts: tuple[HumanTaskEntryFact | StaticTaskFact | ArtifactAccessFact, ...] = (
        *facts.entries, *facts.tasks, *facts.artifacts,
    )
    truncation_issues = [issue for issue in analysis.issues
                         if issue.code == "HUMAN_TASK_FACTS_TRUNCATED"]
    if facts.omitted_fact_count == 0:
        truncation_consistent = not truncation_issues
    else:
        truncation_consistent = (
            len(all_facts) == MAX_HUMAN_TASK_FACTS
            and len(truncation_issues) == 1
            and truncation_issues[0].message
            == f"omitted {facts.omitted_fact_count} human-task facts"
        )
    if not truncation_consistent:
        raise CacheError("human-task provenance mismatch")
    owners = analysis.symbol_index.entries
    parsed_files = {parsed.relative_path: parsed for parsed in analysis.files}
    graph = analysis.flow.graph if analysis.flow is not None else None
    if graph is None:
        if all_facts:
            raise CacheError("human-task provenance mismatch")
        return
    evidence: dict[str, StaticTaskFact | ArtifactAccessFact] = {
        fact.id: fact for fact in facts.tasks
    }
    evidence.update({fact.id: fact for fact in facts.artifacts})
    for fact in all_facts:
        owner_id = (fact.root_symbol_id if isinstance(fact, HumanTaskEntryFact)
                    else fact.owner_symbol_id)
        owner = owners.get(owner_id)
        parsed = parsed_files.get(fact.source_anchor.file_path)
        if (owner is None
                or owner.kind not in (NodeKind.MODULE, NodeKind.CLASS,
                                      NodeKind.FUNCTION, NodeKind.METHOD)
                or parsed is None or not parsed.was_parsed
                or fact.source_anchor.end_line is None
                or fact.source_anchor.end_line > parsed.line_count
                or not _anchor_contains(owner.source_anchor, fact.source_anchor)
                or not has_valid_human_task_fact_id(fact)):
            raise CacheError("human-task provenance mismatch")
        if isinstance(fact, HumanTaskEntryFact):
            root_targets = {
                target for item in evidence.values()
                if getattr(item, "owner_symbol_id", None) == owner_id
                and isinstance(item, StaticTaskFact)
                for target in item.target_symbol_ids
            }
            if (fact.source_anchor != owner.source_anchor
                    or any(item not in evidence for item in fact.evidence_fact_ids)
                    or any(getattr(evidence[item], "owner_symbol_id", None) not in (
                        owner_id, *root_targets,
                    )
                           for item in fact.evidence_fact_ids)):
                raise CacheError("human-task provenance mismatch")
            continue
        control = graph.nodes.get(fact.control_node_id)
        if (control is None
                or control.kind not in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
                or len(control.source_anchors) != 1
                or not (_anchor_contains(control.source_anchors[0], fact.source_anchor)
                        or _anchor_contains(fact.source_anchor, control.source_anchors[0]))
                or not any(edge.source == owner_id and edge.target == control.id
                           and edge.type is EdgeType.CONTAINS for edge in graph.edges)):
            raise CacheError("human-task provenance mismatch")
        callsite_ids = ((fact.callsite_id,) if isinstance(fact, ArtifactAccessFact)
                        and fact.callsite_id is not None
                        else fact.callsite_ids if isinstance(fact, StaticTaskFact) else ())
        for callsite_id in callsite_ids:
            callsite = graph.nodes.get(callsite_id)
            if (callsite is None or callsite.kind is not NodeKind.CALL_SITE
                    or callsite.metadata.get("scope_symbol_id") != owner_id
                    or len(callsite.source_anchors) != 1
                    or not _anchor_contains(owner.source_anchor, callsite.source_anchors[0])):
                raise CacheError("human-task provenance mismatch")
        if isinstance(fact, ArtifactAccessFact):
            if (fact.callsite_id is not None
                    and graph.nodes[fact.callsite_id].source_anchors != (fact.source_anchor,)):
                raise CacheError("human-task provenance mismatch")
            continue
        targets = tuple(dict.fromkeys(
            edge.target for edge in graph.edges
            if edge.source in fact.callsite_ids and edge.type is EdgeType.CALLS
        ))[:8]
        if targets != fact.target_symbol_ids:
            raise CacheError("human-task provenance mismatch")


def _instance_source_path(name: str) -> tuple[str, ...] | None:
    normalized = name.replace("]", "").replace("[", ".")
    parts = tuple(normalized.split("."))
    return parts if parts and all(part.isidentifier() for part in parts) else None


def _parameter_attributes(layer_type: str) -> frozenset[str]:
    if layer_type == "torch.nn.Embedding":
        return frozenset({"weight"})
    if layer_type in ("torch.nn.Linear", "torch.nn.LayerNorm"):
        return frozenset({"weight", "bias"})
    if layer_type.rsplit(".", 1)[-1] == "LayerNorm":
        return frozenset({"weight"})
    return frozenset()


def _validate_parameter_sharings(analysis: ProjectAnalysis) -> None:
    model = analysis.model
    if model is None:
        return
    facts = model.parameter_sharings
    if len(facts) > MAX_PARAMETER_SHARING_FACTS:
        raise CacheError("parameter-sharing provenance mismatch")
    truncation = [issue for issue in model.issues
                  if issue.code == "MODEL_PARAMETER_SHARING_TRUNCATED"]
    if truncation and (len(facts) != MAX_PARAMETER_SHARING_FACTS or len(truncation) != 1):
        raise CacheError("parameter-sharing provenance mismatch")
    unresolved_anchors = {
        issue.source_anchor for issue in model.issues
        if issue.code == "MODEL_PARAMETER_SHARING_UNRESOLVED"
        and issue.source_anchor is not None
    }
    definitions = {item.symbol_id: item for item in model.definitions}
    instances = {item.id: item for item in model.instances}
    owners = analysis.symbol_index.entries
    parsed_files = {parsed.relative_path: parsed for parsed in analysis.files}
    graph = analysis.flow.graph if analysis.flow is not None else None
    confidence_strength = {
        Confidence.UNRESOLVED: 0, Confidence.PROBABLE: 1, Confidence.EXACT: 2,
    }
    for fact in facts:
        definition = definitions.get(fact.model_definition_id)
        owner = owners.get(fact.owner_symbol_id)
        assigned = instances.get(fact.assigned_instance_id)
        source = instances.get(fact.source_instance_id)
        parsed = parsed_files.get(fact.source_anchor.file_path)
        if (definition is None or owner is None or assigned is None or source is None
                or parsed is None or not parsed.was_parsed or graph is None
                or owner.kind is not NodeKind.METHOD or owner.name != "__init__"
                or owner.parent_symbol != definition.symbol_id
                or assigned.owner_definition_id != definition.symbol_id
                or source.owner_definition_id != definition.symbol_id
                or assigned.id == source.id
                or fact.assigned_attribute not in _parameter_attributes(assigned.layer_type)
                or fact.source_attribute not in _parameter_attributes(source.layer_type)
                or fact.source_anchor in unresolved_anchors
                or not _anchor_contains(owner.source_anchor, fact.source_anchor)
                or not has_valid_parameter_sharing_id(fact)
                or confidence_strength[fact.confidence] > min(
                    confidence_strength[assigned.confidence],
                    confidence_strength[source.confidence],
                )):
            raise CacheError("parameter-sharing provenance mismatch")
        step = next((node for node in graph.nodes.values()
                     if node.kind is NodeKind.FLOW_STEP
                     and node.metadata.get("statement_type") in ("Assign", "AnnAssign")
                     and node.source_anchors == (fact.source_anchor,)), None)
        if (step is None or not any(edge.source == owner.id and edge.target == step.id
                                    and edge.type is EdgeType.CONTAINS
                                    for edge in graph.edges)):
            raise CacheError("parameter-sharing provenance mismatch")
        if any(node.kind is NodeKind.CONTROL_BLOCK and len(node.source_anchors) == 1
               and node.source_anchors[0] != fact.source_anchor
               and _anchor_contains(node.source_anchors[0], fact.source_anchor)
               for node in graph.nodes.values()):
            raise CacheError("parameter-sharing provenance mismatch")
        assigned_path = _instance_source_path(assigned.name)
        source_path = _instance_source_path(source.name)
        if assigned_path is None or source_path is None:
            raise CacheError("parameter-sharing provenance mismatch")
        target_name = assigned_path[-1]
        target_qualifier = "self" + "".join(f".{item}" for item in assigned_path[:-1])
        source_qualifier = "self" + "".join(f".{item}" for item in source_path)
        references = [reference for reference in analysis.symbol_index.references
                      if reference.scope_symbol_id == owner.id
                      and _anchor_contains(fact.source_anchor, reference.source_anchor)]
        target_refs = [reference for reference in references
                       if reference.name == target_name
                       and reference.qualifier == target_qualifier
                       and reference.source_anchor.start_column == fact.source_anchor.start_column]
        source_refs = [reference for reference in references
                       if reference.name == fact.source_attribute
                       and reference.qualifier == source_qualifier
                       and reference.source_anchor.end_column == fact.source_anchor.end_column]
        lhs_range_valid = False
        if len(target_refs) == 1 and len(source_refs) == 1:
            target_anchor = target_refs[0].source_anchor
            source_anchor = source_refs[0].source_anchor
            lhs_range_valid = (
                fact.assigned_attribute == fact.source_attribute
                and target_anchor.file_path == fact.source_anchor.file_path
                and source_anchor.file_path == fact.source_anchor.file_path
                and target_anchor.start_line == fact.source_anchor.start_line
                and target_anchor.start_column == fact.source_anchor.start_column
                and source_anchor.end_line == fact.source_anchor.end_line
                and source_anchor.end_column == fact.source_anchor.end_column
            )
            if (lhs_range_valid and target_anchor.end_line == source_anchor.start_line
                    and target_anchor.end_column is not None
                    and source_anchor.start_column is not None):
                lhs_range_valid = (
                    target_anchor.end_column + len(fact.assigned_attribute) + 1
                    < source_anchor.start_column
                )
        disabled_evidence = "".join((*assigned.evidence, *source.evidence)).replace(" ", "")
        if (len(target_refs) != 1 or len(source_refs) != 1 or not lhs_range_valid
                or (fact.assigned_attribute == "bias" or fact.source_attribute == "bias")
                and "bias=False" in disabled_evidence
                or "elementwise_affine=False" in disabled_evidence):
            raise CacheError("parameter-sharing provenance mismatch")


@dataclass(frozen=True, slots=True)
class CachedAnalysis:
    analysis: ProjectAnalysis
    fingerprint: ProjectFingerprint


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "codeviz"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "codeviz"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "codeviz"


class CacheStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    def _project_dir(self, project_root: Path) -> tuple[Path, Path]:
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise CacheError("project root is not a directory")
        data_root = self.data_dir.resolve()
        if data_root.is_relative_to(root):
            raise CacheError("cache directory must be outside the analyzed project")
        key = sha256(str(root).encode("utf-8")).hexdigest()
        directory = data_root / "projects" / key
        if directory.exists() and not directory.resolve().is_relative_to(data_root):
            raise CacheError("cache directory resolves outside application data")
        return root, directory

    def analysis_path(self, project_root: Path) -> Path:
        return self._project_dir(project_root)[1] / "analysis.json"

    def _write_json(self, target: Path, document: object, max_bytes: int) -> None:
        try:
            content = json.dumps(document, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
            if len(content) > max_bytes:
                raise CacheError("cache record exceeds size limit")
            directory = target.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not directory.resolve().is_relative_to(self.data_dir.resolve()):
                raise CacheError("cache directory resolves outside application data")
            temporary: Path | None = None
            try:
                with NamedTemporaryFile(mode="wb", dir=directory, prefix=f".{target.stem}-",
                                        suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError(f"cannot save cache record: {error}") from error

    @staticmethod
    def _read_json(target: Path, max_bytes: int) -> object | None:
        if not target.exists():
            return None
        if target.is_symlink():
            raise CacheError("cache record is a symbolic link")
        try:
            if target.stat().st_size > max_bytes:
                raise CacheError("cache record exceeds size limit")
            with target.open("rb") as source:
                content = source.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise CacheError("cache record exceeds size limit")
            decoded = cast(object, json.loads(content))
            if decoded is None:
                raise CacheError("cache record cannot be null")
            return decoded
        except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError(f"cannot load cache record: {error}") from error

    @staticmethod
    def _project_document(root: Path, data: object) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "project_root": str(root), "data": data}

    @staticmethod
    def _project_data(payload: object, root: Path) -> object:
        if (not isinstance(payload, dict) or set(payload) != {
            "schema_version", "project_root", "data"
        } or type(payload["schema_version"]) is not int or
                payload["schema_version"] != _SCHEMA_VERSION or
                payload["project_root"] != str(root)):
            raise CacheError("cache record has wrong schema or project")
        return payload["data"]

    def save_analysis(self, project_root: Path, analysis: ProjectAnalysis,
                      fingerprint: ProjectFingerprint) -> None:
        root, directory = self._project_dir(project_root)
        try:
            fingerprint_payload = fingerprint.to_payload()
            validated = ProjectFingerprint.from_payload(fingerprint_payload)
            if (validated.analysis_version != ANALYSIS_VERSION or
                    validated.semantic_version != SEMANTIC_VERSION or
                    validated.parser_version != PARSER_VERSION):
                raise CacheVersionMismatch("analysis versions changed; reanalysis required")
            _validate_analysis_sources(analysis, validated)
            document = {
                "schema_version": _SCHEMA_VERSION,
                "project_root": str(root),
                "fingerprint": fingerprint_payload,
                "analysis": encode_analysis(analysis),
            }
        except (TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError("cannot save analysis cache") from None
        self._write_json(directory / "analysis.json", document, _MAX_CACHE_BYTES)

    def load_analysis(self, project_root: Path) -> CachedAnalysis | None:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "analysis.json", _MAX_CACHE_BYTES)
        if payload is None:
            return None
        try:
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version", "project_root", "fingerprint", "analysis"
            } or type(payload["schema_version"]) is not int:
                raise CacheError("analysis cache schema is invalid")
            if payload["schema_version"] != _SCHEMA_VERSION:
                raise CacheError("analysis cache schema version is unsupported")
            if payload["project_root"] != str(root):
                raise CacheError("analysis cache belongs to another project")
            fingerprint = ProjectFingerprint.from_payload(payload["fingerprint"])
            if (fingerprint.analysis_version != ANALYSIS_VERSION or
                    fingerprint.semantic_version != SEMANTIC_VERSION or
                    fingerprint.parser_version != PARSER_VERSION):
                raise CacheVersionMismatch("analysis versions changed; reanalysis required")
            analysis = decode_analysis(payload["analysis"])
            _validate_analysis_sources(analysis, fingerprint)
            return CachedAnalysis(analysis, fingerprint)
        except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError("cannot load analysis cache") from None

    def save_view_state(self, project_root: Path, state: ViewState) -> None:
        root, directory = self._project_dir(project_root)
        self._write_json(directory / "view-state.json",
                         self._project_document(root, state.to_dict()), _MAX_STATE_BYTES)

    def load_view_state(self, project_root: Path) -> ViewState:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "view-state.json", _MAX_STATE_BYTES)
        if payload is None:
            return ViewState()
        data = self._project_data(payload, root)
        if not isinstance(data, dict):
            raise CacheError("view state has invalid fields")
        try:
            return ViewState.from_dict(data)
        except (TypeError, ValueError) as error:
            raise CacheError(f"invalid view state: {error}") from error

    def save_annotations(self, project_root: Path, notes: dict[str, str]) -> None:
        if (len(notes) > 1000 or any(not key or type(key) is not str or
                                     type(value) is not str or len(value) > 4000
                                     for key, value in notes.items())):
            raise CacheError("annotations exceed their limits")
        root, directory = self._project_dir(project_root)
        self._write_json(directory / "annotations.json",
                         self._project_document(root, notes), _MAX_STATE_BYTES)

    def load_annotations(self, project_root: Path) -> dict[str, str]:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "annotations.json", _MAX_STATE_BYTES)
        if payload is None:
            return {}
        data = self._project_data(payload, root)
        if (not isinstance(data, dict) or len(data) > 1000 or
                any(type(key) is not str or not key or type(value) is not str or
                    len(value) > 4000 for key, value in data.items())):
            raise CacheError("invalid annotations record")
        return data

    def recent_projects(self) -> tuple[Path, ...]:
        payload = self._read_json(self.data_dir.resolve() / "recent-projects.json",
                                  _MAX_STATE_BYTES)
        if payload is None:
            return ()
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "paths"}
                or payload["schema_version"] != _SCHEMA_VERSION or
                not isinstance(payload["paths"], list) or len(payload["paths"]) > 10 or
                any(type(path) is not str for path in payload["paths"])):
            raise CacheError("invalid recent projects record")
        return tuple(path for raw in payload["paths"]
                     if (path := Path(raw)).is_dir())

    def record_recent_project(self, project_root: Path) -> None:
        root, _ = self._project_dir(project_root)
        previous = self.recent_projects()
        paths = [str(root), *(str(path) for path in previous if path != root)][:10]
        self._write_json(self.data_dir.resolve() / "recent-projects.json",
                         {"schema_version": _SCHEMA_VERSION, "paths": paths},
                         _MAX_STATE_BYTES)
