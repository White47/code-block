"""Closed connection capability projection and lifecycle propagation."""

import unittest
from dataclasses import replace
from time import monotonic, sleep
from unittest.mock import patch

from codeviz.assistant._connection_values import ConnectionCapabilities
from codeviz.assistant._usage_values import ObservedUsage
from codeviz.assistant.bridge_requests import (
    ConnectionRequest,
    PollRequest,
    parse_configure,
)
from codeviz.assistant.budget import BudgetUsage
from codeviz.assistant.connection import ConnectionProbeResult
from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.jobs import JobSnapshot
from codeviz.assistant.poll_public import (
    PollIdentity,
    PollProjectionError,
    PollState,
    snapshot_to_public,
)
from codeviz.assistant.service import _AssistantRuntime


class ConnectionCapabilityProjectionTests(unittest.TestCase):
    @staticmethod
    def capabilities() -> ConnectionCapabilities:
        return ConnectionCapabilities(True, False, True, "local-json-schema")

    @staticmethod
    def connection_state(status: JobStatus = JobStatus.COMPLETED) -> PollState:
        return PollState(
            PollIdentity("connection", "job", None, None, None, None),
            JobSnapshot(
                "job",
                status,
                False,
                False,
                status not in (JobStatus.QUEUED, JobStatus.RUNNING),
                False,
            ),
            1,
            None,
            0,
            connection_supported=status is JobStatus.COMPLETED,
        )

    def runtime(self) -> _AssistantRuntime:
        def unused_provider_factory(*_args: object) -> object:
            raise AssertionError("fake connection runner must not construct a provider")

        runtime = _AssistantRuntime(provider_factory=unused_provider_factory)  # type: ignore[arg-type]
        runtime.configure(parse_configure({
            "baseUrl": "https://provider.invalid/v1",
            "model": "probe-model",
            "key": "offline-test-only",
            "enabled": True,
        }))
        self.addCleanup(runtime.shutdown)
        return runtime

    def wait_released(self, runtime: _AssistantRuntime, job_id: str) -> dict[str, object]:
        deadline = monotonic() + 3
        state: dict[str, object] = {}
        while monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, None, 0))
            if state["resourcesReleased"] is True:
                return state
            sleep(0.005)
        self.fail(f"Connection lifecycle did not settle: {state}")

    def test_local_validation_is_not_reported_as_remote_strictness(self) -> None:
        state = replace(
            self.connection_state(),
            connection_capabilities=self.capabilities(),
        )

        self.assertEqual(snapshot_to_public(state)["connectionResult"], {
            "toolSupported": True,
            "structuredOutputSupported": False,
            "jsonObjectSupported": True,
            "outputValidation": "local-json-schema",
        })

    def test_probe_result_accepts_only_the_exact_local_record(self) -> None:
        capabilities = self.capabilities()

        result = ConnectionProbeResult(
            BudgetUsage(),
            ObservedUsage(),
            None,
            capabilities,
        )

        self.assertIs(result.connection_capabilities, capabilities)

    def test_local_record_rejects_wrong_types_modes_and_hybrids(self) -> None:
        malformed = (
            (1, False, True, "local-json-schema"),
            (True, True, True, "local-json-schema"),
            (True, False, False, "local-json-schema"),
            (True, False, True, "remote-strict"),
        )
        for values in malformed:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ConnectionCapabilities(*values)  # type: ignore[arg-type]

    def test_projection_revalidates_exact_type_kind_status_and_mutation(self) -> None:
        class DerivedCapabilities(ConnectionCapabilities):
            pass

        invalid_mode = self.capabilities()
        object.__setattr__(invalid_mode, "output_validation", "remote-strict")
        hybrid = self.capabilities()
        object.__setattr__(hybrid, "structured_output_supported", True)
        explanation = PollState(
            PollIdentity("explanation", "job", "epoch", 1, "chat", 0),
            JobSnapshot("job", JobStatus.RUNNING, False, False, False, False),
            1,
            1,
            0,
            connection_capabilities=self.capabilities(),
        )
        malformed = (
            replace(self.connection_state(), connection_capabilities=invalid_mode),
            replace(self.connection_state(), connection_capabilities=hybrid),
            replace(
                self.connection_state(),
                connection_capabilities=DerivedCapabilities(
                    True, False, True, "local-json-schema"),
            ),
            explanation,
            replace(
                self.connection_state(JobStatus.RUNNING),
                connection_capabilities=self.capabilities(),
            ),
        )

        for state in malformed:
            with self.subTest(kind=state.identity.kind, status=state.lifecycle.status), \
                    self.assertRaises(PollProjectionError):
                snapshot_to_public(state)

    def test_fake_runner_local_result_survives_retirement_and_replay(self) -> None:
        runtime = self.runtime()
        emitted = self.capabilities()

        async def fake_runner(*_args: object, **_kwargs: object) -> ConnectionProbeResult:
            return ConnectionProbeResult(BudgetUsage(), ObservedUsage(), None, emitted)

        request = ConnectionRequest(1, "local-capability")
        with patch("codeviz.assistant.service.run_connection", side_effect=fake_runner):
            started = runtime.test_connection(request)
            job_id = started["jobId"]
            self.assertIsInstance(job_id, str)
            state = self.wait_released(runtime, job_id)  # type: ignore[arg-type]

        expected = {
            "toolSupported": True,
            "structuredOutputSupported": False,
            "jsonObjectSupported": True,
            "outputValidation": "local-json-schema",
        }
        self.assertEqual(state["status"], "Completed")
        self.assertEqual(state["connectionResult"], expected)
        replay = runtime.test_connection(request)
        self.assertEqual(replay["jobId"], job_id)
        self.assertEqual(
            runtime.poll(PollRequest(job_id, None, 0))["connectionResult"],  # type: ignore[arg-type]
            expected,
        )
        retired = runtime._engine._connection_completed[job_id]  # type: ignore[index,union-attr]
        self.assertIsNot(retired.state.connection_capabilities, emitted)
        self.assertFalse(hasattr(retired, "connection_capabilities"))

    def test_mutated_runner_result_fails_before_capability_assignment(self) -> None:
        runtime = self.runtime()
        mutated = self.capabilities()
        result = ConnectionProbeResult(BudgetUsage(), ObservedUsage(), None, mutated)
        object.__setattr__(mutated, "output_validation", "remote-strict")

        async def fake_runner(*_args: object, **_kwargs: object) -> ConnectionProbeResult:
            return result

        with patch("codeviz.assistant.service.run_connection", side_effect=fake_runner):
            started = runtime.test_connection(ConnectionRequest(1, "mutated-capability"))
            job_id = started["jobId"]
            self.assertIsInstance(job_id, str)
            state = self.wait_released(runtime, job_id)  # type: ignore[arg-type]

        self.assertEqual(state["status"], "Failed")
        self.assertIsNone(state["connectionResult"])
        retired = runtime._engine._connection_completed[job_id]  # type: ignore[index,union-attr]
        self.assertIsNone(retired.state.connection_capabilities)


if __name__ == "__main__":
    unittest.main()
