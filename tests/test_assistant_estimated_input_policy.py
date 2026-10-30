"""Per-job cumulative input accounting without provider or service I/O."""

import importlib
import json
import unittest
from dataclasses import replace
from threading import Event, Thread

from assistant_estimated_fixtures import PROFILE, prepared

from codeviz.assistant._usage_values import ObservedUsage
from codeviz.assistant._wire_accounting import WireAccountingError, WireAccountingPolicy
from codeviz.assistant.budget import EXPLANATION_BUDGET, BudgetLedger, BudgetReservation
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.provider import PreparedRequest


def strict_prepared(identity: str, input_tokens: int | None = None) -> PreparedRequest:
    body = json.dumps(
        {"messages": [{"role": "user", "content": "x"}]},
        separators=(",", ":"),
    ).encode("utf-8")
    return PreparedRequest(
        identity,
        "POST",
        "https://strict.invalid:443/v1/chat/completions",
        body,
        32,
        1_000_000_000.0,
        len(body) if input_tokens is None else input_tokens,
        (),
        (),
        True,
    )


class EstimatedInputPolicyTests(unittest.TestCase):
    def policy(self) -> tuple[BudgetLedger, WireAccountingPolicy]:
        ledger = BudgetLedger(clock=lambda: 0.0)
        return ledger, WireAccountingPolicy(ledger, trusted_profiles=(PROFILE,))

    def test_production_policy_uses_large_input_and_phase_output_limits(self):
        ledger = BudgetLedger(EXPLANATION_BUDGET.limits(), clock=lambda: 0.0)
        policy = WireAccountingPolicy(
            ledger,
            trusted_profiles=(PROFILE,),
            budget=EXPLANATION_BUDGET,
        )

        for index in range(2):
            identity = f"large-{index}"
            handle = replace(prepared(30_000, identity), max_output_tokens=512)
            policy.admit(handle, "ordinary")
            policy.dispatch(identity)
            policy.observe(identity, ObservedUsage(15_000, 1, "actual"))
            policy.finalize(identity)

        self.assertEqual(policy.snapshot.limit_tokens, 100_000)
        self.assertIsNone(policy.snapshot.blocked_reason)
        self.assertEqual(ledger.usage.input_tokens, 60_000)
        with self.assertRaises(WireAccountingError) as caught:
            policy.admit(replace(
                prepared(50_000, "too-large"), max_output_tokens=512), "ordinary")
        self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(ledger.usage.model_requests, 2)

        final_ledger = BudgetLedger(EXPLANATION_BUDGET.limits(), clock=lambda: 0.0)
        final_policy = WireAccountingPolicy(
            final_ledger,
            trusted_profiles=(PROFILE,),
            budget=EXPLANATION_BUDGET,
        )
        with self.assertRaises(WireAccountingError):
            final_policy.admit(
                replace(prepared(1_000, "oversized-final"), max_output_tokens=2_001),
                "final",
            )
        self.assertEqual(final_ledger.usage.model_requests, 0)

    def test_deepseek_config_requires_exact_phase_allocations_atomically(self):
        budget = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        ledger = BudgetLedger(budget.limits(), clock=lambda: 0.0)
        policy = WireAccountingPolicy(
            ledger, trusted_profiles=(PROFILE,), budget=budget)

        wrong = replace(prepared(1_000, "wrong-final"), max_output_tokens=2_000)
        before = ledger.usage
        with self.assertRaises(WireAccountingError) as caught:
            policy.admit(wrong, "final")
        self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(ledger.usage, before)

        final = replace(prepared(1_000, "exact-final"), max_output_tokens=4_096)
        policy.admit(final, "final")
        self.assertEqual(ledger.usage.output_tokens, 4_096)
        policy.dispatch(final.preparation_id)
        policy.observe(final.preparation_id, ObservedUsage(1, 1, "actual"))
        policy.finalize(final.preparation_id)

        wrong_repair = replace(prepared(1_000, "wrong-repair"), max_output_tokens=4_096)
        before = ledger.usage
        with self.assertRaises(WireAccountingError):
            policy.admit(wrong_repair, "repair")
        self.assertEqual(ledger.usage, before)
        repair = replace(prepared(1_000, "exact-repair"), max_output_tokens=2_000)
        policy.admit(repair, "repair")
        self.assertEqual(ledger.usage.output_tokens, 6_096)

    def test_deepseek_fifth_ordinary_is_rejected_without_reservation(self):
        budget = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        ledger = BudgetLedger(budget.limits(), clock=lambda: 0.0)
        policy = WireAccountingPolicy(
            ledger, trusted_profiles=(PROFILE,), budget=budget)

        for index in range(4):
            handle = replace(
                prepared(1_000, f"ordinary-{index}"), max_output_tokens=512)
            policy.admit(handle, "ordinary")
            policy.dispatch(handle.preparation_id)
            policy.observe(handle.preparation_id, ObservedUsage(1, 1, "actual"))
            policy.finalize(handle.preparation_id)
        before = ledger.usage
        fifth = replace(prepared(1_000, "ordinary-4"), max_output_tokens=512)
        with self.assertRaises(WireAccountingError):
            policy.admit(fifth, "ordinary")
        self.assertEqual(ledger.usage, before)

    def test_precharged_ledger_cannot_shrink_a_configured_stage(self):
        budget = importlib.import_module(
            "codeviz.assistant._deepseek_admission")._deepseek_explanation_budget()
        for phase, phase_limit in (("ordinary", 512), ("final", 4_096), ("repair", 2_000)):
            with self.subTest(phase=phase):
                ledger = BudgetLedger(budget.limits(), clock=lambda: 0.0)
                policy = WireAccountingPolicy(
                    ledger, trusted_profiles=(PROFILE,), budget=budget)
                if phase == "repair":
                    final = replace(prepared(1_000, "final"), max_output_tokens=4_096)
                    policy.admit(final, "final")
                    policy.finalize(final.preparation_id)
                ledger.reserve(BudgetReservation(
                    output_tokens=budget.max_output_tokens - ledger.usage.output_tokens - 1))
                before = ledger.usage
                for allocation in (1, phase_limit):
                    handle = replace(prepared(1_000, "short-stage"), max_output_tokens=allocation)
                    with self.assertRaises(WireAccountingError) as caught:
                        policy.admit(handle, phase)
                    self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
                    self.assertEqual(ledger.usage, before)

        ledger, legacy = self.policy()
        ledger.reserve(BudgetReservation(output_tokens=1_999))
        legacy.admit(replace(prepared(1_000, "legacy-final"), max_output_tokens=1), "final")
        self.assertEqual(ledger.usage.output_tokens, 2_000)

    def test_actual_total_controls_cumulative_admission(self):
        ledger, policy = self.policy()
        policy.admit(prepared(1000), "ordinary")
        policy.dispatch("one")
        policy.observe("one", ObservedUsage(19_000, 1, "actual"))
        policy.finalize("one")

        with self.assertRaises(WireAccountingError) as caught:
            policy.admit(prepared(2000, "two"), "ordinary")

        self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(ledger.usage.model_requests, 1)
        self.assertEqual(policy.snapshot.blocked_reason, "estimated_limit")

    def test_reserved_estimate_is_not_refunded_when_actual_is_lower(self):
        ledger, policy = self.policy()
        policy.admit(prepared(19_000), "ordinary")
        policy.dispatch("one")
        policy.observe("one", ObservedUsage(1000, 1, "actual"))
        policy.finalize("one")

        with self.assertRaises(WireAccountingError) as caught:
            policy.admit(prepared(2000, "two"), "ordinary")

        self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(ledger.usage.model_requests, 1)
        self.assertEqual(policy.snapshot.blocked_reason, "estimated_limit")

    def test_actual_overrun_is_retained_without_rejecting_observation(self):
        ledger, policy = self.policy()
        policy.admit(prepared(1000), "ordinary")
        policy.dispatch("one")

        policy.observe("one", ObservedUsage(21_000, 1, "actual"))
        policy.finalize("one")

        self.assertEqual(policy.observed, ObservedUsage(21_000, 1, "actual"))
        self.assertEqual(policy.snapshot.known_actual_input_tokens, 21_000)
        self.assertEqual(policy.snapshot.blocked_reason, "observed_overrun")
        with self.assertRaises(WireAccountingError) as caught:
            policy.ensure_can_prepare()
        self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")
        self.assertEqual(ledger.usage.model_requests, 1)

    def test_unsafe_reported_totals_are_rejected_before_accounting_mutates(self):
        for basis, input_tokens, output_tokens in (
            ("actual", 2**53, 0),
            ("actual", 0, 2**53),
            ("estimated", 2**53, 0),
            ("estimated", 0, 2**53),
        ):
            with self.subTest(basis=basis, input=input_tokens, output=output_tokens):
                _, policy = self.policy()
                policy.admit(prepared(1000), "ordinary")
                policy.dispatch("one")

                with self.assertRaises(WireAccountingError) as caught:
                    policy.observe(
                        "one",
                        ObservedUsage(input_tokens, output_tokens, basis),  # type: ignore[arg-type]
                    )

                self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
                policy.finalize("one")
                self.assertEqual(policy.observed, ObservedUsage())
                self.assertEqual(policy.snapshot.known_actual_input_tokens, 0)
                self.assertEqual(policy.snapshot.blocked_reason, "usage_unknown")

    def test_actual_zero_is_complete_while_unknown_is_sticky(self):
        _, zero_policy = self.policy()
        zero_policy.admit(prepared(1000), "ordinary")
        zero_policy.dispatch("one")
        zero_policy.observe("one", ObservedUsage(0, 0, "actual"))
        zero_policy.finalize("one")

        zero_policy.admit(prepared(1000, "two"), "ordinary")
        self.assertEqual(zero_policy.observed, ObservedUsage(0, 0, "actual"))
        self.assertIsNone(zero_policy.snapshot.blocked_reason)

        for usage in (ObservedUsage(), ObservedUsage(1000, 1, "estimated")):
            with self.subTest(basis=usage.basis):
                _, unknown_policy = self.policy()
                unknown_policy.admit(prepared(1000), "ordinary")
                unknown_policy.dispatch("one")
                unknown_policy.observe("one", usage)
                unknown_policy.finalize("one")

                self.assertEqual(unknown_policy.observed, ObservedUsage())
                self.assertEqual(unknown_policy.snapshot.blocked_reason, "usage_unknown")
                with self.assertRaises(WireAccountingError) as caught:
                    unknown_policy.admit(prepared(1000, "two"), "ordinary")
                self.assertEqual(caught.exception.code, "AI_BUDGET_EXCEEDED")

    def test_finalize_only_marks_dispatched_missing_usage_unknown(self):
        ledger, policy = self.policy()
        policy.admit(prepared(1000), "ordinary")

        policy.finalize("one")
        policy.admit(prepared(1000, "two"), "ordinary")
        self.assertEqual(ledger.usage.model_requests, 2)
        self.assertIsNone(policy.snapshot.blocked_reason)

        policy.dispatch("two")
        policy.finalize("two")
        policy.finalize("two")
        self.assertEqual(policy.snapshot.blocked_reason, "usage_unknown")
        with self.assertRaises(WireAccountingError):
            policy.ensure_can_prepare()

    def test_legacy_strict_facade_preserves_unknown_and_discrepancy_rules(self):
        ledger = BudgetLedger(clock=lambda: 0.0)
        policy = WireAccountingPolicy(ledger)
        first, _ = policy.admit(strict_prepared("one"), "ordinary")

        policy.report_usage(first, {
            "inputTokens": None,
            "outputTokens": None,
            "basis": "unknown",
        })
        second, _ = policy.admit(strict_prepared("two"), "ordinary")
        with self.assertRaises(WireAccountingError) as caught:
            policy.report_usage(second, {
                "inputTokens": second.input_tokens,
                "outputTokens": second.output_tokens + 1,
                "basis": "actual",
            })

        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")
        self.assertEqual(ledger.usage.model_requests, 2)
        self.assertIsNone(policy.snapshot)

    def test_legacy_facade_rejects_estimated_admission(self):
        _, policy = self.policy()
        reservation, _ = policy.admit(prepared(1000), "ordinary")

        with self.assertRaises(WireAccountingError) as caught:
            policy.report_usage(reservation, {
                "inputTokens": 1000,
                "outputTokens": 1,
                "basis": "actual",
            })

        self.assertEqual(caught.exception.code, "AI_PROTOCOL_INCOMPATIBLE")

    def test_cancelled_ledger_error_passes_through_admission(self):
        ledger, policy = self.policy()
        ledger.cancel()

        with self.assertRaises(AgentError) as caught:
            policy.admit(prepared(1000), "ordinary")

        self.assertEqual(caught.exception.code, "cancelled")

    def test_admission_clock_can_reenter_snapshot_without_deadlock(self):
        policy_holder: dict[str, WireAccountingPolicy] = {}
        snapshots = []

        def clock() -> float:
            policy = policy_holder.get("policy")
            if policy is not None:
                snapshots.append(policy.snapshot)
            return 0.0

        ledger = BudgetLedger(clock=clock)
        policy = WireAccountingPolicy(ledger, trusted_profiles=(PROFILE,))
        policy_holder["policy"] = policy
        completed = Event()
        errors: list[Exception] = []

        def admit() -> None:
            try:
                policy.admit(prepared(1000), "ordinary")
            except Exception as error:
                errors.append(error)
            finally:
                completed.set()

        thread = Thread(target=admit, daemon=True)
        thread.start()

        self.assertTrue(completed.wait(1.0), "admission deadlocked in the ledger clock")
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertEqual(ledger.usage.model_requests, 1)


if __name__ == "__main__":
    unittest.main()
