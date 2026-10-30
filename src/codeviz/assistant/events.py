"""Bounded, public per-job event deltas and authoritative poll state."""

from __future__ import annotations

import json
from collections import deque
from threading import RLock
from typing import Literal, cast

from .contracts import JobStatus

JobKind = Literal["explanation", "navigation", "connection"]
_TERMINAL = {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
             JobStatus.STALE}
_EVENT_LIMIT = 8 * 1024
_DELTA_LIMIT = 64 * 1024
_ENVELOPE_LIMIT = 160 * 1024
_TEXT_LIMIT = 64 * 1024


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Invalid assistant event data.") from None


class EventRing:
    """Own safe display events and a bounded current state for one job."""

    def __init__(self, job_id: str, *, kind: JobKind) -> None:
        if (type(job_id) is not str or not job_id or len(_encoded(job_id)) > 130
                or kind not in ("explanation", "navigation", "connection")):
            raise ValueError("Invalid assistant event identity.")
        self._lock = RLock()
        self._job_id = job_id
        self._kind = kind
        self._seq = 0
        self._events: deque[dict[str, object]] = deque(maxlen=256)
        self._status = JobStatus.QUEUED
        self._text = ""
        self._answer: str | None = None
        self._cancel_requested = False
        self._cancel_accepted = False
        self._dispatch_closed = False
        self._resources_released = False

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def retirement_events(self) -> tuple[dict[str, object], ...]:
        """Keep only the terminal status and subsequent confirmation tail."""
        with self._lock:
            events = tuple(self._events)
            first = next((index for index, event in enumerate(events)
                          if event["type"] == "status"
                          and event.get("status") in {status.value for status in _TERMINAL}),
                         len(events))
            while first > 0 and events[first - 1]["type"] == "confirmation":
                first -= 1
            return tuple(dict(event) for event in events[first:][-8:])

    def _event(self, event_type: str, **fields: object) -> dict[str, object]:
        return {"seq": self._seq + 1, "jobId": self._job_id,
                "type": event_type, **fields}

    def _append(self, event: dict[str, object]) -> None:
        if len(_encoded(event)) > _EVENT_LIMIT:
            raise ValueError("Assistant event exceeds limit.")
        self._seq += 1
        self._events.append(event)

    def append_text(self, text: str) -> None:
        """Append display-only text; a call either publishes every chunk or none."""
        if self._kind != "explanation" or type(text) is not str:
            raise ValueError("Invalid assistant text event.")
        with self._lock:
            if self._status in _TERMINAL:
                raise ValueError("Terminal job cannot receive text.")
            try:
                display_size = len((self._text + text).encode("utf-8"))
            except UnicodeError:
                raise ValueError("Invalid assistant text event.") from None
            if display_size > _TEXT_LIMIT:
                raise ValueError("Assistant display text exceeds limit.")
            if (len(_encoded([self._text + text, self._answer]))
                    > _ENVELOPE_LIMIT - _EVENT_LIMIT - 2048):
                raise ValueError("Assistant poll state exceeds limit.")
            if not text:
                return
            pending: list[dict[str, object]] = []
            start = 0
            while start < len(text):
                low, high = 1, len(text) - start
                while low < high:
                    mid = (low + high + 1) // 2
                    candidate = {"seq": self._seq + len(pending) + 1,
                                 "jobId": self._job_id, "type": "text",
                                 "text": text[start:start + mid]}
                    if len(_encoded(candidate)) <= _EVENT_LIMIT:
                        low = mid
                    else:
                        high = mid - 1
                event = {"seq": self._seq + len(pending) + 1,
                         "jobId": self._job_id, "type": "text",
                         "text": text[start:start + low]}
                if len(_encoded(event)) > _EVENT_LIMIT:
                    raise ValueError("Assistant text chunk exceeds limit.")
                pending.append(event)
                start += low
            for event in pending:
                self._append(event)
            self._text += text

    def set_answer(self, answer: str) -> None:
        """Store a previously validated final answer only in poll state."""
        if self._kind != "explanation" or type(answer) is not str:
            raise ValueError("Invalid assistant answer.")
        try:
            size = len(answer.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Invalid assistant answer.") from None
        if size > _TEXT_LIMIT:
            raise ValueError("Assistant answer exceeds limit.")
        with self._lock:
            if self._status in _TERMINAL:
                raise ValueError("Terminal answer cannot change.")
            if (len(_encoded([self._text, answer]))
                    > _ENVELOPE_LIMIT - _EVENT_LIMIT - 2048):
                raise ValueError("Assistant poll state exceeds limit.")
            self._answer = answer

    def set_status(self, status: JobStatus) -> None:
        if type(status) is not JobStatus:
            raise ValueError("Invalid assistant job status.")
        with self._lock:
            if self._status == status:
                return
            if self._status in _TERMINAL and not (
                self._status is JobStatus.COMPLETED and status is JobStatus.STALE
            ):
                raise ValueError("Terminal job status cannot change.")
            event = self._event("status", status=status.value,
                                resultReady=status is JobStatus.COMPLETED)
            self._append(event)
            self._status = status

    def update_confirmations(self, *, cancel_requested: bool | None = None,
                             cancel_accepted: bool | None = None,
                             dispatch_closed: bool | None = None,
                             resources_released: bool | None = None) -> None:
        supplied = {"cancelRequested": cancel_requested,
                    "cancelAccepted": cancel_accepted,
                    "dispatchClosed": dispatch_closed,
                    "resourcesReleased": resources_released}
        if any(value is not None and type(value) is not bool
               for value in supplied.values()):
            raise ValueError("Invalid assistant confirmation.")
        with self._lock:
            old = {"cancelRequested": self._cancel_requested,
                   "cancelAccepted": self._cancel_accepted,
                   "dispatchClosed": self._dispatch_closed,
                   "resourcesReleased": self._resources_released}
            changed = {key: value for key, value in supplied.items()
                       if value is not None and value != old[key]}
            if not changed:
                return
            if any(value is False for value in changed.values()):
                raise ValueError("Assistant confirmation cannot regress.")
            self._append(self._event("confirmation", **changed))
            self._cancel_requested = old["cancelRequested"] or bool(cancel_requested)
            self._cancel_accepted = old["cancelAccepted"] or bool(cancel_accepted)
            self._dispatch_closed = old["dispatchClosed"] or bool(dispatch_closed)
            self._resources_released = old["resourcesReleased"] or bool(resources_released)

    def poll(self, after_seq: int) -> dict[str, object]:
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError("Invalid assistant event cursor.")
        with self._lock:
            if after_seq > self._seq:
                raise ValueError("Future assistant event cursor.")
            result: dict[str, object] = {
                "jobId": self._job_id, "kind": self._kind, "seq": self._seq,
                "status": self._status.value,
                "cancelRequested": self._cancel_requested,
                "cancelAccepted": self._cancel_accepted,
                "dispatchClosed": self._dispatch_closed,
                "resourcesReleased": self._resources_released,
                "reset": False, "events": [],
            }
            if self._kind == "explanation":
                result["text"] = self._text
                result["answer"] = self._answer
            oldest = cast(int, self._events[0]["seq"]) if self._events else self._seq + 1
            if after_seq < oldest - 1:
                result["reset"] = True
                return result
            events: list[dict[str, object]] = []
            for event in self._events:
                if cast(int, event["seq"]) <= after_seq:
                    continue
                candidate = [*events, event]
                if (len(candidate) > 32 or len(_encoded(candidate)) > _DELTA_LIMIT):
                    break
                result["events"] = candidate
                if len(_encoded(result)) > _ENVELOPE_LIMIT:
                    break
                events = candidate
            result["events"] = events
            return result
