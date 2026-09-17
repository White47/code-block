"""Conservative torch.nn syntax patterns; never imports the analyzed framework."""

import ast

from .rules import ConstructorMatch

_LAYERS = frozenset({
    "Linear", "Embedding", "Conv1d", "Conv2d", "Conv3d",
    "LayerNorm", "BatchNorm1d", "BatchNorm2d", "BatchNorm3d",
    "Dropout", "Dropout1d", "Dropout2d", "Dropout3d",
    "ReLU", "GELU", "Sigmoid", "Tanh", "Softmax",
    "MultiheadAttention", "TransformerEncoder", "TransformerEncoderLayer",
    "TransformerDecoder", "TransformerDecoderLayer",
})
_CONTAINERS = frozenset({"Sequential", "ModuleList", "ModuleDict"})


def _target_names(target: ast.AST) -> set[str]:
    return {part.id for part in ast.walk(target)
            if isinstance(part, ast.Name) and isinstance(part.ctx, (ast.Store, ast.Del))}


def _bound_names(statement: ast.stmt) -> set[str]:
    """Conservatively identify module-scope writes, including control blocks."""
    if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return {statement.name}
    if isinstance(statement, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in statement.names}
    if isinstance(statement, ast.ImportFrom):
        return {alias.asname or alias.name for alias in statement.names}
    if isinstance(statement, ast.Assign):
        return set().union(*(_target_names(target) for target in statement.targets))
    if isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        return _target_names(statement.target)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        names = _target_names(statement.target)
        return names | set().union(*(_bound_names(item) for item in (*statement.body, *statement.orelse)))
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        names = set().union(*(_target_names(item.optional_vars) for item in statement.items
                              if item.optional_vars is not None))
        return names | set().union(*(_bound_names(item) for item in statement.body))
    if isinstance(statement, (ast.If, ast.While)):
        return set().union(*(_bound_names(item) for item in (*statement.body, *statement.orelse)))
    if isinstance(statement, (ast.Try, ast.TryStar)):
        names = set().union(*(_bound_names(item) for item in
                              (*statement.body, *statement.orelse, *statement.finalbody)))
        for handler in statement.handlers:
            if handler.name:
                names.add(handler.name)
            names.update(set().union(*(_bound_names(item) for item in handler.body)))
        return names
    if isinstance(statement, ast.Match):
        match_names: set[str] = set()
        for case in statement.cases:
            match_names.update(part.name for part in ast.walk(case.pattern)
                               if isinstance(part, (ast.MatchAs, ast.MatchStar))
                               and part.name is not None)
            match_names.update(set().union(*(_bound_names(item) for item in case.body)))
        return match_names
    return {part.target.id for part in ast.walk(statement)
            if isinstance(part, ast.NamedExpr) and isinstance(part.target, ast.Name)}


class PyTorchModelRule:
    """Recognize only names traceable to explicit torch imports."""

    def import_bindings(self, tree: ast.Module) -> dict[str, str]:
        bindings: dict[str, str] = {}
        shadowed: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if alias.name == "torch" or alias.name.startswith("torch.nn"):
                        bindings[bound] = alias.name if alias.asname else bound
            elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
                prefix = statement.module or ""
                if prefix == "torch" or prefix.startswith("torch.nn"):
                    for alias in statement.names:
                        if alias.name != "*":
                            bindings[alias.asname or alias.name] = f"{prefix}.{alias.name}"
            else:
                shadowed.update(_bound_names(statement))
        return {name: path for name, path in bindings.items() if name not in shadowed}

    def canonical_name(self, expression: ast.expr, bindings: dict[str, str]) -> str | None:
        if isinstance(expression, ast.Name):
            return bindings.get(expression.id)
        if isinstance(expression, ast.Attribute):
            base = self.canonical_name(expression.value, bindings)
            return f"{base}.{expression.attr}" if base is not None else None
        return None

    def is_module_base(self, expression: ast.expr, bindings: dict[str, str]) -> bool:
        return self.canonical_name(expression, bindings) == "torch.nn.Module"

    def match_constructor(self, expression: ast.expr,
                          bindings: dict[str, str]) -> ConstructorMatch | None:
        canonical = self.canonical_name(expression, bindings)
        if canonical is None or not canonical.startswith("torch.nn."):
            return None
        name = canonical.removeprefix("torch.nn.")
        if name in _LAYERS:
            return ConstructorMatch(canonical)
        if name in _CONTAINERS:
            return ConstructorMatch(canonical, name)
        return None
