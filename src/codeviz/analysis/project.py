"""One-shot project analysis: scan, parse, and merge lexical symbol facts."""

from dataclasses import dataclass, field
from pathlib import Path

from codeviz.core import SourceAnchor
from codeviz.semantic.components import SemanticAnalysis, build_semantic_analysis

from .discovery import discover_python_files
from .entries import GenericEntryCandidate, detect_generic_entries
from .flow import FlowAnalysis, build_project_flow
from .human_tasks import HumanTaskAnalysis, build_human_task_analysis
from .human_understanding import (
    HumanUnderstandingFacts,
    build_human_understanding_facts,
)
from .model import ModelAnalysis, build_project_model
from .python import AnalysisIssue, ParsedFile, parse_python_file
from .resolution import ResolutionResult, resolve_project_symbols
from .symbols import SymbolIndex, index_parsed_file
from .training import TrainingAnalysis, build_training_analysis


@dataclass(frozen=True, slots=True)
class ProjectAnalysis:
    files: tuple[ParsedFile, ...]
    symbol_index: SymbolIndex
    issues: tuple[AnalysisIssue, ...]
    resolution: ResolutionResult | None = None
    flow: FlowAnalysis | None = None
    model: ModelAnalysis | None = None
    training: TrainingAnalysis | None = None
    semantic: SemanticAnalysis | None = None
    generic_entries: tuple[GenericEntryCandidate, ...] = ()
    human_understanding: HumanUnderstandingFacts = field(default_factory=HumanUnderstandingFacts)
    human_tasks: HumanTaskAnalysis = field(default_factory=HumanTaskAnalysis)


def analyze_project(project_root: Path, ignore_paths: tuple[str, ...] = ()) -> ProjectAnalysis:
    """Analyze each discovered Python file independently; preserve partial results."""
    files: list[ParsedFile] = []
    index = SymbolIndex()
    issues: list[AnalysisIssue] = []
    for relative_path in discover_python_files(project_root, ignore_paths):
        parsed = parse_python_file(project_root, relative_path)
        files.append(parsed)
        issues.extend(parsed.issues)
        if parsed.tree is not None:
            try:
                index.merge(index_parsed_file(parsed))
            except Exception as error:
                issues.append(AnalysisIssue(
                    "INDEX_ERROR", f"{type(error).__name__}: {error}", relative_path,
                    SourceAnchor(relative_path, 1),
                ))
    resolution = resolve_project_symbols(tuple(files), index)
    issues.extend(resolution.issues)
    flow = build_project_flow(tuple(files), resolution)
    issues.extend(flow.issues)
    model = build_project_model(tuple(files), resolution, flow)
    issues.extend(model.issues)
    training = build_training_analysis(tuple(files), resolution, model, flow)
    issues.extend(training.issues)
    semantic = build_semantic_analysis(tuple(files), model, training)
    issues.extend(semantic.issues)
    human_understanding = build_human_understanding_facts(tuple(files), resolution, flow)
    if human_understanding.omitted_fact_count:
        anchor = SourceAnchor(files[0].relative_path, 1) if files else None
        issues.append(AnalysisIssue(
            "UNDERSTANDING_FACTS_TRUNCATED",
            f"omitted {human_understanding.omitted_fact_count} human-understanding facts",
            files[0].relative_path if files else "",
            anchor,
        ))
    human_tasks = build_human_task_analysis(tuple(files), resolution, flow, model, training)
    if human_tasks.omitted_fact_count:
        anchor = SourceAnchor(files[0].relative_path, 1) if files else None
        issues.append(AnalysisIssue(
            "HUMAN_TASK_FACTS_TRUNCATED",
            f"omitted {human_tasks.omitted_fact_count} human-task facts",
            files[0].relative_path if files else "",
            anchor,
        ))
    return ProjectAnalysis(tuple(files), resolution.symbol_index, tuple(issues),
                           resolution, flow, model, training, semantic,
                           detect_generic_entries(tuple(files), resolution.symbol_index),
                           human_understanding, human_tasks)
