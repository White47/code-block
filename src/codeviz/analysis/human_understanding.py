"""Bounded, source-backed facts for later human-readable projections.

Fact IDs are derived cache identities. They include the lexical owner, fact
kind, and canonical source anchor; they are not replacements for symbol IDs.
"""

import ast
import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from codeviz.core import Confidence, NodeKind, NodeOrigin, SourceAnchor

from .flow import FlowAnalysis
from .lexical_scopes import ImplicitScopeVisitor, ScopeLocations, definition_expressions
from .python import ParsedFile
from .resolution import ResolutionResult
from .symbols import SymbolReference

MAX_FACTS = 512
MAX_AUTHOR_TEXT_BYTES = 240


class HumanOperationKind(StrEnum):
    RETURN_VALUE = "return_value"
    RETURN_EMPTY = "return_empty"
    STANDARD_OUTPUT = "standard_output"
    COUNT = "count"
    SUM = "sum"
    CONVERSION = "conversion"


_EXTERNAL_OPERATION_KINDS = {
    "builtins.print": HumanOperationKind.STANDARD_OUTPUT,
    "builtins.len": HumanOperationKind.COUNT,
    "builtins.sum": HumanOperationKind.SUM,
    # H1 intentionally recognizes only the conversion identity named in the
    # reviewed static evidence. Other conversion-like builtins remain unknown.
    "builtins.int": HumanOperationKind.CONVERSION,
}


def external_operation_kind(qualified_name: str) -> HumanOperationKind | None:
    """Map only reviewed external identities; never classify from call spelling."""
    return _EXTERNAL_OPERATION_KINDS.get(qualified_name)


def _valid_id(value: object) -> bool:
    return (type(value) is str and value.startswith("understanding_")
            and len(value) == 78
            and all(char in "0123456789abcdef" for char in value[14:]))


@dataclass(frozen=True, slots=True)
class AuthorStatementFact:
    id: str
    owner_symbol_id: str
    text: str
    source_anchor: SourceAnchor
    truncated: bool
    confidence: Confidence = Confidence.PROBABLE

    def __post_init__(self) -> None:
        if not _valid_id(self.id) or type(self.owner_symbol_id) is not str or not self.owner_symbol_id:
            raise ValueError("author statement requires stable fact and owner IDs")
        if (type(self.text) is not str or not self.text.strip()
                or len(self.text.encode("utf-8")) > MAX_AUTHOR_TEXT_BYTES):
            raise ValueError("author statement text must be nonempty and bounded")
        if not isinstance(self.source_anchor, SourceAnchor) or type(self.truncated) is not bool:
            raise ValueError("author statement requires one source anchor and truncation state")
        if self.confidence is not Confidence.PROBABLE:
            raise ValueError("source author statements must remain Probable")


@dataclass(frozen=True, slots=True)
class OperationFact:
    id: str
    owner_symbol_id: str
    kind: HumanOperationKind
    source_anchor: SourceAnchor
    callsite_id: str | None = None
    target_symbol_id: str | None = None
    confidence: Confidence = Confidence.UNRESOLVED

    def __post_init__(self) -> None:
        if not _valid_id(self.id) or type(self.owner_symbol_id) is not str or not self.owner_symbol_id:
            raise ValueError("operation requires stable fact and owner IDs")
        if not isinstance(self.kind, HumanOperationKind) or not isinstance(self.source_anchor, SourceAnchor):
            raise ValueError("operation requires a known kind and one source anchor")
        if self.callsite_id is not None and (type(self.callsite_id) is not str or not self.callsite_id):
            raise ValueError("operation callsite ID must be nonempty")
        if self.target_symbol_id is not None and (
                type(self.target_symbol_id) is not str or not self.target_symbol_id):
            raise ValueError("operation target ID must be nonempty")
        if not isinstance(self.confidence, Confidence):
            raise ValueError("operation requires a known confidence")
        is_return = self.kind in (
            HumanOperationKind.RETURN_VALUE, HumanOperationKind.RETURN_EMPTY,
        )
        if is_return and (self.callsite_id is not None or self.target_symbol_id is not None
                          or self.confidence is not Confidence.EXACT):
            raise ValueError("return operations are Exact syntax facts without call targets")
        if not is_return and (self.callsite_id is None or self.target_symbol_id is None
                              or self.confidence is Confidence.UNRESOLVED):
            raise ValueError("call operations require resolved callsite and target evidence")


@dataclass(frozen=True, slots=True)
class HumanUnderstandingFacts:
    author_statements: tuple[AuthorStatementFact, ...] = ()
    operations: tuple[OperationFact, ...] = ()
    omitted_fact_count: int = 0

    def __post_init__(self) -> None:
        if (type(self.author_statements) is not tuple
                or any(not isinstance(item, AuthorStatementFact) for item in self.author_statements)
                or type(self.operations) is not tuple
                or any(not isinstance(item, OperationFact) for item in self.operations)):
            raise ValueError("human-understanding facts must use registered immutable values")
        facts: tuple[AuthorStatementFact | OperationFact, ...] = (
            *self.author_statements, *self.operations,
        )
        if len(facts) > MAX_FACTS or len({fact.id for fact in facts}) != len(facts):
            raise ValueError("human-understanding facts exceed budget or duplicate IDs")
        if type(self.omitted_fact_count) is not int or self.omitted_fact_count < 0:
            raise ValueError("omitted fact count must be a nonnegative integer")


def _anchor(parsed: ParsedFile, node: ast.AST) -> SourceAnchor:
    start = getattr(node, "lineno", 1)
    return SourceAnchor(
        parsed.relative_path,
        start,
        start_column=getattr(node, "col_offset", None),
        end_line=getattr(node, "end_lineno", start),
        end_column=getattr(node, "end_col_offset", None),
    )


def _fact_id(owner: str, fact_type: str, anchor: SourceAnchor, *identity: str | None) -> str:
    payload = [
        "human-understanding-v1", fact_type, owner,
        anchor.file_path, anchor.start_line, anchor.start_column,
        anchor.end_line, anchor.end_column, *identity,
    ]
    digest = sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"understanding_{digest}"


def has_valid_human_fact_id(fact: AuthorStatementFact | OperationFact) -> bool:
    """Recompute a derived identity at persistence trust boundaries."""
    if isinstance(fact, AuthorStatementFact):
        expected = _fact_id(fact.owner_symbol_id, "author_statement", fact.source_anchor)
    else:
        expected = _fact_id(
            fact.owner_symbol_id, "operation", fact.source_anchor,
            fact.kind.value, fact.target_symbol_id,
        )
    return fact.id == expected


def _docstring_node(node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                    ) -> tuple[ast.Expr, str] | None:
    body = node.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and type(body[0].value.value) is str):
        return body[0], body[0].value.value
    return None


def _author_text(raw: str) -> tuple[str, bool] | None:
    meaningful = [line.strip() for line in raw.splitlines() if line.strip()]
    if not meaningful:
        return None
    line = meaningful[0]
    sentence_end = next((
        index + 1 for index, char in enumerate(line)
        if char in "!?。！？"
        or char == "." and (index + 1 == len(line) or line[index + 1].isspace())
    ), len(line))
    selected = line[:sentence_end]
    truncated = sentence_end < len(line) or len(meaningful) > 1
    encoded = selected.encode("utf-8")
    if len(encoded) > MAX_AUTHOR_TEXT_BYTES:
        selected = encoded[:MAX_AUTHOR_TEXT_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    if not selected:
        return None
    return selected, truncated


class _OperationVisitor(ImplicitScopeVisitor):
    def __init__(self, parsed: ParsedFile, resolution: ResolutionResult,
                 flow: FlowAnalysis) -> None:
        assert parsed.tree is not None
        self.parsed = parsed
        self.index = resolution.symbol_index
        self.locations = ScopeLocations(parsed.tree, self.index, parsed.relative_path)
        self.scope_id = next(
            entry.id for entry in self.index.entries.values()
            if entry.file_path == parsed.relative_path and entry.kind == NodeKind.MODULE
        )
        self.operations: list[OperationFact] = []
        self.references: dict[
            tuple[str, int, int | None, str, str | None], list[SymbolReference]
        ] = {}
        for reference in self.index.references:
            if reference.source_anchor.file_path != parsed.relative_path:
                continue
            reference_key = (
                reference.scope_symbol_id or "", reference.source_anchor.start_line,
                reference.source_anchor.start_column, reference.name, reference.qualifier,
            )
            self.references.setdefault(reference_key, []).append(reference)
        self.callsites: dict[
            tuple[str, int, int | None, int | None, int | None, str], list[str]
        ] = {}
        for callsite in flow.graph.nodes.values():
            if (callsite.kind != NodeKind.CALL_SITE or callsite.file_path != parsed.relative_path
                    or len(callsite.source_anchors) != 1):
                continue
            anchor = callsite.source_anchors[0]
            callsite_key = (
                str(callsite.metadata.get("scope_symbol_id", "")), anchor.start_line,
                anchor.start_column, anchor.end_line, anchor.end_column, callsite.name,
            )
            self.callsites.setdefault(callsite_key, []).append(callsite.id)
        self.graph = flow.graph

    def _visit_definition(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        for expression in definition_expressions(node):
            self.visit(expression)
        owner = self.locations.owners.get(node)
        if owner is None:
            return
        previous = self.scope_id
        self.scope_id = owner
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope_id = previous

    visit_FunctionDef = _visit_definition
    visit_AsyncFunctionDef = _visit_definition
    visit_ClassDef = _visit_definition

    def visit_Return(self, node: ast.Return) -> None:
        kind = (HumanOperationKind.RETURN_EMPTY if node.value is None
                else HumanOperationKind.RETURN_VALUE)
        anchor = _anchor(self.parsed, node)
        self.operations.append(OperationFact(
            _fact_id(self.scope_id, "operation", anchor, kind.value, None),
            self.scope_id,
            kind,
            anchor,
            confidence=Confidence.EXACT,
        ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        expression = node.func
        name = expression.id if isinstance(expression, ast.Name) else (
            expression.attr if isinstance(expression, ast.Attribute) else ast.unparse(expression)
        )
        qualifier = ast.unparse(expression.value) if isinstance(expression, ast.Attribute) else None
        reference_key = (self.scope_id, expression.lineno,
                         getattr(expression, "col_offset", None), name, qualifier)
        references = self.references.get(reference_key, ())
        if len(references) == 1:
            reference = references[0]
            target_ids = ((reference.resolved_definition_id,)
                          if reference.resolved_definition_id is not None
                          else reference.possible_target_ids)
            if len(target_ids) == 1:
                target = self.graph.nodes.get(target_ids[0])
                operation_kind = (external_operation_kind(target.qualified_name)
                                  if target is not None
                                  and target.origin is NodeOrigin.EXTERNAL else None)
                anchor = _anchor(self.parsed, node)
                callsite_key = (self.scope_id, anchor.start_line, anchor.start_column,
                                anchor.end_line, anchor.end_column, name)
                callsites = self.callsites.get(callsite_key, ())
                if target is not None and operation_kind is not None and len(callsites) == 1:
                    self.operations.append(OperationFact(
                        _fact_id(self.scope_id, "operation", anchor,
                                 operation_kind.value, target.id),
                        self.scope_id,
                        operation_kind,
                        anchor,
                        callsites[0],
                        target.id,
                        reference.confidence,
                    ))
        self.generic_visit(node)


def build_human_understanding_facts(
    files: tuple[ParsedFile, ...],
    resolution: ResolutionResult,
    flow: FlowAnalysis,
) -> HumanUnderstandingFacts:
    """Extract bounded author claims while source ASTs are available."""
    statements: list[AuthorStatementFact] = []
    operations: list[OperationFact] = []
    index = resolution.symbol_index
    for parsed in files:
        if parsed.tree is None:
            continue
        owners = {
            (entry.source_anchor.start_line, entry.source_anchor.start_column, entry.name): entry.id
            for entry in index.entries.values()
            if entry.file_path == parsed.relative_path
            and entry.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)
        }
        module_owner = next((entry.id for entry in index.entries.values()
                             if entry.file_path == parsed.relative_path
                             and entry.kind == NodeKind.MODULE), None)
        if module_owner is None:
            # Parsing may succeed while indexing this file is isolated as an
            # INDEX_ERROR. No lexical owner is then authoritative for facts.
            continue
        for node in ast.walk(parsed.tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = module_owner if isinstance(node, ast.Module) else owners.get(
                (node.lineno, node.col_offset, node.name)
            )
            document_info = _docstring_node(node)
            if owner is None or document_info is None:
                continue
            document, raw_document = document_info
            extracted = _author_text(raw_document)
            if extracted is None:
                continue
            text, truncated = extracted
            anchor = _anchor(parsed, document)
            statements.append(AuthorStatementFact(
                _fact_id(owner, "author_statement", anchor), owner, text, anchor, truncated,
            ))
        visitor = _OperationVisitor(parsed, resolution, flow)
        visitor.visit(parsed.tree)
        operations.extend(visitor.operations)
    candidates: list[AuthorStatementFact | OperationFact] = [*statements, *operations]
    candidates.sort(key=lambda fact: (
        fact.source_anchor.file_path, fact.source_anchor.start_line,
        fact.source_anchor.start_column if fact.source_anchor.start_column is not None else -1,
        0 if isinstance(fact, AuthorStatementFact) else 1,
        fact.id,
    ))
    kept = candidates[:MAX_FACTS]
    return HumanUnderstandingFacts(
        tuple(fact for fact in kept if isinstance(fact, AuthorStatementFact)),
        tuple(fact for fact in kept if isinstance(fact, OperationFact)),
        len(candidates) - len(kept),
    )
