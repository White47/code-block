"""Conservative AST-time facts for explicit model parameter sharing."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Iterator

from codeviz.core import Confidence, NodeKind, SourceAnchor

from .python import AnalysisIssue, ParsedFile
from .resolution import ResolutionResult

if TYPE_CHECKING:
    from .model import ModelAnalysis, ModuleInstance

MAX_PARAMETER_SHARING_FACTS = 512
MAX_PARAMETER_ATTRIBUTE_BYTES = 64
_ID_PREFIX = "parameter_sharing_"
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_CONFIDENCE_STRENGTH = {
    Confidence.UNRESOLVED: 0,
    Confidence.PROBABLE: 1,
    Confidence.EXACT: 2,
}


@dataclass(frozen=True, slots=True)
class ParameterSharingFact:
    id: str
    model_definition_id: str
    owner_symbol_id: str
    assigned_instance_id: str
    assigned_attribute: str
    source_instance_id: str
    source_attribute: str
    source_anchor: SourceAnchor
    confidence: Confidence

    def __post_init__(self) -> None:
        identifiers = (
            self.model_definition_id, self.owner_symbol_id,
            self.assigned_instance_id, self.source_instance_id,
        )
        attributes = (self.assigned_attribute, self.source_attribute)
        if (not _valid_fact_id(self.id)
                or any(type(value) is not str or not value for value in identifiers)
                or any(type(value) is not str or not _IDENTIFIER.fullmatch(value)
                       or len(value.encode("utf-8")) > MAX_PARAMETER_ATTRIBUTE_BYTES
                       for value in attributes)
                or self.assigned_instance_id == self.source_instance_id
                or not isinstance(self.source_anchor, SourceAnchor)
                or self.confidence not in (Confidence.EXACT, Confidence.PROBABLE)):
            raise ValueError("invalid parameter-sharing fact")


def _valid_fact_id(value: object) -> bool:
    return bool(
        type(value) is str
        and value.startswith(_ID_PREFIX)
        and len(value) == len(_ID_PREFIX) + 64
        and all(char in "0123456789abcdef" for char in value[len(_ID_PREFIX):])
    )


def parameter_sharing_fact_id(
    model_definition_id: str,
    owner_symbol_id: str,
    assigned_instance_id: str,
    assigned_attribute: str,
    source_instance_id: str,
    source_attribute: str,
    source_anchor: SourceAnchor,
) -> str:
    payload = (
        "h3-parameter-sharing-v1", model_definition_id, owner_symbol_id,
        assigned_instance_id, assigned_attribute, source_instance_id, source_attribute,
        source_anchor.file_path, source_anchor.start_line, source_anchor.start_column,
        source_anchor.end_line, source_anchor.end_column,
    )
    digest = sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"{_ID_PREFIX}{digest}"


def has_valid_parameter_sharing_id(fact: ParameterSharingFact) -> bool:
    return fact.id == parameter_sharing_fact_id(
        fact.model_definition_id, fact.owner_symbol_id,
        fact.assigned_instance_id, fact.assigned_attribute,
        fact.source_instance_id, fact.source_attribute, fact.source_anchor,
    )


def _anchor(parsed: ParsedFile, node: ast.AST) -> SourceAnchor:
    return SourceAnchor(
        parsed.relative_path, getattr(node, "lineno", 1),
        start_column=getattr(node, "col_offset", None),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        end_column=getattr(node, "end_col_offset", None),
    )


def _self_attribute_chain(expression: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    current: ast.expr = expression
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id != "self":
        return None
    parts.reverse()
    return tuple(parts)


def _instance_path(name: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    for base, key in re.findall(r"([A-Za-z_]\w*)|\[([A-Za-z_]\w*)\]", name):
        parts.append(base or key)
    if not parts:
        return None
    rebuilt = parts[0] + "".join(f"[{part}]" for part in parts[1:])
    return tuple(parts) if rebuilt == name else None


def _constructor_calls(parsed: ParsedFile) -> dict[tuple[int, int | None], ast.Call]:
    assert parsed.tree is not None
    return {
        (node.lineno, node.col_offset): node
        for node in ast.walk(parsed.tree) if isinstance(node, ast.Call)
    }


def _keyword_bool(call: ast.Call | None, name: str, default: bool) -> bool | None:
    if call is None:
        return None
    keyword = next((item for item in call.keywords if item.arg == name), None)
    if keyword is None:
        return default
    if isinstance(keyword.value, ast.Constant) and type(keyword.value.value) is bool:
        return keyword.value.value
    return None


def allowed_parameter_attributes(
    instance: ModuleInstance,
    constructor: ast.Call | None,
) -> frozenset[str]:
    layer = instance.layer_type
    if layer == "torch.nn.Embedding":
        return frozenset({"weight"})
    if layer == "torch.nn.Linear":
        bias = _keyword_bool(constructor, "bias", True)
        return frozenset(("weight", "bias") if bias is True else ("weight",))
    if layer == "torch.nn.LayerNorm":
        affine = _keyword_bool(constructor, "elementwise_affine", True)
        if affine is not True:
            return frozenset()
        bias = _keyword_bool(constructor, "bias", True)
        return frozenset(("weight", "bias") if bias is True else ("weight",))
    if layer.rsplit(".", 1)[-1] == "LayerNorm":
        return frozenset({"weight"})
    return frozenset()


def _statements(
    statements: list[ast.stmt],
    guarded: bool = False,
) -> Iterator[tuple[ast.stmt, bool]]:
    for statement in statements:
        yield statement, guarded
        if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                  ast.Try, ast.TryStar, ast.With, ast.AsyncWith)):
            blocks = [getattr(statement, "body", []), getattr(statement, "orelse", []),
                      getattr(statement, "finalbody", [])]
            if isinstance(statement, (ast.Try, ast.TryStar)):
                blocks.extend(handler.body for handler in statement.handlers)
            for block in blocks:
                yield from _statements(block, True)


def _assignment_parts(statement: ast.stmt) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        return statement.targets[0], statement.value
    if (isinstance(statement, ast.AnnAssign) and statement.value is not None
            and isinstance(statement.target, ast.expr)):
        return statement.target, statement.value
    return None


def _issue(parsed: ParsedFile, node: ast.AST, message: str) -> AnalysisIssue:
    return AnalysisIssue(
        "MODEL_PARAMETER_SHARING_UNRESOLVED", message,
        parsed.relative_path, _anchor(parsed, node),
    )


def build_model_parameter_sharing_facts(
    files: tuple[ParsedFile, ...],
    resolution: ResolutionResult,
    model: ModelAnalysis,
) -> tuple[tuple[ParameterSharingFact, ...], tuple[AnalysisIssue, ...]]:
    """Recognize unique, unconditional constructor parameter aliases."""
    definitions = {item.symbol_id: item for item in model.definitions}
    instances_by_owner: dict[str, dict[tuple[str, ...], list[ModuleInstance]]] = {}
    for instance in model.instances:
        path = _instance_path(instance.name)
        if path is not None:
            instances_by_owner.setdefault(instance.owner_definition_id, {}).setdefault(
                path, [],
            ).append(instance)
    parsed_by_path = {parsed.relative_path: parsed for parsed in files if parsed.tree is not None}
    facts: list[ParameterSharingFact] = []
    issues: list[AnalysisIssue] = []
    for definition_id, path_map in instances_by_owner.items():
        definition = definitions.get(definition_id)
        if definition is None:
            continue
        parsed = parsed_by_path.get(definition.source_anchor.file_path)
        if parsed is None or parsed.tree is None:
            continue
        class_node = next((node for node in ast.walk(parsed.tree)
                           if isinstance(node, ast.ClassDef)
                           and node.lineno == definition.source_anchor.start_line
                           and node.col_offset == definition.source_anchor.start_column), None)
        if class_node is None:
            continue
        constructors = [node for node in class_node.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "__init__"]
        if len(constructors) != 1:
            continue
        constructor = constructors[0]
        owner = next((entry for entry in resolution.symbol_index.entries.values()
                      if entry.parent_symbol == definition_id
                      and entry.kind is NodeKind.METHOD
                      and entry.name == "__init__"
                      and entry.source_anchor.start_line == constructor.lineno), None)
        if owner is None:
            continue
        calls = _constructor_calls(parsed)
        writes: Counter[tuple[str, ...]] = Counter()
        candidates: list[tuple[ast.stmt, bool, tuple[str, ...], tuple[str, ...]]] = []
        for statement, guarded in _statements(constructor.body):
            assignment = _assignment_parts(statement)
            if assignment is None:
                continue
            target, value = assignment
            target_chain = _self_attribute_chain(target)
            value_chain = _self_attribute_chain(value)
            if target_chain is not None:
                writes[target_chain] += 1
            if (target_chain is not None and len(target_chain) >= 2
                    and target_chain[-1] in ("weight", "bias")
                    and (value_chain is None or len(value_chain) < 2)):
                issues.append(_issue(
                    parsed, statement,
                    "parameter alias uses a dynamic or local intermediate value",
                ))
                continue
            if (target_chain is not None and value_chain is not None
                    and len(target_chain) >= 2 and len(value_chain) >= 2):
                candidates.append((statement, guarded, target_chain, value_chain))
        for call in (node for node in ast.walk(constructor) if isinstance(node, ast.Call)):
            if (isinstance(call.func, ast.Name) and call.func.id == "setattr"
                    and len(call.args) >= 2
                    and _self_attribute_chain(call.args[0]) is not None
                    and isinstance(call.args[1], ast.Constant)
                    and call.args[1].value in ("weight", "bias")):
                issues.append(_issue(
                    parsed, call, "dynamic parameter attribute assignment is unsupported",
                ))
        for statement, guarded, target_chain, value_chain in candidates:
            target_path, target_attr = target_chain[:-1], target_chain[-1]
            source_path, source_attr = value_chain[:-1], value_chain[-1]
            target_matches = path_map.get(target_path, [])
            source_matches = path_map.get(source_path, [])
            if target_attr not in ("weight", "bias") and source_attr not in ("weight", "bias"):
                continue
            if (guarded or writes[target_chain] != 1 or len(target_matches) != 1
                    or len(source_matches) != 1):
                issues.append(_issue(parsed, statement,
                                     "parameter alias is conditional, repeated, or ambiguous"))
                continue
            target_instance, source_instance = target_matches[0], source_matches[0]
            target_call = calls.get((target_instance.source_anchor.start_line,
                                     target_instance.source_anchor.start_column))
            source_call = calls.get((source_instance.source_anchor.start_line,
                                     source_instance.source_anchor.start_column))
            if (target_instance.id == source_instance.id
                    or target_attr != source_attr
                    or target_attr not in allowed_parameter_attributes(target_instance, target_call)
                    or source_attr not in allowed_parameter_attributes(source_instance, source_call)):
                issues.append(_issue(parsed, statement,
                                     "parameter endpoints or attributes are not statically proven"))
                continue
            anchor = _anchor(parsed, statement)
            confidence = min(
                (target_instance.confidence, source_instance.confidence),
                key=_CONFIDENCE_STRENGTH.__getitem__,
            )
            if confidence is Confidence.UNRESOLVED:
                issues.append(_issue(parsed, statement,
                                     "parameter endpoint confidence is unresolved"))
                continue
            facts.append(ParameterSharingFact(
                parameter_sharing_fact_id(
                    definition_id, owner.id, target_instance.id, target_attr,
                    source_instance.id, source_attr, anchor,
                ),
                definition_id, owner.id, target_instance.id, target_attr,
                source_instance.id, source_attr, anchor, confidence,
            ))
    facts.sort(key=lambda fact: (
        fact.source_anchor.file_path, fact.source_anchor.start_line,
        fact.source_anchor.start_column if fact.source_anchor.start_column is not None else -1,
        fact.id,
    ))
    omitted = max(0, len(facts) - MAX_PARAMETER_SHARING_FACTS)
    if omitted:
        anchor = facts[MAX_PARAMETER_SHARING_FACTS].source_anchor
        issues.append(AnalysisIssue(
            "MODEL_PARAMETER_SHARING_TRUNCATED",
            f"omitted {omitted} parameter-sharing facts", anchor.file_path, anchor,
        ))
    return tuple(facts[:MAX_PARAMETER_SHARING_FACTS]), tuple(issues)
