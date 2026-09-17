"""Small plugin contract for syntax-backed framework model recognition."""

import ast
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ConstructorMatch:
    canonical_name: str
    container_kind: str | None = None


class ModelRule(Protocol):
    def import_bindings(self, tree: ast.Module) -> dict[str, str]: ...

    def canonical_name(self, expression: ast.expr, bindings: dict[str, str]) -> str | None: ...

    def is_module_base(self, expression: ast.expr, bindings: dict[str, str]) -> bool: ...

    def match_constructor(self, expression: ast.expr,
                          bindings: dict[str, str]) -> ConstructorMatch | None: ...
