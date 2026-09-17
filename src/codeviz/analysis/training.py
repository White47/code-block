"""Static training events and independent entry points over parsed Python facts."""

import ast
from dataclasses import dataclass

from codeviz.core import (
    Confidence,
    EntityRole,
    EntryPoint,
    EntryPointKind,
    NodeKind,
    SourceAnchor,
    stable_node_id,
)
from codeviz.semantic import (
    PyTorchModelRule,
    PyTorchTrainingRule,
    ReceiverFact,
    TrainingRole,
)

from .flow import FlowAnalysis
from .imports import ModuleCatalog
from .model import ModelAnalysis
from .python import AnalysisIssue, ParsedFile
from .resolution import ResolutionResult
from .symbols import SymbolIndexEntry

_OPTIMIZERS = frozenset({"SGD", "Adam", "AdamW", "RMSprop", "Adagrad", "Adadelta", "Adamax"})
_SCHEDULERS = frozenset({"StepLR", "MultiStepLR", "ExponentialLR", "CosineAnnealingLR",
                         "ReduceLROnPlateau", "OneCycleLR", "LambdaLR"})
_LOSSES = frozenset({"CrossEntropyLoss", "MSELoss", "L1Loss", "NLLLoss", "BCELoss",
                     "BCEWithLogitsLoss", "SmoothL1Loss", "KLDivLoss", "HuberLoss"})
_FUNCTIONAL_LOSSES = frozenset({"cross_entropy", "mse_loss", "l1_loss", "nll_loss",
                                "binary_cross_entropy", "binary_cross_entropy_with_logits"})


@dataclass(frozen=True, slots=True)
class TrainingEvent:
    id: str
    role: TrainingRole
    source_anchor: SourceAnchor
    confidence: Confidence
    evidence: tuple[str, ...]
    entry_point_id: str
    source_node_id: str | None
    code_name: str
    parent_loop_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrainingAnalysis:
    entry_points: tuple[EntryPoint, ...] = ()
    events: tuple[TrainingEvent, ...] = ()
    issues: tuple[AnalysisIssue, ...] = ()


def _anchor(parsed: ParsedFile, node: ast.AST) -> SourceAnchor:
    line = getattr(node, "lineno", 1)
    return SourceAnchor(parsed.relative_path, line,
                        start_column=getattr(node, "col_offset", None),
                        end_line=getattr(node, "end_lineno", line),
                        end_column=getattr(node, "end_col_offset", None))


def _entry_kind(name: str) -> EntryPointKind | None:
    lower = name.casefold()
    if lower in ("train", "fit") or lower.startswith(("train_", "fit_")):
        return EntryPointKind.TRAIN
    if lower in ("evaluate", "eval", "validate", "validation", "test") or lower.startswith(
        ("evaluate_", "eval_", "validate_", "validation_")
    ):
        return EntryPointKind.EVALUATE
    if lower in ("infer", "inference", "predict") or lower.startswith(
        ("infer_", "inference_", "predict_")
    ):
        return EntryPointKind.INFERENCE
    if lower in ("export", "export_model") or lower.startswith("export_"):
        return EntryPointKind.EXPORT
    return None


def _parameter_role(name: str) -> str | None:
    lower = name.casefold()
    if lower in ("model", "net", "network"):
        return "model"
    if lower in ("optimizer", "optim", "opt"):
        return "optimizer"
    if lower in ("scheduler", "lr_scheduler"):
        return "scheduler"
    if lower in ("criterion", "loss_fn", "loss_func"):
        return "criterion"
    if lower == "loss":
        return "loss"
    return None


def _local_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in (*function.args.posonlyargs, *function.args.args,
             *function.args.kwonlyargs)}
    if function.args.vararg is not None:
        names.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        names.add(function.args.kwarg.arg)
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


class _EntryVisitor(ast.NodeVisitor):
    def __init__(self, parsed: ParsedFile, function: ast.FunctionDef | ast.AsyncFunctionDef,
                 entry: SymbolIndexEntry, kind: EntryPointKind, imports: dict[str, str],
                 resolution: ResolutionResult, model: ModelAnalysis, flow: FlowAnalysis) -> None:
        self.parsed = parsed
        self.function = function
        self.entry = entry
        self.kind = kind
        shadowed = _local_names(function)
        self.imports = {name: path for name, path in imports.items() if name not in shadowed}
        self.rule = PyTorchTrainingRule()
        self.framework = PyTorchModelRule()
        self.model_ids = {item.symbol_id for item in model.definitions}
        self.used_model_ids: list[str] = []
        self.references = {
            (reference.source_anchor.file_path, reference.source_anchor.start_line,
             reference.source_anchor.start_column): reference.resolved_definition_id
            for reference in resolution.symbol_index.references
            if reference.resolved_definition_id is not None
        }
        self.call_sites = {
            (node.file_path, node.source_anchors[0].start_line,
             node.source_anchors[0].start_column, node.metadata.get("scope_symbol_id")): node.id
            for node in flow.graph.nodes.values() if node.kind == NodeKind.CALL_SITE
        }
        self.control_blocks = {
            (node.file_path, node.source_anchors[0].start_line,
             node.source_anchors[0].start_column): node.id
            for node in flow.graph.nodes.values()
            if node.kind == NodeKind.CONTROL_BLOCK and node.scope == entry.qualified_name
        }
        self.events: list[TrainingEvent] = []
        self.loop_stack: list[str] = []
        self.facts: dict[str, ReceiverFact] = {}
        for argument in (*function.args.posonlyargs, *function.args.args,
                         *function.args.kwonlyargs):
            role = _parameter_role(argument.arg)
            if role is not None:
                self.facts[argument.arg] = ReceiverFact(
                    role, Confidence.PROBABLE, (f"parameter name {argument.arg!r} suggests {role}",)
                )
        self.no_grad_context_calls = {
            id(item.context_expr) for statement in ast.walk(function)
            if isinstance(statement, (ast.With, ast.AsyncWith)) for item in statement.items
            if isinstance(item.context_expr, ast.Call)
        }

    def _event(self, role: TrainingRole, node: ast.AST, confidence: Confidence,
               evidence: tuple[str, ...], source_node_id: str | None, code_name: str) -> None:
        ordinal = len(self.events)
        event_id = stable_node_id(
            self.parsed.relative_path, f"{self.entry.qualified_name}.{role.value}",
            NodeKind.FLOW_STEP, self.entry.qualified_name, EntityRole.INVOCATION,
            occurrence_key=f"source:{source_node_id or 'ast'}:event:{ordinal}",
        )
        self.events.append(TrainingEvent(event_id, role, _anchor(self.parsed, node),
                                         confidence, evidence, self.entry.id,
                                         source_node_id, code_name,
                                         self.loop_stack[-1] if self.loop_stack else None))

    def _fact_for_value(self, value: ast.expr) -> ReceiverFact | None:
        if isinstance(value, ast.Name):
            return self.facts.get(value.id)
        if not isinstance(value, ast.Call):
            return None
        target_id = self.references.get((self.parsed.relative_path, value.func.lineno,
                                         value.func.col_offset))
        if target_id in self.model_ids:
            if target_id not in self.used_model_ids:
                self.used_model_ids.append(target_id)
            return ReceiverFact("model", Confidence.EXACT,
                                (f"constructor resolves to model definition {target_id}",))
        canonical = self.framework.canonical_name(value.func, self.imports)
        if canonical is not None:
            if canonical.startswith("torch.optim.lr_scheduler.") and canonical.rsplit(".", 1)[-1] in _SCHEDULERS:
                return ReceiverFact("scheduler", Confidence.EXACT, (f"constructor {canonical}",))
            if canonical.startswith("torch.optim.") and canonical.rsplit(".", 1)[-1] in _OPTIMIZERS:
                return ReceiverFact("optimizer", Confidence.EXACT, (f"constructor {canonical}",))
            if canonical.startswith("torch.nn.") and canonical.rsplit(".", 1)[-1] in _LOSSES:
                return ReceiverFact("criterion", Confidence.EXACT, (f"constructor {canonical}",))
            if (canonical.startswith("torch.nn.functional.")
                    and canonical.rsplit(".", 1)[-1] in _FUNCTIONAL_LOSSES):
                return ReceiverFact("loss", Confidence.EXACT, (f"functional loss {canonical}",))
        if isinstance(value.func, ast.Name):
            receiver = self.facts.get(value.func.id)
            if receiver is not None and receiver.role == "criterion":
                return ReceiverFact("loss", receiver.confidence,
                                    (*receiver.evidence, f"result of {value.func.id}(...)"))
        return None

    def _bind(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            fact = self._fact_for_value(value)
            if fact is None:
                role = _parameter_role(target.id)
                fact = (ReceiverFact(role, Confidence.PROBABLE,
                                     (f"assignment name {target.id!r} suggests {role}",))
                        if role is not None else None)
            if fact is None:
                self.facts.pop(target.id, None)
            else:
                self.facts[target.id] = fact
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                if isinstance(item, ast.Name):
                    self.facts.pop(item.id, None)

    def _clear_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.facts.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._clear_target(item)

    @staticmethod
    def _merge_facts(before: dict[str, ReceiverFact],
                     branches: tuple[dict[str, ReceiverFact], ...]) -> dict[str, ReceiverFact]:
        merged = dict(before)
        names = set(before).union(*(set(branch) for branch in branches))
        for name in names:
            if all(branch.get(name) == before.get(name) for branch in branches):
                continue
            candidates = [fact for fact in (before.get(name),
                          *(branch.get(name) for branch in branches)) if fact is not None]
            if candidates and len({fact.role for fact in candidates}) == 1:
                merged[name] = ReceiverFact(
                    candidates[0].role, Confidence.PROBABLE,
                    (*candidates[0].evidence, "receiver may be rebound across control flow"),
                )
            else:
                merged.pop(name, None)
        return merged

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind(node.target, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._clear_target(node.target)

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        matched = self.rule.classify_call(node, self.imports, self.facts)
        if matched is None:
            return
        if matched.role == TrainingRole.DISABLE_GRADIENT and id(node) not in self.no_grad_context_calls:
            return
        source_node_id = self.call_sites.get((self.parsed.relative_path, node.lineno,
                                              node.col_offset, self.entry.id))
        self._event(matched.role, node, matched.confidence, matched.evidence,
                    source_node_id, ast.unparse(node.func))

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        target = ast.unparse(node.target).casefold()
        iterator = ast.unparse(node.iter).casefold()
        role: TrainingRole | None = None
        if "epoch" in target:
            role = TrainingRole.EPOCH_LOOP
        elif ("batch" in target or "loader" in iterator or "dataloader" in iterator):
            role = TrainingRole.BATCH_LOOP
        if role is not None:
            source_node_id = self.control_blocks.get(
                (self.parsed.relative_path, node.lineno, node.col_offset)
            )
            self._event(role, node, Confidence.PROBABLE,
                        (f"loop syntax {ast.unparse(node.target)} in {ast.unparse(node.iter)}",),
                        source_node_id, ast.unparse(node.target))
            self.loop_stack.append(self.events[-1].id)
        self.visit(node.iter)
        before = dict(self.facts)
        self._clear_target(node.target)
        for statement in node.body:
            self.visit(statement)
        after_body = dict(self.facts)
        if role is not None:
            self.loop_stack.pop()
        self.facts = self._merge_facts(before, (after_body, before))
        before_else = dict(self.facts)
        for statement in node.orelse:
            self.visit(statement)
        self.facts = self._merge_facts(before_else, (dict(self.facts), before_else))

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = dict(self.facts)
        for statement in node.body:
            self.visit(statement)
        after_body = dict(self.facts)
        self.facts = dict(before)
        for statement in node.orelse:
            self.visit(statement)
        self.facts = self._merge_facts(before, (after_body, dict(self.facts)))

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        before = dict(self.facts)
        for statement in node.body:
            self.visit(statement)
        self.facts = self._merge_facts(before, (dict(self.facts), before))
        before_else = dict(self.facts)
        for statement in node.orelse:
            self.visit(statement)
        self.facts = self._merge_facts(before_else, (dict(self.facts), before_else))

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        before = dict(self.facts)
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)
        branches = [dict(self.facts)]
        for handler in node.handlers:
            self.facts = dict(before)
            if handler.name:
                self.facts.pop(handler.name, None)
            for statement in handler.body:
                self.visit(statement)
            branches.append(dict(self.facts))
        self.facts = self._merge_facts(before, tuple(branches))
        for statement in node.finalbody:
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._clear_target(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._clear_target(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return  # Nested definitions are not executed by defining the entry point.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def run(self) -> tuple[EntryPoint, tuple[TrainingEvent, ...]]:
        for statement in self.function.body:
            self.visit(statement)
        entry = EntryPoint(self.entry.id, self.entry.name, self.kind,
                           self.entry.source_anchor,
                           tuple(event.id for event in self.events),
                           tuple(self.used_model_ids))
        return entry, tuple(self.events)


def build_training_analysis(files: tuple[ParsedFile, ...], resolution: ResolutionResult,
                            model: ModelAnalysis, flow: FlowAnalysis) -> TrainingAnalysis:
    """Analyze each named training/evaluation/inference/export entry independently."""
    entries: list[EntryPoint] = []
    events: list[TrainingEvent] = []
    issues: list[AnalysisIssue] = []
    symbols = {(item.file_path, item.source_anchor.start_line, item.name): item
               for item in resolution.symbol_index.entries.values()
               if item.kind in (NodeKind.FUNCTION, NodeKind.METHOD)}
    catalog = ModuleCatalog(files, resolution.symbol_index)
    framework = PyTorchModelRule()
    for parsed in files:
        if parsed.tree is None:
            continue
        try:
            imports = framework.import_bindings(parsed.tree)
        except Exception as error:
            issues.append(AnalysisIssue(
                "TRAINING_ERROR", f"{type(error).__name__}: {error}",
                parsed.relative_path, SourceAnchor(parsed.relative_path, 1),
            ))
            continue
        if catalog.is_project_path("torch"):
            imports = {name: path for name, path in imports.items()
                       if not path.startswith("torch")}
        for function in ast.walk(parsed.tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            kind = _entry_kind(function.name)
            symbol = symbols.get((parsed.relative_path, function.lineno, function.name))
            if kind is None or symbol is None:
                continue
            try:
                visitor = _EntryVisitor(parsed, function, symbol, kind,
                                        imports, resolution, model, flow)
                entry, flow_events = visitor.run()
                entries.append(entry)
                events.extend(flow_events)
            except Exception as error:
                issues.append(AnalysisIssue(
                    "TRAINING_ERROR", f"{type(error).__name__}: {error}",
                    parsed.relative_path, SourceAnchor(parsed.relative_path, function.lineno),
                ))
    return TrainingAnalysis(tuple(entries), tuple(events), tuple(issues))
