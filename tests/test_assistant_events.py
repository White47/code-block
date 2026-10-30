"""Standalone C4 event ring contracts."""

import json
import unittest

from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.events import EventRing


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class EventRingTests(unittest.TestCase):
    def test_ring_reset_has_authoritative_state_and_terminal_updates_advance_seq(self):
        ring = EventRing("job-1", kind="explanation")
        for _ in range(257):
            ring.append_text("x")
        lagged = ring.poll(0)
        self.assertTrue(lagged["reset"])
        self.assertEqual(lagged["events"], [])
        self.assertEqual(lagged["seq"], 257)
        self.assertEqual(lagged["text"], "x" * 257)
        self.assertEqual(lagged["status"], "Queued")
        recent = ring.poll(1)
        self.assertFalse(recent["reset"])
        self.assertEqual([event["seq"] for event in recent["events"]], list(range(2, 34)))
        ring.set_status(JobStatus.COMPLETED)
        completed = ring.poll(257)
        self.assertEqual(completed["events"][0]["resultReady"], True)
        ring.update_confirmations(dispatch_closed=True, resources_released=True)
        confirmed = ring.poll(258)
        self.assertEqual(confirmed["seq"], 259)
        self.assertTrue(confirmed["resourcesReleased"])
        self.assertEqual(confirmed["events"][0]["seq"], 259)

    def test_completed_result_can_become_stale_after_dependency_change(self):
        ring = EventRing("job-1", kind="explanation")
        ring.set_status(JobStatus.COMPLETED)
        ring.set_status(JobStatus.STALE)
        state = ring.poll(1)
        self.assertEqual(state["status"], "Stale")
        self.assertEqual(state["events"][0]["status"], "Stale")

    def test_utf8_chunks_and_exact_json_limits(self):
        ring = EventRing("job-1", kind="explanation")
        ring.append_text('😀"\\\n' * 1500)
        cursor = 0
        chunks = []
        while cursor < ring.seq:
            poll = ring.poll(cursor)
            self.assertFalse(poll["reset"])
            self.assertLessEqual(len(encoded(poll["events"])), 64 * 1024)
            self.assertLessEqual(len(poll["events"]), 32)
            for event in poll["events"]:
                self.assertLessEqual(len(encoded(event)), 8 * 1024)
                chunks.append(event["text"])
            cursor = poll["events"][-1]["seq"]
        self.assertEqual("".join(chunks), '😀"\\\n' * 1500)
        self.assertEqual(ring.poll(cursor)["text"], '😀"\\\n' * 1500)

    def test_cursor_validation_and_delta_payload(self):
        ring = EventRing("job-1", kind="explanation")
        for cursor in (-1, True, 1, 1.0, "0"):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                ring.poll(cursor)
        ring.append_text("😀" * 16384)
        result = ring.poll(0)
        self.assertFalse(result["reset"])
        self.assertLessEqual(len(result["events"]), 32)
        self.assertLessEqual(len(encoded(result["events"])), 64 * 1024)
        self.assertGreater(result["events"][0]["seq"], 0)
        self.assertLess(result["events"][-1]["seq"], ring.seq)
        resumed = ring.poll(result["events"][-1]["seq"])
        self.assertFalse(resumed["reset"])
        self.assertEqual(resumed["events"][-1]["seq"], ring.seq)

    def test_closed_public_events_and_text_bound(self):
        ring = EventRing("job-1", kind="explanation")
        with self.assertRaises(ValueError):
            ring.append_text("x" * (64 * 1024 + 1))
        with self.assertRaises(ValueError):
            ring.append_text("\ud800")
        with self.assertRaises(ValueError):
            ring.append_text({"provider": "raw"})
        self.assertEqual(ring.seq, 0)
        ring.set_answer('"' * (64 * 1024))
        with self.assertRaises(ValueError):
            ring.append_text('"' * (20 * 1024))
        self.assertEqual(ring.seq, 0)
        self.assertLessEqual(len(encoded(ring.poll(0))), 160 * 1024)
        navigation = EventRing("job-2", kind="navigation")
        with self.assertRaises(ValueError):
            navigation.append_text("source")
        navigation.set_status(JobStatus.COMPLETED)
        event = navigation.poll(0)["events"][0]
        self.assertEqual(set(event), {"seq", "jobId", "type", "status", "resultReady"})
        self.assertNotIn("text", navigation.poll(0))


if __name__ == "__main__":
    unittest.main()
