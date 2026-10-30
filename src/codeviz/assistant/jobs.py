"""Internal C4 admission and compact retirement records."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from codeviz.ui.assistant_context import AssistantContext, PublicationIdentity

from .bridge_requests import UnderstandingRef
from .budget import BudgetLedger, BudgetUsage, ExplanationBudgetConfig
from .config import ProviderConfig
from .contracts import JobStatus, RequestScope
from .events import EventRing, JobKind
from .issues import SafeIssue
from .project_retrieval import ProjectCoverage
from .provider import PreparedRequest, ProviderAdapter, ProviderDraft
from .snapshot import AnalysisSnapshot, AnchorRecord
from .tool_contracts import EntityRef, RelationKey

if TYPE_CHECKING:
    from asyncio import Task
    from concurrent.futures import Future

    from ._connection_values import ConnectionCapabilities
    from ._usage_values import InputAccountingSnapshot, ObservedUsage
    from ._validation_diagnostics import Capture
    from .connection import _ConnectionResources
    from .navigation import NavigationTarget
    from .poll_public import DisplayEvent, PollIdentity, PollState
    from .response import Answer
    from .retained_evidence import (
        RetainedEvidenceContext,
        RetainedEvidenceIdentity,
        RetainedEvidenceLease,
    )
    from .tools import ToolSession


@dataclass(frozen=True, slots=True)
class RootRetrieval:
    """Trusted saved-context selection; never accepted from model output."""

    anchor: AnchorRecord
    target: EntityRef | RelationKey | None

    def __post_init__(self) -> None:
        if (type(self.anchor) is not AnchorRecord
                or (self.target is not None and type(self.target) not in (EntityRef, RelationKey))):
            raise ValueError("Invalid internal root retrieval.")
        self.anchor.__post_init__()
        if self.target is not None:
            self.target.__post_init__()

    def canonical(self) -> object:
        self.__post_init__()
        anchor = self.anchor
        target = self.target
        chosen = (None if target is None else
                  ("entity", target.kind, target.id) if isinstance(target, EntityRef) else
                  ("relation", target.collection_kind, target.original_index))
        return ((anchor.file_path, anchor.start_line, anchor.start_column,
                 anchor.end_line, anchor.end_column), chosen)


@dataclass(frozen=True, slots=True)
class JobRequest:
    scope: RequestScope
    conversation_id: str
    question: str
    context_digest: str
    model: str
    config_revision: int
    consent_revision: int
    normalized_scope: str
    draft: ProviderDraft = field(repr=False)
    expected_identity: PublicationIdentity = field(repr=False)
    root_retrieval: RootRetrieval | None = field(default=None, repr=False)
    seed_search: bool = False
    budget: ExplanationBudgetConfig | None = field(default=None, repr=False)
    purpose: Literal["explanation", "project_understanding"] = "explanation"
    understanding: UnderstandingRef | None = None
    understanding_owner: tuple[str, str, str] | None = None
    understanding_owner_revision: int | None = None

    def __post_init__(self) -> None:
        if self.understanding is not None:
            if (type(self.understanding) is not UnderstandingRef
                    or type(self.understanding_owner) is not tuple or len(self.understanding_owner) != 3
                    or type(self.understanding_owner_revision) is not int
                    or self.understanding_owner_revision < 0 or self.purpose != "explanation"
                    or self.seed_search or self.root_retrieval is not None):
                raise ValueError("Invalid internal understanding binding.")
        elif self.understanding_owner is not None or self.understanding_owner_revision is not None:
            raise ValueError("Invalid internal understanding binding.")
        if (type(self.seed_search) is not bool
                or type(self.purpose) is not str
                or self.purpose not in ("explanation", "project_understanding")
                or (self.purpose == "project_understanding"
                    and (self.seed_search or self.root_retrieval is not None))
                or (self.budget is not None
                    and type(self.budget) is not ExplanationBudgetConfig)):
            raise ValueError("Invalid internal job request.")
        if self.budget is not None:
            self.budget.__post_init__()
        if self.root_retrieval is not None:
            if type(self.root_retrieval) is not RootRetrieval:
                raise ValueError("Invalid internal job request.")
            self.root_retrieval.__post_init__()
        if (type(self.scope) is not RequestScope or type(self.draft) is not ProviderDraft
                or type(self.expected_identity) is not PublicationIdentity):
            raise ValueError("Invalid internal job request.")
        if (self.expected_identity.project_epoch != self.scope.project_epoch
                or self.expected_identity.analysis_generation != self.scope.analysis_generation):
            raise ValueError("Invalid internal job request.")
        for value in (self.conversation_id, self.question, self.context_digest,
                      self.model, self.normalized_scope):
            if type(value) is not str or not value or len(value) > 16_384:
                raise ValueError("Invalid internal job request.")
            value.encode("utf-8")
        if (type(self.config_revision) is not int or self.config_revision < 0
                or type(self.consent_revision) is not int or self.consent_revision < 0
                or type(self.draft.body) is not bytes
                or not 1 <= len(self.draft.body) <= 256 * 1024
                or type(self.draft.max_output_tokens) is not int
                or self.draft.max_output_tokens < 1
                or (self.budget is not None
                    and self.draft.max_output_tokens != self.budget.final_output_tokens)):
            raise ValueError("Invalid internal job request.")

    def digest(self) -> str:
        values = (self.scope.analysis_generation,
                  self.conversation_id, self.question, self.context_digest,
                  self.model, self.config_revision, self.consent_revision,
                  self.normalized_scope,
                  None if self.root_retrieval is None else self.root_retrieval.canonical(),
                  self.seed_search, self.purpose,
                  None if self.understanding is None else self.understanding.to_public(),
                  self.understanding_owner, self.understanding_owner_revision,
                  None if self.budget is None else self.budget.canonical())
        encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    status: JobStatus
    cancel_requested: bool
    cancel_accepted: bool
    dispatch_closed: bool
    resources_released: bool
    issue: SafeIssue | None = None


@dataclass(frozen=True, slots=True)
class _RetiredJob:
    """Bounded lifecycle state retained without the live ring or request."""

    snapshot: JobSnapshot
    seq: int
    kind: JobKind
    publication: PublicationIdentity
    events: tuple[dict[str, object], ...]
    public_state: PollState | None = field(default=None, repr=False)
    input_accounting: InputAccountingSnapshot | None = None
    project_coverage: ProjectCoverage | None = None


    def __post_init__(self) -> None:
        if self.public_state is not None:
            object.__setattr__(self, "public_state", replace(self.public_state,
                lifecycle=self.snapshot, seq=self.seq,
                answer=self.public_state.answer if self.snapshot.status is JobStatus.COMPLETED else None))


@dataclass(slots=True)
class _Job:
    job_id: str
    request: JobRequest
    digest: str
    ring: EventRing
    conversation_revision: int | None = None
    authority_origin: str | None = None
    authority_endpoint: str | None = None
    ledger: BudgetLedger | None = None
    status: JobStatus = JobStatus.QUEUED
    cancel_requested: bool = False
    cancel_accepted: bool = False
    closing: bool = False
    dispatch_closed: bool = False
    resources_released: bool = False
    prepared: PreparedRequest | None = None
    provider: ProviderAdapter | None = None
    provider_released: bool = False
    task: Task[None] | None = None
    dispatched: bool = False
    admitted_at: float = 0.0
    deadline: float = 0.0
    admission_pending: bool = True
    launch_waiting: bool = False
    b_submitting: bool = False
    b_future: Future[Any] | None = None
    analysis_snapshot: AnalysisSnapshot | None = None
    issue: SafeIssue | None = None
    tool_session: ToolSession | None = None
    provider_factory: Callable[[], ProviderAdapter] | None = field(default=None, repr=False)
    public_identity: PollIdentity | None = None
    validated_answer: Answer | None = field(default=None, repr=False)
    observed: ObservedUsage | None = None
    input_accounting: InputAccountingSnapshot | None = None
    validation_capture: Capture | None = field(default=None, repr=False)
    project_coverage: ProjectCoverage | None = None

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(self.job_id, self.status, self.cancel_requested,
                           self.cancel_accepted, self.dispatch_closed,
                           self.resources_released, self.issue)


@dataclass(slots=True)
class _ConnectionJob:
    """Source-free admission; owns no project request, evidence or conversation."""

    job_id: str
    config: ProviderConfig
    request_id: str
    resources: _ConnectionResources = field(repr=False)
    provider_factory: Callable[[], ProviderAdapter] | None = field(repr=False)
    status: JobStatus = JobStatus.QUEUED
    cancel_requested: bool = False
    cancel_accepted: bool = False
    dispatch_closed: bool = False
    resources_released: bool = False
    closing: bool = False
    task: Task[None] | None = field(default=None, repr=False)
    issue: SafeIssue | None = None
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    observed: ObservedUsage | None = None
    seq: int = 0
    events: deque[DisplayEvent] = field(default_factory=lambda: deque(maxlen=256), repr=False)
    input_accounting: InputAccountingSnapshot | None = None
    connection_capabilities: ConnectionCapabilities | None = None

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(self.job_id, self.status, self.cancel_requested,
                           self.cancel_accepted, self.dispatch_closed,
                           self.resources_released, self.issue)


@dataclass(frozen=True, slots=True)
class _RetiredConnection:
    """Safe bounded history without provider, key, capture or factory references."""

    state: PollState
    events: tuple[DisplayEvent, ...]
    input_accounting: InputAccountingSnapshot | None = None


@dataclass(slots=True)
class _NavigationJob:
    """Read-only check with an independently revocable retained-evidence lease."""

    job_id: str
    scope: RequestScope
    publication: PublicationIdentity
    identity: PollIdentity
    retained_identity: RetainedEvidenceIdentity
    owner: tuple[str, str, str]
    evidence_id: str
    action: Literal["source", "graph"]
    context: AssistantContext | None = field(repr=False)
    retained_context: RetainedEvidenceContext | None = field(repr=False)
    lease: RetainedEvidenceLease | None = field(repr=False)
    status: JobStatus = JobStatus.QUEUED
    cancel_requested: bool = False
    cancel_accepted: bool = False
    dispatch_closed: bool = False
    resources_released: bool = False
    closing: bool = False
    issue: SafeIssue | None = None
    target: NavigationTarget | None = field(default=None, repr=False)
    task: Task[None] | None = field(default=None, repr=False)
    b_submitting: bool = False
    b_future: Future[NavigationTarget] | None = field(default=None, repr=False)
    seq: int = 0
    events: deque[DisplayEvent] = field(default_factory=lambda: deque(maxlen=256), repr=False)
    understanding: UnderstandingRef | None = None

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(self.job_id, self.status, self.cancel_requested,
                           self.cancel_accepted, self.dispatch_closed,
                           self.resources_released, self.issue)


@dataclass(frozen=True, slots=True)
class _RetiredNavigation:
    state: PollState
    publication: PublicationIdentity
    retained_identity: RetainedEvidenceIdentity
    owner: tuple[str, str, str]
    events: tuple[DisplayEvent, ...]
    understanding: UnderstandingRef | None = None
