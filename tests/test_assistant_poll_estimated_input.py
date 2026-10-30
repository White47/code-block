"""Estimated input accounting has one closed, kind-bound poll representation."""

import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Literal, cast

from codeviz.assistant._usage_values import InputAccountingSnapshot, ObservedUsage
from codeviz.assistant.budget import BudgetUsage
from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.jobs import JobSnapshot
from codeviz.assistant.poll_public import (
    DisplayEvent,
    PollIdentity,
    PollProjectionError,
    PollState,
    poll_to_public,
    snapshot_to_public,
)
from codeviz.assistant.service import AssistantService


class AssistantEstimatedInputPollTests(unittest.TestCase):
    @staticmethod
    def state(
        kind: Literal["explanation", "connection", "navigation"] = "explanation",
    ) -> PollState:
        identity = PollIdentity(
            kind,
            "job",
            None if kind == "connection" else "epoch",
            None if kind == "connection" else 1,
            None if kind == "connection" else "conversation",
            None if kind == "connection" else 0,
        )
        return PollState(
            identity,
            JobSnapshot("job", JobStatus.RUNNING, False, False, False, False),
            1,
            None if kind == "connection" else 1,
            0,
        )

    def test_strict_and_estimated_usage_have_only_the_approved_closed_shapes(self) -> None:
        strict = cast(dict[str, object], snapshot_to_public(self.state())["usage"])
        self.assertEqual(set(strict), {"reserved", "observed"})

        estimated = replace(
            self.state(),
            observed=ObservedUsage(0, 3, "actual"),
            input_accounting=InputAccountingSnapshot("wire-bytes-v1", 20_000, 0, None),
        )
        usage = cast(dict[str, object], snapshot_to_public(estimated)["usage"])
        self.assertEqual(set(usage), {"reserved", "observed", "inputAccounting"})
        self.assertEqual(usage["inputAccounting"], {
            "basis": "estimated",
            "method": "wire-bytes-v1",
            "limitTokens": 20_000,
            "upperBound": False,
            "knownActualInputTokens": 0,
            "blockedReason": None,
        })

    def test_unknown_keeps_known_actual_lower_bound_and_limits_are_kind_specific(self) -> None:
        partial = replace(
            self.state(),
            observed=ObservedUsage(),
            input_accounting=InputAccountingSnapshot(
                "wire-bytes-v1", 20_000, 19_000, "usage_unknown"
            ),
        )
        usage = cast(dict[str, object], snapshot_to_public(partial)["usage"])
        accounting = cast(dict[str, object], usage["inputAccounting"])
        self.assertEqual(accounting["knownActualInputTokens"], 19_000)
        self.assertEqual(cast(dict[str, object], usage["observed"])["inputTokens"], None)

        wrong_kinds = tuple(replace(
            self.state("connection"),
            input_accounting=InputAccountingSnapshot("wire-bytes-v1", limit, 0, None),
        ) for limit in (20_000, 100_000))
        navigation = replace(
            self.state("navigation"),
            evidence_id="evidence",
            action="source",
            input_accounting=InputAccountingSnapshot("wire-bytes-v1", 20_000, 0, None),
        )
        for malformed in (*wrong_kinds, navigation):
            with self.subTest(kind=malformed.identity.kind), self.assertRaises(PollProjectionError):
                snapshot_to_public(malformed)

    def test_usage_events_and_reset_use_the_same_estimated_shape(self) -> None:
        state = replace(
            self.state("connection"),
            seq=1,
            observed=ObservedUsage(4, 0, "actual"),
            input_accounting=InputAccountingSnapshot("wire-bytes-v1", 1024, 4, None),
        )
        usage = cast(dict[str, object], snapshot_to_public(state)["usage"])
        event = DisplayEvent(1, "usage", {"usage": usage})
        delivered = poll_to_public(state, after_seq=0, events=(event,))
        self.assertEqual(cast(dict[str, object], delivered["events"][0])["usage"], usage)
        reset = poll_to_public(state, after_seq=0, reset=True)
        self.assertEqual(reset["usage"], usage)

    def test_malformed_estimated_usage_and_inconsistent_actual_total_fail_closed(self) -> None:
        state = replace(
            self.state(),
            seq=1,
            observed=ObservedUsage(7, 1, "actual"),
            input_accounting=InputAccountingSnapshot("wire-bytes-v1", 20_000, 7, None),
        )
        valid = cast(dict[str, object], snapshot_to_public(state)["usage"])
        malformed: list[dict[str, object]] = []

        extra = deepcopy(valid)
        extra["extra"] = False
        malformed.append(extra)
        for key, value in (
            ("method", "WIRE-BYTES-V1"),
            ("upperBound", True),
            ("knownActualInputTokens", True),
            ("knownActualInputTokens", 2**53),
            ("limitTokens", 1024),
        ):
            candidate = deepcopy(valid)
            cast(dict[str, object], candidate["inputAccounting"])[key] = value
            malformed.append(candidate)
        inner_extra = deepcopy(valid)
        cast(dict[str, object], inner_extra["inputAccounting"])["detail"] = "forbidden"
        malformed.append(inner_extra)
        mismatch = deepcopy(valid)
        cast(dict[str, object], mismatch["inputAccounting"])["knownActualInputTokens"] = 6
        malformed.append(mismatch)

        for usage in malformed:
            with self.subTest(usage=usage), self.assertRaises(PollProjectionError):
                poll_to_public(
                    state,
                    after_seq=0,
                    events=(DisplayEvent(1, "usage", {"usage": usage}),),
                )

    def test_blocked_reason_matches_the_policy_observation_state(self) -> None:
        malformed = (
            replace(
                self.state(),
                observed=ObservedUsage(7, 1, "actual"),
                input_accounting=InputAccountingSnapshot(
                    "wire-bytes-v1", 20_000, 7, "usage_unknown"
                ),
            ),
            replace(
                self.state(),
                observed=ObservedUsage(),
                input_accounting=InputAccountingSnapshot(
                    "wire-bytes-v1", 20_000, 20_001, "observed_overrun"
                ),
            ),
            replace(
                self.state(),
                observed=ObservedUsage(20_000, 1, "actual"),
                input_accounting=InputAccountingSnapshot(
                    "wire-bytes-v1", 20_000, 20_000, "observed_overrun"
                ),
            ),
        )

        for state in malformed:
            with self.subTest(accounting=state.input_accounting), self.assertRaises(
                PollProjectionError
            ):
                snapshot_to_public(state)

        valid_overrun = replace(
            self.state(),
            observed=ObservedUsage(20_001, 1, "actual"),
            input_accounting=InputAccountingSnapshot(
                "wire-bytes-v1", 20_000, 20_001, "observed_overrun"
            ),
        )
        usage = cast(dict[str, object], snapshot_to_public(valid_overrun)["usage"])
        self.assertEqual(
            cast(dict[str, object], usage["inputAccounting"])["blockedReason"],
            "observed_overrun",
        )

        try:
            large_overrun = replace(
                self.state(),
                observed=ObservedUsage(100_001, 1, "actual"),
                input_accounting=InputAccountingSnapshot(
                    "wire-bytes-v1", 100_000, 100_001, "observed_overrun"
                ),
            )
        except ValueError as error:
            self.fail(f"new explanation accounting limit was rejected: {error}")
        large_usage = cast(dict[str, object], snapshot_to_public(large_overrun)["usage"])
        self.assertEqual(
            cast(dict[str, object], large_usage["inputAccounting"])["limitTokens"],
            100_000,
        )

        under_large_limit = replace(
            large_overrun,
            observed=ObservedUsage(20_001, 1, "actual"),
            input_accounting=InputAccountingSnapshot(
                "wire-bytes-v1", 100_000, 20_001, "observed_overrun"
            ),
        )
        with self.assertRaises(PollProjectionError):
            snapshot_to_public(under_large_limit)

    def test_service_copies_live_accounting_into_explanation_and_connection_poll_states(self) -> None:
        explanation_accounting = InputAccountingSnapshot(
            "wire-bytes-v1", 20_000, 5, "usage_unknown"
        )
        explanation = SimpleNamespace(
            public_identity=self.state().identity,
            snapshot=lambda: self.state().lifecycle,
            request=SimpleNamespace(config_revision=1, consent_revision=1),
            ring=SimpleNamespace(seq=3),
            ledger=None,
            observed=ObservedUsage(),
            validated_answer=None,
            status=JobStatus.FAILED,
            input_accounting=explanation_accounting,
        )
        explanation_state = AssistantService._explanation_state_locked(explanation)
        self.assertIs(explanation_state.input_accounting, explanation_accounting)

        connection_accounting = InputAccountingSnapshot("wire-bytes-v1", 1024, 0, None)
        connection = SimpleNamespace(
            job_id="job",
            snapshot=lambda: self.state("connection").lifecycle,
            config=SimpleNamespace(revision=1),
            seq=4,
            resources=SimpleNamespace(ledger=SimpleNamespace(usage=BudgetUsage())),
            observed=ObservedUsage(0, 0, "actual"),
            status=JobStatus.RUNNING,
            input_accounting=connection_accounting,
        )
        connection_state = AssistantService._connection_state_locked(connection)
        self.assertIs(connection_state.input_accounting, connection_accounting)


if __name__ == "__main__":
    unittest.main()
