"""Verify structural occurrence paths without enumerating their expansion tree."""

from dataclasses import dataclass
from hashlib import sha256

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import NodeKind, SemanticComponent

from .query import GraphQuery

STRUCTURE_KINDS = frozenset({NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD})


def analysis_namespace(analysis: ProjectAnalysis) -> str:
    return sha256("\n".join(sorted(analysis.symbol_index.entries)).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class StructureIdentity:
    component: SemanticComponent
    path: tuple[str, ...]
    projected: bool


class StructureResolver:
    def __init__(self, analysis: ProjectAnalysis, namespace: str = "") -> None:
        self.analysis = analysis
        self.namespace = namespace or analysis_namespace(analysis)
        self.query = GraphQuery(analysis.semantic.graph) if analysis.semantic else None

    @property
    def project_id(self) -> str:
        return f"project:{self.namespace}"

    def roots(self) -> tuple[str, ...]:
        if self.query is None:
            return ()
        return tuple(root for root in self.query.roots
                     if self.query.components[root].role == "ModelDefinition")

    def children(self, component_id: str) -> tuple[str, ...]:
        if self.query is None:
            return ()
        direct = self.query.children(component_id)
        component = self.query.components.get(component_id)
        target_id = component.metadata.get("target_definition_id") if component else None
        target = self.query.components.get(target_id) if isinstance(target_id, str) else None
        referred = (self.query.children(target.id) if target is not None
                    and target.role == "ModelDefinition" else ())
        return tuple(dict.fromkeys((*direct, *referred)))

    def occurrence_id(self, path: tuple[str, ...], projected: bool) -> str:
        return (f"projection:{self.namespace}:" + "/".join(path)) if projected else path[-1]

    def definition_id(self, component: SemanticComponent) -> str | None:
        if component.role == "ModelDefinition":
            return component.id
        target = component.metadata.get("target_definition_id")
        return target if isinstance(target, str) and target in self.roots() else None

    def resolve(self, node_id: str, root_id: str | None = None) -> StructureIdentity:
        if self.query is None:
            raise ValueError("semantic analysis is required")
        roots = self.roots()
        if root_id is not None and root_id not in roots:
            raise ValueError("requested root is not available in this view")
        allowed = (root_id,) if root_id is not None else roots
        prefix = f"projection:{self.namespace}:"
        if node_id.startswith(prefix):
            path = tuple(node_id[len(prefix):].split("/"))
            if not 2 <= len(path) <= 6 or path[0] not in allowed:
                raise ValueError("invalid structural occurrence path")
            projected = False
            for parent, child in zip(path, path[1:], strict=False):
                if child not in self.children(parent):
                    raise ValueError("invalid structural occurrence transition")
                projected = projected or child not in self.query.children(parent)
            if not projected or node_id != self.occurrence_id(path, True):
                raise ValueError("invalid structural occurrence identity")
            return StructureIdentity(self.query.components[path[-1]], path, True)
        if node_id not in self.query.components:
            raise ValueError("unknown structural component")
        path = self.query.path_to_root(node_id, frozenset(allowed))
        if not path:
            raise ValueError("selected component is outside the requested view")
        if len(path) > 6:
            raise ValueError("invalid structural occurrence path")
        return StructureIdentity(self.query.components[node_id], path, False)

    def project_entries(self) -> dict[str, str]:
        entries = {item.id: item for item in self.analysis.symbol_index.entries.values()
                   if item.definition and item.kind in STRUCTURE_KINDS}
        return {node_id: (item.parent_symbol if item.parent_symbol in entries else self.project_id)
                for node_id, item in entries.items()}

    def project_valid(self, node_id: str, root_id: str | None = None) -> bool:
        parents = self.project_entries()
        root = root_id or self.project_id
        if root != self.project_id and root not in parents:
            return False
        current = node_id
        seen: set[str] = set()
        while current not in seen:
            if current == root:
                return True
            seen.add(current)
            if current not in parents:
                return False
            current = parents[current]
        return False
